import asyncio
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple, Type, Optional

import aiohttp
from pydantic import BaseModel

from core import utils


class Record(BaseModel):
    '''Data entry, passed around from Monitors to Actions through Filters'''
    title: str
    url: str

    def __str__(self):
        text = self.title.strip()
        return f'{text} ({self.url})'

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
        self.logger.debug(f'[bus] subscription on topic {topic} by {callback!r}')
        self.subscriptions[topic].append(callback)

    def pub(self, topic: str, message: Record):
        self.logger.debug(f'[bus] on topic {topic} message "{message}"')
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
        self.bus = MessageBus()
        self.entities = {entity.name: entity for entity in entities}

        for entity_name in self.entities:
            topic = self.bus.incoming_topic_for(self.conf.name, entity_name)
            self.bus.sub(topic, self._handle)

    def _handle(self, topic: str, record: Record) -> None:
        _, entity = self.bus.split_message_topic(topic)
        for record_type in self.supported_record_types:
            if isinstance(record, record_type):
                break
        else:
            logging.debug(
                f'{self.conf.name}: forwarding record with unsupported type "{record.__class__.__name__}" down the chain: {record}')
            self.on_record(entity, record)
        self.handle(entity, record)

    @abstractmethod
    def handle(self, entity_name: str, record: Record) -> None:
        '''Perform action on record if entity in self.entities'''

    def on_record(self, entity_name: str, record: Record):
        '''Implementation should call it for every new Record it produces'''
        topic = self.bus.outgoing_topic_for(self.conf.name, entity_name)
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

    def handle(self, entity_name: str, record: Record) -> None:
        logging.warning(f'TaskMonitor({self.conf.name}, {entity_name}) got Record despite not expecting any, might be sign of possible misconfiguration. Record: {record}')

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
        names = ', '.join([entity.name for entity in entities])
        logging.debug(f'[start_tasks_for] will start {len(entities)} tasks separated by {interval} seconds for {names}')
        for entity in entities:
            logging.debug(f'[start_tasks_for] starting task {entity.name} with {entity.update_interval} update interval')
            self.tasks[entity.name] = asyncio.create_task(self.run_for(entity), name=f'{self.conf.name}:{entity.name}')
            await asyncio.sleep(interval)
        logging.debug(f'[start_tasks_for] done with tasks set with {interval} interval')

    @abstractmethod
    async def run_for(self, entity: TaskMonitorEntity):
        '''Task for specific entity that should check for new records based on update_interval and call self.on_record() for each'''


class TaskMonitor(BaseTaskMonitor):

    async def run_for(self, entity: TaskMonitorEntity):
        while True:
            try:
                await self.run_once(entity)
            except Exception:
                logging.exception(f'{self.conf.name}: task for entity {entity} failed, terminating')
                break
            await asyncio.sleep(entity.update_interval)

    async def run_once(self, entity: TaskMonitorEntity):
        records = await self.get_new_records(entity)
        for record in records:
            self.on_record(entity.name, record)

    @abstractmethod
    async def get_new_records(self, entity: TaskMonitorEntity) -> Sequence[Record]:
        '''Produce new records, optionally adjust update_interval'''

class HttpTaskMonitorEntity(TaskMonitorEntity):
    cookies_file: Optional[Path] = None

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
                    logging.exception(f'{self.conf.name}: task for entity {entity} failed, terminating')
                    break
                await asyncio.sleep(entity.update_interval)

    async def run_once(self, entity: TaskMonitorEntity, session: aiohttp.ClientSession):
        records = await self.get_new_records(entity, session)
        for record in records:
            self.on_record(entity.name, record)

    @abstractmethod
    async def get_new_records(self, entity: TaskMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        '''Produce new records, optionally adjust update_interval'''



class FilterEntity(ActorEntity):
    pass

class Filter(Actor):

    def __init__(self, conf: ActorConfig, entities: Sequence[FilterEntity]):
        super().__init__(conf, entities)

    def handle(self, entity_name: str, record: Record):
        filtered = self.match(entity_name, record)
        if filtered is not None:
            self.on_record(entity_name, record)
        else:
            logging.debug(f'filter {self.conf.name}: record "{record}" dropped on filter {self.entities[entity_name]}')

    @abstractmethod
    def match(self, entity_name: str, record: Record) -> Optional[Record]:
        '''Take record and return it if it matches some condition
        or otherwise process it, else return None'''

