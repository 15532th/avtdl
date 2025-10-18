import asyncio
import logging
import signal
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Literal, Optional, Tuple

from pydantic import Field

from avtdl.core.interfaces import Record
from avtdl.core.state import StateSerializer
from avtdl.core.utils import DictRootModel

Subscription = Callable[[str, Record], None]
SubscriptionsMapping = Dict[str, List[Subscription]]

HISTORY_SIZE = 20


def deque_factory() -> deque:
    return deque(maxlen=HISTORY_SIZE)


class MessageHistory(DictRootModel):
    root: Dict[str, deque[Record]] = Field(default_factory=lambda: defaultdict(deque_factory))


class MessageBus:
    PREFIX_IN = 'inputs'
    PREFIX_OUT = 'output'
    SEPARATOR = '/'

    PERSISTENCE_FILE = Path('bus/history.dat')

    def __init__(self) -> None:
        self.subscriptions: SubscriptionsMapping = defaultdict(list)
        self.logger = logging.getLogger('bus')
        self.history: MessageHistory = MessageHistory()

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

    def dump_state(self, directory: Path):
        StateSerializer.dump(self.history, directory / self.PERSISTENCE_FILE)

    def apply_state(self, directory: Path):
        stored_history = StateSerializer.restore(MessageHistory, directory / self.PERSISTENCE_FILE)
        if stored_history is None:
            return
        for topic, stored_topic_history in stored_history.items():
            active_topic_history = self.history[topic]
            for record in reversed(stored_topic_history):
                if len(active_topic_history) >= (active_topic_history.maxlen or 100500):
                    break
                active_topic_history.appendleft(record)


class TerminatedAction(int, Enum):
    EXIT = 0
    RESTART = 2


@dataclass
class TaskStatus:
    actor: Optional[str]
    entity: Optional[str]
    status: str = ''
    record: Optional[Record] = None

    def set_status(self, status: str, record: Optional[Record] = ...):  # type: ignore
        self.status = status
        if record is not ...:
            self.record = record

    def clear(self):
        self.status = ''
        self.record = None

    def is_empty(self) -> bool:
        return not self.status and not self.record


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
        self._info: Dict[asyncio.Task, Optional[TaskStatus]] = {}

    def create_task(self, coro: Coroutine, *, name: Optional[str] = None,
                    _info: Optional[TaskStatus] = None) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        if task in self.tasks:
            raise RuntimeError(f'newly created task {task} is already monitored')
        self.tasks.add(task)
        self._info[task] = _info
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
            self._info.pop(task)

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
        return [status for status in self._info.values() if status is not None]

    def get_task_status(self, task_name: str) -> Optional[TaskStatus]:
        for task, task_info in self._info.items():
            if task.get_name() == task_name:
                return task_info
        return None

class RuntimeContext:
    def __init__(self, bus: MessageBus, controller: TasksController):
        self.bus: MessageBus = bus
        self.controller: TasksController = controller
        self.extra: Dict[str, Any] = {}
        self._sigint_handler = signal.getsignal(signal.SIGINT)
        self._sigterm_handler = signal.getsignal(signal.SIGINT)

    def set_extra(self, name: str, value: Any):
        """
        Store extra object in RuntimeContext

        Used to add objects that doesn't exist when RuntimeContext is initialized
        (namely Settings instance)
        """
        self.extra[name] = value

    def get_extra(self, name: str) -> Optional[Any]:
        """Retrieve object stored by `set_extra`"""
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
