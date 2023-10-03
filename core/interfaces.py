import datetime
import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from hashlib import sha1
from textwrap import shorten
from typing import Callable, Dict, List, Sequence, Tuple, Type, Optional

from pydantic import BaseModel

MAX_REPR_LEN = 60

class Record(BaseModel):
    '''Data entry, passed around from Monitors to Actions through Filters'''

    @abstractmethod
    def __str__(self) -> str:
        '''Text representation of the record to be sent in message, written to file etc.'''

    @abstractmethod
    def __repr__(self) -> str:
        '''Short text representation of the record to be printed in logs'''

    def as_timezone(self, timezone: Optional[datetime.timezone]=None) -> 'Record':
        if timezone is None:
            return self
        fields = self.model_dump()
        for k, v in fields.items():
            if isinstance(v, datetime.datetime):
                fields[k] = v.astimezone(timezone)
        return self.model_validate(fields)

    def as_json(self, indent=None) -> str:
        return json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False, default=str, indent=indent)

    def hash(self) -> str:
        record_hash = sha1(self.as_json().encode())
        return record_hash.hexdigest()


class TextRecord(Record):

    text: str

    def __str__(self):
        return self.text

    def __repr__(self):
        return f'TextRecord("{shorten(self.text, MAX_REPR_LEN)}")'


class LivestreamRecord(Record, ABC):
    '''Record that has a downloadable url'''
    url: str

class EventType:
    generic: str = 'generic'
    error: str = 'error'
    started: str = 'started'
    finished: str = 'finished'

class Event(Record):

    event_type: str = EventType.generic
    text: str

    def __str__(self):
        return self.text

    def __repr__(self):
        text = shorten(self.text, MAX_REPR_LEN)
        return f'Event(event_type="{self.event_type}", text="{text}")'

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
        self.logger.debug(f'on topic {topic} message "{message!r}"')
        for cb in self.subscriptions[topic]:
            cb(topic, message)

    def get_subscribed(self) -> Dict[str, List[str]]:
        '''return list of pairs [actor, entity] present in subscriptions'''
        subscribed: Dict[str, List[str]] = defaultdict(list)
        for topic in self._subscriptions.keys():
            actor, entity = self.split_message_topic(topic)
            subscribed[actor].append(entity)
        return subscribed

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
            logging.warning(f'received record on topic {topic}, but have no entity with name {entity_name} configured, dropping record {record!r}')
            return
        entity = self.entities[entity_name]
        for record_type in self.supported_record_types:
            if isinstance(record, record_type):
                break
        else:
            self.logger.debug(f'forwarding record with unsupported type "{record.__class__.__name__}" down the chain: {record!r}')
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

