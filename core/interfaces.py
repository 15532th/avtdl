import asyncio
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Callable, Dict, List, Sequence, Tuple, Type, Optional

import aiohttp
from pydantic import BaseModel, FilePath

from core import utils


class Record(BaseModel):
    '''Data entry, passed around from Monitors to Actions through Filters'''
    title: str
    url: str

    def __str__(self):
        text = self.title.strip()
        return f'{text} ({self.url})'

    def format_record(self, timezone=None):
        '''If implementation contains datetime objects it should overwrite this
        method to allow representation in specific user-defined timezone.
        If timezone is None, record should be formatted in local time.

        Client code that wants to present Records in specific timezone should
        call this method instead of str()'''
        return self.__str__()

class TextRecord(Record):
    def __str__(self):
        return self.title

class EventType:
    generic: str = 'generic'
    error: str = 'error'
    started: str = 'started'
    finished: str = 'finished'
class Event(Record):
    event_type: str = EventType.generic

    def __str__(self):
        return self.title
class MessageBus:
    PREFIX_IN = 'inputs'
    PREFIX_OUT = 'output'
    SEPARATOR = '/'

    _subscriptions: Dict[str, List[Callable[[str, Record], None]]] = defaultdict(list)

    def __init__(self):
        self.subscriptions = self._subscriptions
        self.logger = logging.getLogger('bus')

    def sub(self, topic: str, callback: Callable[[str, Record], None]):
        self.logger.debug(f'subscription on topic {topic} by {callback!r}')
        self.subscriptions[topic].append(callback)

    def pub(self, topic: str, message: Record):
        self.logger.debug(f'on topic {topic} message "{message}"')
        for cb in self.subscriptions[topic]:
            cb(topic, message)

    def make_topic(self, *args: str):
        return self.SEPARATOR.join(args)

    def split_topic(self, topic: str):
        return topic.split(self.SEPARATOR)

    def incoming_topic_for(self, actor: str, entity: str) -> str:
        return self.make_topic(self.PREFIX_IN, actor, entity)

    def outgoing_topic_for(self, actor: str, entity: str) -> str:
        return self.make_topic(self.PREFIX_OUT, actor, entity)

    def split_message_topic(self, topic) -> Tuple[str, str]:
        try:
            _, actor, entity = self.split_topic(topic)
        except ValueError:
            self.logger.error(f'failed to split message topic "{topic}"')
            raise
        return actor, entity

    def split_event_topic(self, topic) -> Tuple[str, str, str]:
        try:
            _, event_type, action, entity = self.split_topic(topic)
        except ValueError:
            self.logger.error(f'failed to split event topic "{topic}"')
            raise
        return event_type, action, entity


class ActorConfig(BaseModel):
    name: str

class ActorEntity(BaseModel):
    name: str

class Actor(ABC):

    supported_record_types: List[Type] = [Record]

    def __init__(self, conf: ActorConfig, entities: Sequence[ActorEntity]):
        self.conf = conf
        self.logger = logging.getLogger(f'actor.{conf.name}')
        self.bus = MessageBus()
        self.entities: Dict[str, ActorEntity] = {entity.name: entity for entity in entities}

        for entity_name in self.entities:
            topic = self.bus.incoming_topic_for(self.conf.name, entity_name)
            self.bus.sub(topic, self._handle)

    def _handle(self, topic: str, record: Record) -> None:
        _, entity_name = self.bus.split_message_topic(topic)
        if not entity_name in self.entities:
            logging.warning(f'received record on topic {topic}, but have no entity with name {entity_name} configured, dropping record {record}')
            return
        entity = self.entities[entity_name]
        for record_type in self.supported_record_types:
            if isinstance(record, record_type):
                break
        else:
            self.logger.debug(f'forwarding record with unsupported type "{record.__class__.__name__}" down the chain: {record}')
            self.on_record(entity, record)
        try:
            self.handle(entity, record)
        except Exception:
            self.logger.exception(f'{self.conf.name}.{entity_name}: error while processing record "{record!r}"')

    @abstractmethod
    def handle(self, entity: ActorEntity, record: Record) -> None:
        '''Perform action on record if entity in self.entities'''

    def on_record(self, entity: ActorEntity, record: Record):
        '''Implementation should call it for every new Record it produces'''
        topic = self.bus.outgoing_topic_for(self.conf.name, entity.name)
        self.bus.pub(topic, record)

    def __repr__(self):
        return f'{self.__class__.__name__}({list(self.entities)})'

    async def run(self):
        '''Will be run as asyncio task once everything is set up'''
        return


class TaskMonitorEntity(ActorEntity):
    update_interval: int

