import datetime
import logging
from abc import ABC, abstractmethod
from textwrap import shorten
from typing import Any, Dict, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from avtdl.core.interfaces import AbstractRecordsStorage, Event, MAX_REPR_LEN, Record
from avtdl.core.runtime import RuntimeContext
from avtdl.core.utils import Timezone


class ActorConfig(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)

    name: str
    defaults: Dict[str, Any] = Field(default={}, exclude=True)


class ActorEntity(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)

    name: str
    """name of a specific entity. Used to reference it in `chains` section. Must be unique within a plugin"""
    reset_origin: bool = False
    """treat throughput records as if they have originated from this entity, 
    emitting them in every Chain this entity is used"""


class Actor(ABC):
    model_config = ConfigDict(use_attribute_docstrings=True)

    def __init__(self, conf: ActorConfig, entities: Sequence[ActorEntity], ctx: RuntimeContext):
        self.conf = conf
        self.logger = logging.getLogger(f'actor').getChild(conf.name)
        self.ctx = ctx
        self.bus = ctx.bus
        self.controller = ctx.controller
        self.entities = {entity.name: entity for entity in entities}

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

    def get_records_storage(self, entity_name: Optional[str] = None) -> Optional[AbstractRecordsStorage]:
        '''
        Implementations might overwrite this method to give web interface access to persistent records storage
        If implementation uses a single storage for all entities, it must return it with entity_name=None,
        and return None when entity_name provided. If each entity uses individual storage, it must be returned
        when entity_name is specified, and for entity_name=None the return value should be None
        '''
        return None

    @abstractmethod
    def handle_record(self, entity: ActorEntity, record: Record) -> None:
        '''Perform an action on record if entity in self.entities'''

    def on_record(self, entity: ActorEntity, record: Record):
        '''Implementation should call it for every new Record it produces'''
        origin = f'{self.conf.name}:{entity.name}'
        if record.origin == origin:
            self.logger.warning(f'[{entity.name}] received incoming record produced by self, which indicates loop in a chain, dropping: "{record!r}".\nCheck config for chains, passing records to each other in a loop. Record has chain set to "{record.chain}"')
            return
        if record.origin is None:
            record.origin = origin
        if entity.reset_origin:
            record = record.model_copy(deep=True)
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

    def __init__(self, conf: ActorConfig, entities: Sequence[MonitorEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)

    def handle_record(self, entity: MonitorEntity, record: Record) -> None:
        # For convenience’s sake monitors pass incoming records down the chain.
        # It allows using multiple monitors in a single chain, one after another
        self.on_record(entity, record)

    def on_record(self, entity: ActorEntity, record: Record):
        '''Implementation should call it for every new Record it produces'''
        super().on_record(entity, record)


class FilterEntity(ActorEntity):
    pass


class Filter(Actor):

    def __init__(self, conf: ActorConfig, entities: Sequence[FilterEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
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
    event_passthrough: bool = False
    """whether events should be treated as regular records. When enabled, events are passed down the chain without processing, unless `consume_record` is also enabled"""
    timezone: Optional[datetime.tzinfo] = None
    """takes timezone name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones> (or local time if omitted), converts record fields containing date and time to this timezone"""

    @field_validator('timezone', mode='plain')
    @classmethod
    def check_timezone(cls, timezone: Optional[str]) -> Optional[datetime.tzinfo]:
        if timezone is None:
            return None
        if not isinstance(timezone, str):
            raise ValueError('Input should be a valid string')
        return Timezone.get_tz(timezone)

    @field_serializer('timezone')
    @classmethod
    def serialize_timezone(cls, timezone: Optional[datetime.tzinfo]) -> Optional[str]:
        return Timezone.get_name(timezone)


class Action(Actor, ABC):

    def __init__(self, conf: ActorConfig, entities: Sequence[ActionEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)

    def handle_record(self, entity: ActionEntity, record: Record) -> None:
        record = record.as_timezone(entity.timezone)
        if not entity.event_passthrough or not isinstance(record, Event):
            self.handle(entity, record)
        if not entity.consume_record:
            self.on_record(entity, record)

    @abstractmethod
    def handle(self, entity: ActionEntity, record: Record):
        '''Method for implementation to process incoming record'''
