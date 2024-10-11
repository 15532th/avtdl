import datetime
import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from hashlib import sha1
from textwrap import shorten
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

import dateutil.tz
from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, field_validator

MAX_REPR_LEN = 60


class Record(BaseModel):
    '''Data entry, passed around from Monitors to Actions through Filters'''

    model_config = ConfigDict(use_attribute_docstrings=True)

    origin: Optional[str] = Field(default=None, exclude=True)
    """semicolon-separated names of actor and entity record originated from"""
    chain: str = Field(default='', exclude=True)
    """name of the Chain this record is going through.
    Empty string means it was just produced and should go to every subscriber"""

    @abstractmethod
    def __str__(self) -> str:
        '''Text representation of the record to be sent in a message, written to a file etc.'''

    @abstractmethod
    def __repr__(self) -> str:
        '''Short text representation of the record to be printed in logs'''

    def __eq__(self, other) -> bool:
        if not isinstance(other, Record):
            return NotImplemented
        return self.model_dump() == other.model_dump()

    def get_uid(self) -> str:
        '''A string that is the same for different versions of the same record'''
        return self.hash()

    def as_timezone(self, timezone: Optional[datetime.timezone] = None) -> 'Record':
        fields = dict(self)
        for k, v in fields.items():
            if isinstance(v, Record):
                fields[k] = v.as_timezone(timezone)
            if isinstance(v, datetime.datetime):
                fields[k] = v.astimezone(timezone)
        record_copy = self.model_validate(fields)
        return record_copy

    def as_json(self, indent: Union[int, str, None] = None) -> str:
        return json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=False, default=str, indent=indent)

    def hash(self) -> str:
        record_hash = sha1(self.as_json().encode())
        return record_hash.hexdigest()


class TextRecord(Record):
    """
    Simplest record, containing only a single text field
    """

    text: str
    """content of the record"""

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f'TextRecord("{shorten(self.text, MAX_REPR_LEN)}")'


class EventType:
    generic: str = 'generic'
    error: str = 'error'
    started: str = 'started'
    finished: str = 'finished'


class Event(Record):
    """
    Record produced by an internal event (usually error) inside the plugin
    """

    event_type: str = EventType.generic
    """text describing the nature of event, can be used to filter classes of events, such as errors"""
    text: str
    """text describing specific even details"""
    record: SerializeAsAny[Optional[Record]] = Field(exclude=True, default=None)
    """record that was being processed when this event happened"""

    def __str__(self):
        return self.text

    def __repr__(self):
        text = shorten(self.text, MAX_REPR_LEN)
        return f'Event(event_type="{self.event_type}", text="{text}")'

    def model_post_init(self, __context):
        if self.record is not None:
            self.origin = self.record.origin
            self.chain = self.record.chain


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
        if message.chain:
            matching_callbacks = self.get_matching_callbacks(topic)
            for generic_topic, callbacks in matching_callbacks.items():
                for callback in callbacks:
                    callback(topic, message)
        else:
            matching_callbacks = self.get_matching_callbacks(topic)
            for specific_topic, callbacks in matching_callbacks.items():
                _, _, chain = self.split_message_topic(specific_topic)
                targeted_message = message.model_copy(deep=True)
                targeted_message.chain = chain
                for callback in callbacks:
                    callback(specific_topic, targeted_message)

    def get_matching_callbacks(self, topic_pattern: str) -> Dict[str, List[Callable[[str, Record], None]]]:
        callbacks = defaultdict(list)
        for topic, callback in self.subscriptions.items():
            if topic.startswith(topic_pattern) or topic_pattern.startswith(topic):
                callbacks[topic].extend(self.subscriptions[topic])
        return callbacks

    def make_topic(self, *args: str):
        return self.SEPARATOR.join(args)

    def split_topic(self, topic: str):
        return topic.split(self.SEPARATOR)

    def incoming_topic_for(self, actor: str, entity: str, chain: str = '') -> str:
        return self.make_topic(self.PREFIX_IN, actor, entity, chain)

    def outgoing_topic_for(self, actor: str, entity: str, chain: str = '') -> str:
        return self.make_topic(self.PREFIX_OUT, actor, entity, chain)

    def split_message_topic(self, topic) -> Tuple[str, str, str]:
        try:
            _, actor, entity, chain = self.split_topic(topic)
        except ValueError:
            self.logger.error(f'failed to split message topic "{topic}"')
            raise
        return actor, entity, chain


class ActorConfig(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)
    name: str


class ActorEntity(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)

    name: str
    """name of a specific entity. Used to reference it in `chains` section. Must be unique within a plugin"""
    reset_origin: bool = False
    """treat throughput records as if they have originated from this entity, 
    emitting them in every Chain this entity is used"""


