import abc
import asyncio
import datetime
import json
import logging
import signal
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from hashlib import sha1
from textwrap import shorten
from typing import Any, Callable, Coroutine, Deque, Dict, List, Literal, Optional, Sequence, Tuple, Union

import dateutil.tz
from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, field_serializer, field_validator

MAX_REPR_LEN = 60


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


class Record(BaseModel):
    '''Data entry, passed around from Monitors to Actions through Filters'''

    model_config = ConfigDict(use_attribute_docstrings=True)

    origin: Optional[str] = Field(default=None, exclude=True)
    """semicolon-separated names of actor and entity record originated from"""
    chain: str = Field(default='', exclude=True)
    """name of the Chain this record is going through.
    Empty string means it was just produced and should go to every subscriber"""
    created_at: datetime.datetime = Field(default_factory=utcnow, exclude=True)
    """record creation timestamp"""
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

    def as_timezone(self, timezone: Optional[datetime.tzinfo] = None) -> 'Record':
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

    def as_embed(self) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        embed_title_max_length = 256
        embed_description_max_length = 4096

        text = str(self)
        if text.find('\n') > -1:
            title, description = text.split('\n', 1)
        else:
            title, description = '', text
        title = shorten(title, embed_title_max_length)
        description = shorten(description, embed_description_max_length)
        return {'title': title, 'description': description}

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


Subscription = Callable[[str, Record], None]
SubscriptionsMapping = Dict[str, List[Subscription]]


class MessageBus:
    PREFIX_IN = 'inputs'
    PREFIX_OUT = 'output'
    SEPARATOR = '/'

    HISTORY_SIZE = 20

    def __init__(self) -> None:
        self.subscriptions: SubscriptionsMapping = defaultdict(list)
        self.logger = logging.getLogger('bus')
        self.history: Dict[str, Deque[Record]] = defaultdict(lambda: deque(maxlen=self.HISTORY_SIZE))

    def sub(self, topic: str, callback: Subscription):
        self.logger.debug(f'subscription on topic {topic} by {callback!r}')
        self.subscriptions[topic].append(callback)

    def _generic_topic(self, specific_topic: str) -> str:
        direction, actor, entity, chain = self.split_subscription_topic(specific_topic)
        generic_topic = self.make_topic(direction, actor, entity, '')
        return generic_topic

    def pub(self, topic: str, message: Record):
        self.logger.debug(f'on topic {topic} message "{message!r}"')
        matching_callbacks = self.get_matching_callbacks(topic)
        for specific_topic, callbacks in matching_callbacks.items():
            if message.chain:
                targeted_message = message
            else:
                _, _, chain = self.split_message_topic(specific_topic)
                targeted_message = message.model_copy(deep=True)
                targeted_message.chain = chain
            for callback in callbacks:
                callback(specific_topic, targeted_message)

            generic_topic = self._generic_topic(specific_topic)
            self.add_to_history(generic_topic, targeted_message)
        if not matching_callbacks:
            # topic has no subscribers, meaning entity is not referenced in chains
            self.add_to_history(self._generic_topic(topic), message)

    def add_to_history(self, topic: str, message: Record):
        self.history[topic].append(message)

    def get_history(self, actor: str, entity: str, chain: str = '', direction: Literal['in', 'out'] = 'in') -> List[Record]:
        if direction == 'in':
            topic = self.incoming_topic_for(actor, entity, '')
        elif direction == 'out':
            topic = self.outgoing_topic_for(actor, entity, '')
        else:
            assert False, f'unexpected direction "{direction}"'
        if chain:
            records = [record for record in self.history[topic] if record.chain == chain]
        else:
            records = list(self.history[topic])
        records.sort(key = lambda record: record.created_at)
        return records

    def get_matching_callbacks(self, topic_pattern: str) -> SubscriptionsMapping:
        pattern_direction, pattern_actor, pattern_entity, pattern_chain = self.split_subscription_topic(topic_pattern)
        callbacks = defaultdict(list)
        for topic, callback in self.subscriptions.items():
            direction, actor, entity, chain = self.split_subscription_topic(topic)
            if pattern_direction != direction:
                continue
            if actor != pattern_actor:
                continue
            if entity != pattern_entity:
                continue
            if chain == '' or pattern_chain == '' or chain == pattern_chain:
                callbacks[topic].extend(callback)
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
        _, actor, entity, chain = self.split_subscription_topic(topic)
        return actor, entity, chain

    def split_subscription_topic(self, topic) -> Tuple[str, str, str, str]:
        try:
            direction, actor, entity, chain = self.split_topic(topic)
        except ValueError:
            self.logger.error(f'failed to split message topic "{topic}"')
            raise
        return direction, actor, entity, chain

    def clear_subscriptions(self):
        self.subscriptions.clear()


class TerminatedAction(int, Enum):
    EXIT = 0
    RESTART = 2


@dataclass
class TaskStatus:
    name: str
    status: str
    actor_name: Optional[str] = None
    entity_name: Optional[str] = None
    record: Optional[Record] = None

    @staticmethod
    def task_status(task: asyncio.Task) -> str:
        if task.done():
            if task.cancelled():
                return 'cancelled'
            elif task.exception() is not None:
                return f'failed: {task.exception()}'
            else:
                return 'done'
        else:
            return 'running'
    @classmethod
    def from_task(cls, task: asyncio.Task) -> 'TaskStatus':
        name = task.get_name()
        status = cls.task_status(task)
        actor_name = None
        entity_name = None
        record = None
        try:
            stack = task.get_stack(limit=1)
            args = stack[0].f_locals
            for arg_name, arg_value in args.items():
                if isinstance(arg_value, Actor):
                    actor_name = arg_value.conf.name
                elif isinstance(arg_value, ActorEntity):
                    entity_name = arg_value.name
                elif isinstance(arg_value, Record):
                    record = arg_value
        except Exception:
            pass
        return cls(name, status, actor_name, entity_name, record)

    def as_json(self) -> Dict[str, Optional[str]]:
        return {
            'name': self.name,
            'status': self.status,
            'actor': self.actor_name,
            'entity': self.entity_name,
            'record': str(self.record) if self.record else None
        }

