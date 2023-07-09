from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import logging
from typing import Callable, Dict, List, Sequence, Tuple
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

    def __init__(self) -> None:
        self.subscriptions: Dict[str, List[Callable[[str, Record], None]]] = defaultdict(list)

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

    def __init__(self, bus: MessageBus, conf: MonitorConfig, entities: Sequence[MonitorEntity]):
        self.conf = conf
        self.bus = bus
        self.entities = {entity.name: entity for entity in entities}

    def on_record(self, entity_name: str, record: Record):
        '''Implementation should call it for every new Record'''
        topic = self.bus.message_topic_for(self.conf.name, entity_name)
        self.bus.pub(topic, record)

    def __repr__(self):
        return f'{self.__class__.__name__}({self.entities!r})'


@dataclass
class ActionConfig:
    name: str

@dataclass
class ActionEntity:
    name: str

class Action(RunnableMixin, ABC):

    def __init__(self, bus: MessageBus, conf: ActionConfig, entities: Sequence[ActionEntity]):
        self.conf = conf
        self.bus = bus
        self.entities = {entity.name: entity for entity in entities}

        for entity_name in self.entities:
            topic = self.bus.message_topic_for(self.conf.name, entity_name)
            self.bus.sub(topic, self._handle)

    def _handle(self, topic: str, record: Record):
        actor, entity = self.bus.split_message_topic(topic)
        if actor == self.conf.name:
            pass
        self.handle(entity, record)

    @abstractmethod
    def handle(self, entity_name: str, record: Record):
        '''Perform action on record if entity in self.entities'''

    def on_event(self, event: Event, entity_name: str, record: Record):
        '''Implementation should call it to publish event'''
        topic = self.bus.event_topic_for(event.value, self.conf.name, entity_name)
        self.bus.pub(topic, record)

    def __repr__(self):
        return f'{self.__class__.__name__}({self.entities!r})'


class Filter:

    @abstractmethod
    def match(self, record):
        '''Take record and return it if it matches some condition
        or otherwise process it, else return None'''