class Actor(ABC):
    model_config = ConfigDict(use_attribute_docstrings=True)

    def __init__(self, conf: ActorConfig, entities: Sequence[ActorEntity]):
        self.conf = conf
        self.logger = logging.getLogger(f'actor').getChild(conf.name)
        self.bus = MessageBus()
        self.entities: Dict[str, ActorEntity] = {entity.name: entity for entity in entities}

        for entity_name in self.entities:
            topic = self.bus.incoming_topic_for(self.conf.name, entity_name)
            self.bus.sub(topic, self._handle)

    def __repr__(self):
        text = f'{self.__class__.__name__}({list(self.entities)})'
        return shorten(text, MAX_REPR_LEN)

    def _handle(self, topic: str, record: Record) -> None:
        _, entity_name, _ = self.bus.split_message_topic(topic)
        if entity_name not in self.entities:
            logging.warning(f'received record on topic {topic}, but have no entity with name {entity_name} configured, dropping record {record!r}')
            return
        entity = self.entities[entity_name]
        try:
            self.handle_record(entity, record)
        except Exception:
            self.logger.exception(f'{self.conf.name}.{entity_name}: error while processing record "{record!r}"')

    @abstractmethod
    def handle_record(self, entity: ActorEntity, record: Record) -> None:
        '''Perform an action on record if entity in self.entities'''

    def on_record(self, entity: ActorEntity, record: Record):
        '''Implementation should call it for every new Record it produces'''
        if entity.reset_origin:
            record.chain = ''
        topic = self.bus.outgoing_topic_for(self.conf.name, entity.name, record.chain)
        self.bus.pub(topic, record)

    async def run(self):
        '''Will be run as asyncio task once everything is set up'''
        return


class MonitorEntity(ActorEntity):
    reset_origin: bool = Field(default=False, exclude=True)
    """excluded for Monitors because no good usecases"""


class Monitor(Actor, ABC):

    def __init__(self, conf: ActorConfig, entities: Sequence[MonitorEntity]):
        super().__init__(conf, entities)

    def handle_record(self, entity: MonitorEntity, record: Record) -> None:
        # For convenience’s sake monitors pass incoming records down the chain.
        # It allows using multiple monitors in a single chain, one after another
        self.on_record(entity, record)

    def on_record(self, entity: ActorEntity, record: Record):
        '''Implementation should call it for every new Record it produces'''
        origin = f'{self.conf.name}:{entity.name}'
        if record.origin == origin:
            self.logger.warning(f'[{entity.name}] received incoming record produced by self, which indicates loop in a chain, dropping: "{record!r}".\nCheck config for chains, passing records to each other in a loop.')
            return
        if record.origin is None:
            record.origin = origin
        super().on_record(entity, record)


class FilterEntity(ActorEntity):
    pass


class Filter(Actor):

    def __init__(self, conf: ActorConfig, entities: Sequence[FilterEntity]):
        super().__init__(conf, entities)
        self.logger = logging.getLogger(f'filters.{self.conf.name}')

    def handle_record(self, entity: FilterEntity, record: Record):
        filtered = self.match(entity, record)
        if filtered is not None:
            filtered.origin = filtered.origin or record.origin
            if filtered.chain is None:
                filtered.chain = ''
            elif not filtered.chain:
                filtered.chain = record.chain
            self.on_record(entity, filtered)
        else:
            self.logger.debug(f'[{entity.name}] record dropped: "{record!r}"')

    @abstractmethod
    def match(self, entity: FilterEntity, record: Record) -> Optional[Record]:
        '''
        Take a record and return it or a new/updated record
        if it matches some condition, otherwise return None.

        If returned record does not have "origin" field, it
        is copied from the original one. "chain" field is
        also copied if not set unless it is set to None, in
        which case it is set to empty string, making record
        propagate in all chains the filter is registered in.
        '''


class ActionEntity(ActorEntity):
    consume_record: bool = True
    """whether record should be consumed or passed down the chain after processing. Disabling it allows chaining multiple Actions"""
    timezone: Optional[str] = None
    """takes timezone name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones> (or local time if omitted), converts record fields containing date and time to this timezone"""

    @field_validator('timezone')
    @classmethod
    def check_timezone(cls, timezone: Optional[str]) -> Optional[datetime.timezone]:
        if timezone is None:
            return None
        tz = dateutil.tz.gettz(timezone)
        if tz is None:
            raise ValueError(f'Unknown timezone: {timezone}')
        return tz


class Action(Actor, ABC):

    def __init__(self, conf: ActorConfig, entities: Sequence[ActionEntity]):
        super().__init__(conf, entities)

    def handle_record(self, entity: ActionEntity, record: Record) -> None:
        record = record.as_timezone(entity.timezone)
        self.handle(entity, record)
        if not entity.consume_record:
            self.on_record(entity, record)

    @abstractmethod
    def handle(self, entity: ActionEntity, record: Record):
        '''Method for implementation to process incoming record'''
