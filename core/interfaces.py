from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass
from enum import Enum
import logging
from typing import Callable, Dict, List, Sequence, Tuple, Type
from collections import defaultdict


@dataclass
class Record:
    '''Data entry, passed around from Monitors to Actions through Filters'''
    title: str
    url: str

    def __str__(self):
        return f'{self.title} ({self.url})'

class RunnableMixin(ABC):

    @abstractmethod
    async def run(self):
        '''Will be runned as asyncio task once everything set up'''
        return


class Event(Enum):
    start: str = 'start'
    end: str = 'end'
    error: str = 'error'

class MessageBus:
    MSG_PREFIX = 'record'
    EVENT_PREFIX = 'event'
    SEPARATOR = '/'

    _subscriptions: Dict[str, List[Callable[[str, Record], None]]] = defaultdict(list)

    def __init__(self):
        self.subscriptions = self._subscriptions

    def sub(self, topic: str, callback: Callable[[str, Record], None]):
        logging.debug(f'[bus] subscription on topic {topic} by {callback!r}')
        self.subscriptions[topic].append(callback)

    def pub(self, topic: str, message: Record):
        logging.debug(f'[bus] on topic {topic} message "{message}"')
        for cb in self.subscriptions[topic]:
            cb(topic, message)

    def make_topic(self, *args: str):
        return self.SEPARATOR.join(args)

    def split_topic(self, topic: str):
        return topic.split(self.SEPARATOR)

    def message_topic_for(self, actor: str, entity: str) -> str:
        return self.make_topic(self.MSG_PREFIX, actor, entity)

    def split_message_topic(self, topic) -> Tuple[str, str]:
        try:
            _, actor, entity = self.split_topic(topic)
        except ValueError:
            logging.error(f'failed to split message topic "{topic}"')
            raise
        return actor, entity

    def event_topic_for(self, event: str, actor: str, entity: str) -> str:
        return self.make_topic(self.EVENT_PREFIX, event, actor, entity)

    def split_event_topic(self, topic) -> Tuple[str, str, str]:
        try:
            _, event_type, action, entity = self.split_topic(topic)
        except ValueError:
            logging.error(f'failed to split event topic "{topic}"')
            raise
        return event_type, action, entity


@dataclass
class MonitorConfig:
    name: str

@dataclass
class MonitorEntity:
    name: str

class Monitor(RunnableMixin, ABC):

    def __init__(self, conf: MonitorConfig, entities: Sequence[MonitorEntity]):
        self.conf = conf
        self.bus = MessageBus()
        self.entities = {entity.name: entity for entity in entities}

    def on_record(self, entity_name: str, record: Record):
        '''Implementation should call it for every new Record'''
        topic = self.bus.message_topic_for(self.conf.name, entity_name)
        self.bus.pub(topic, record)

    def __repr__(self):
        return f'{self.__class__.__name__}({[entity for entity in self.entities]})'


@dataclass
class TaskMonitorEntity(MonitorEntity):
    def __init__(self, name: str, update_interval: int):
        super().__init__(name)
        self.update_interval = update_interval

class TaskMonitor(Monitor):

    def __init__(self, conf: MonitorConfig, entities: Sequence[TaskMonitorEntity]):
        super().__init__(conf, entities)
        self.tasks: Dict[str, asyncio.Task] = {}

    async def run(self):
        for name, entity in self.entities.items():
            self.tasks[name] = asyncio.create_task(self.run_for(entity), name=f'{self.conf.name}:{entity.name}')
        await asyncio.Future()

    async def run_for(self, entity: TaskMonitorEntity):
        while True:
            try:
                records = await self.get_new_records(entity)
            except Exception:
                logging.exception(f'{self.conf.name}: task for entity {entity} failed')
                break
            for record in records:
                self.on_record(entity.name, record)
            await asyncio.sleep(entity.update_interval)

    @abstractmethod
    async def get_new_records(self, entity: TaskMonitorEntity) -> Sequence[Record]:
        '''Produce new records, optionally adjust update_interval'''


@dataclass
class ActionConfig:
    name: str

@dataclass
class ActionEntity:
    name: str

class Action(RunnableMixin, ABC):

    supported_record_types: List[Type] = [Record]

    def __init__(self, conf: ActionConfig, entities: Sequence[ActionEntity]):
        self.conf = conf
        self.bus = MessageBus()
        self.entities = {entity.name: entity for entity in entities}

        for entity_name in self.entities:
            topic = self.bus.message_topic_for(self.conf.name, entity_name)
            self.bus.sub(topic, self._handle)

    def _handle(self, topic: str, record: Record):
        actor, entity = self.bus.split_message_topic(topic)
        if actor == self.conf.name:
            logging.debug(f'{self.conf.name}: on topic {topic} ignored record produced by self: {record}')
        for record_type in self.supported_record_types:
            if isinstance(record, record_type):
                break
        else:
            logging.debug(f'{self.conf.name}: ignoring record with unsupported type "{record.__class__.__name__}": {record}')
            return
        self.handle(entity, record)

    @abstractmethod
    def handle(self, entity_name: str, record: Record):
        '''Perform action on record if entity in self.entities'''

    def on_event(self, event: Event, entity_name: str, record: Record):
        '''Implementation should call it to publish event'''
        topic = self.bus.event_topic_for(event.value, self.conf.name, entity_name)
        self.bus.pub(topic, record)

    def __repr__(self):
        return f'{self.__class__.__name__}({[entity for entity in self.entities]})'


class Filter:

    @abstractmethod
    def match(self, record):
        '''Take record and return it if it matches some condition
        or otherwise process it, else return None'''