class TasksController:
    class TerminatedError(KeyboardInterrupt):
        """Raised when application restart is requested"""

    def __init__(self, poll_interval: float = 5, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger('task_controller')
        self.poll_interval = poll_interval
        self.termination_pending = False
        self.termination_required = False
        self.terminated_action = TerminatedAction.EXIT
        self.tasks: set[asyncio.Task] = set()

    def create_task(self, coro: Coroutine, *, name: Optional[str] = None) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        if task in self.tasks:
            raise RuntimeError(f'newly created task {task} is already monitored')
        self.tasks.add(task)
        return task

    async def check_done_tasks(self, done: set[asyncio.Task]) -> None:
        for task in done:
            if not task.done():
                continue
            try:
                task_exception = task.exception()
                if task_exception is not None:
                    self.logger.error(f'task "{task.get_name()}" has terminated with exception', exc_info=task_exception)
            except asyncio.CancelledError:
                self.logger.debug(f'task "{task.get_name()}" cancelled')
            self.tasks.discard(task)

    async def monitor_tasks(self) -> None:
        while not self.termination_required:
            if not self.tasks:
                await asyncio.sleep(self.poll_interval)
                continue
            done, pending = await asyncio.wait(self.tasks, return_when=asyncio.FIRST_EXCEPTION, timeout=self.poll_interval)
            await self.check_done_tasks(done)
        self.termination_required = False
        raise self.TerminatedError()

    async def cancel_all_tasks(self) -> None:
        self.logger.debug(f'terminating {len(self.tasks)} tasks')
        while True:
            for task in self.tasks:
                task.cancel('terminating')
            done, pending = await asyncio.wait(self.tasks, return_when=asyncio.ALL_COMPLETED)
            await self.check_done_tasks(done)
            if not pending:
                break
            self.logger.debug(f'{len(pending)} more tasks left to terminate')
        self.logger.debug('all tasks terminated')

    async def terminate(self, delay: float, action: TerminatedAction):
        if self.termination_pending:
            self.logger.warning(f'active termination request is already pending')
            return
        self.termination_pending = True
        if delay > 0:
            self.logger.debug(f'terminating after {delay:.02f}')
            await asyncio.sleep(delay)
        self.logger.debug(f'terminating now')
        self.termination_pending = False
        self.terminated_action = action
        self.termination_required = True

    def terminate_after(self, delay: float, action: TerminatedAction):
        self.create_task(self.terminate(delay, action), name=f'terminate after {delay}')

    async def run_until_termination(self) -> TerminatedAction:
        try:
            await self.monitor_tasks()
        except self.TerminatedError:
            await self.cancel_all_tasks()
        except (asyncio.CancelledError, KeyboardInterrupt):
            await self.cancel_all_tasks()
            raise
        return self.terminated_action

    def get_status(self) -> List[TaskStatus]:
        return [TaskStatus.from_task(task) for task in self.tasks]


class RuntimeContext:
    def __init__(self, bus: MessageBus, controller: TasksController):
        self.bus: MessageBus = bus
        self.controller: TasksController = controller
        self.extra: Dict[str, Any] = {}
        self._sigint_handler = signal.getsignal(signal.SIGINT)
        self._sigterm_handler = signal.getsignal(signal.SIGINT)

    def set_extra(self, name: str, value: Any):
        self.extra[name] = value

    def get_extra(self, name: str) -> Optional[Any]:
        return self.extra.get(name)

    def _get_handler(self) -> Callable:
        controller = self.controller
        def handler(sig, frame):
            controller.terminate_after(0, TerminatedAction.EXIT)
        return handler

    def __enter__(self):
        signal.signal(signal.SIGINT, self._get_handler())
        signal.signal(signal.SIGTERM, self._get_handler())

    def __exit__(self, exc_type, exc_val, exc_tb):
        signal.signal(signal.SIGINT, self._sigint_handler)
        signal.signal(signal.SIGTERM, self._sigterm_handler)


    @classmethod
    def create(cls) -> 'RuntimeContext':
        bus = MessageBus()
        controller = TasksController()
        return cls(bus=bus, controller=controller)


class AbstractRecordsStorage(abc.ABC):
    """Interface for accessing persistent records storage from web ui"""

    @abstractmethod
    def page_count(self, per_page: int) -> int:
        """return total number of pages"""

    @abstractmethod
    def load_page(self, page: Optional[int], per_page: int, desc: bool = True) -> List[Record]:
        """return content of specific page as a list of Record instances"""


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
        '''Implementations might overwrite this method to give web interface access to persistent records storage'''
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


class Timezone:
    known: Dict[str, Any] = {}

    @classmethod
    def get_tz(cls, name: Optional[str]) -> Optional[datetime.tzinfo]:
        if name is None:
            return None
        tz = dateutil.tz.gettz(name)
        if tz is None:
            raise ValueError(f'Unknown timezone: {name}')
        cls.known[name] = tz
        return tz

    @classmethod
    def get_name(cls, tz: Optional[datetime.tzinfo]) -> Optional[str]:
        if tz is None:
            return None
        for name, timezone in cls.known.items():
            if tz == timezone:
                return name
        return tz.tzname(datetime.datetime.now())