class BaseTaskMonitor(Actor):

    def __init__(self, conf: ActorConfig, entities: Sequence[TaskMonitorEntity]):
        super().__init__(conf, entities)
        self.tasks: Dict[str, asyncio.Task] = {}

    def handle(self, entity: ActorEntity, record: Record) -> None:
        self.logger.warning(f'TaskMonitor({self.conf.name}, {entity.name}) got Record despite not expecting any, might be sign of possible misconfiguration. Record: {record}')

    async def run(self):
        # start cyclic tasks
        await self.start_cyclic_tasks()
        # and wait forever
        await asyncio.Future()

    async def start_cyclic_tasks(self):
        by_entity_interval = defaultdict(list)
        for entity in self.entities.values():
            by_entity_interval[entity.update_interval].append(entity)
        by_group_interval = {interval / len(entities): entities for interval, entities in by_entity_interval.items()}
        for interval in sorted(by_group_interval.keys()):
            entities = by_group_interval[interval]
            asyncio.create_task(self.start_tasks_for(entities, interval))

    async def start_tasks_for(self, entities, interval):
        logger = self.logger.getChild('scheduler')
        if len(entities) == 0:
            logger.debug(f'called with no entities and {interval} interval')
            return
        names = ', '.join([f'{self.conf.name}.{entity.name}' for entity in entities])
        logger.info(f'will start {len(entities)} tasks with {entities[0].update_interval} update interval and {interval} offset for {names}')
        for entity in entities:
            logger.debug(f'starting task {entity.name} with {entity.update_interval} update interval')
            self.tasks[entity.name] = asyncio.create_task(self.run_for(entity), name=f'{self.conf.name}:{entity.name}')
            if entity == entities[-1]: # skip sleeping after last
                continue
            await asyncio.sleep(interval)
        logger.info(f'done starting tasks for {names}')

    @abstractmethod
    async def run_for(self, entity: TaskMonitorEntity):
        '''Task for specific entity that should check for new records based on update_interval and call self.on_record() for each'''


class TaskMonitor(BaseTaskMonitor):

    async def run_for(self, entity: TaskMonitorEntity):
        while True:
            try:
                await self.run_once(entity)
            except Exception:
                self.logger.exception(f'{self.conf.name}: task for entity {entity} failed, terminating')
                break
            await asyncio.sleep(entity.update_interval)

    async def run_once(self, entity: TaskMonitorEntity):
        records = await self.get_new_records(entity)
        for record in records:
            self.on_record(entity, record)

    @abstractmethod
    async def get_new_records(self, entity: TaskMonitorEntity) -> Sequence[Record]:
        '''Produce new records, optionally adjust update_interval'''

class HttpTaskMonitorEntity(TaskMonitorEntity):
    cookies_file: Optional[FilePath] = None

class HttpTaskMonitor(BaseTaskMonitor):
    '''Maintain and provide for records update tasks aiohttp.ClientSession objects
    grouped by HttpTaskMonitorEntity.cookies_path, which means entities that use
    the same cookies file will share session'''

    def __init__(self, conf: ActorConfig, entities: Sequence[HttpTaskMonitorEntity]):
        self.sessions: Dict[str, aiohttp.ClientSession] = {}
        super().__init__(conf, entities)

    def _get_session(self, entity: HttpTaskMonitorEntity) -> aiohttp.ClientSession:
        session_id = str(entity.cookies_file)
        session = self.sessions.get(session_id)
        if session is None:
            cookies = utils.load_cookies(entity.cookies_file)
            session = aiohttp.ClientSession(cookies=cookies)
        return session

    async def run_for(self, entity: HttpTaskMonitorEntity):
        session = self._get_session(entity)
        async with session:
            while True:
                try:
                    await self.run_once(entity, session)
                except Exception:
                    self.logger.exception(f'{self.conf.name}: task for entity {entity} failed, terminating')
                    break
                await asyncio.sleep(entity.update_interval)

    async def run_once(self, entity: TaskMonitorEntity, session: aiohttp.ClientSession):
        records = await self.get_new_records(entity, session)
        for record in records:
            self.on_record(entity, record)

    @abstractmethod
    async def get_new_records(self, entity: TaskMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        '''Produce new records, optionally adjust update_interval'''



class FilterEntity(ActorEntity):
    pass

class Filter(Actor):

    def __init__(self, conf: ActorConfig, entities: Sequence[FilterEntity]):
        super().__init__(conf, entities)
        self.logger = logging.getLogger(f'filters.{self.conf.name}')

    def handle(self, entity: FilterEntity, record: Record):
        filtered = self.match(entity, record)
        if filtered is not None:
            self.on_record(entity, filtered)
        else:
            self.logger.debug(f'record "{record}" dropped on filter {entity}')

    @abstractmethod
    def match(self, entity: FilterEntity, record: Record) -> Optional[Record]:
        '''Take record and return it if it matches some condition
        or otherwise process it, else return None'''

