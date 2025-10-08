import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from pydantic import FilePath, NonNegativeFloat

from avtdl.core.actors import Action, ActionEntity, ActorConfig
from avtdl.core.config import SettingsSection
from avtdl.core.interfaces import Record
from avtdl.core.request import HttpClient, SessionStorage
from avtdl.core.runtime import RuntimeContext, TaskStatus
from avtdl.core.utils import DictRootModel, StateSerializer, with_prefix


class HttpActionConfig(ActorConfig):
    pass


class HttpActionEntity(ActionEntity):
    cookies_file: Optional[FilePath] = None
    """path to a text file containing cookies in Netscape format"""
    headers: Optional[Dict[str, str]] = {}
    """custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead"""


class HttpAction(Action, ABC):

    def __init__(self, conf: HttpActionConfig, entities: Sequence[HttpActionEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: HttpActionConfig
        self.entities: Mapping[str, HttpActionEntity]  # type: ignore
        self.sessions = SessionStorage(self.logger)

    async def run(self) -> None:
        name = f'ensure_closed for {self.logger.name} ({self!r})'
        _ = self.controller.create_task(self.sessions.ensure_closed(), name=name)
        await super().run()

    def get_client(self, entity: HttpActionEntity) -> HttpClient:
        """provide  HttpClient instance for entity task to make network requests"""
        session = self.sessions.get_session(entity.cookies_file, entity.headers)
        logger = with_prefix(self.logger, f'[{entity.name}] ')
        client = HttpClient(logger, session)
        return client


class QueueActionConfig(HttpActionConfig):
    consumption_delay: NonNegativeFloat = 1
    """delay before entity starts processing next record after finishing previous, in seconds"""


class QueueActionEntity(HttpActionEntity):
    restartable: bool = True
    """Attempt to store unprocessed records on disk at shutdown and process them on the next startup"""


class QueueAction(HttpAction):

    def __init__(self, conf: QueueActionConfig, entities: Sequence[QueueActionEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        settings: Optional[SettingsSection] = ctx.get_extra('settings')
        if settings is None:
            raise RuntimeError(f'runtime context is missing Settings instance. This is a bug, please report it')
        self.state_directory = settings.state_directory

        self.conf: QueueActionConfig
        self.entities: Mapping[str, QueueActionEntity]  # type: ignore
        self.queues: Dict[str, asyncio.Queue] = self.initialize_queues(self.entities.values())
        self.info: Dict[str, TaskStatus] = {entity.name: TaskStatus(self.conf.name, entity.name) for entity in entities}

    def persistence_file(self) -> Path:
        return Path(f'{self.conf.name}/queues.dat')

    def initialize_queues(self, entities: Sequence[QueueActionEntity]) -> Dict[str, asyncio.Queue]:
        unprocessed = StateSerializer.restore(QueuesSerialized, self.state_directory, self.persistence_file())
        if unprocessed is None:
            unprocessed  = QueuesSerialized()
        queues = {}
        for entity in entities:
            if entity.restartable:
                queue = unprocessed.retrieve(entity.name) or asyncio.Queue()
                self.logger.debug(f'[{entity.name}] ...')
            else:
                queue = asyncio.Queue()
            queues[entity.name] = queue
        return queues

    def dump_queues(self):
        queues = {}
        for name, queue in self.queues.items():
            queues[name] = []
            while True:
                try:
                    queues[name].append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
        StateSerializer.dump(QueuesSerialized(**queues), self.state_directory, self.persistence_file())

    def handle(self, entity: ActionEntity, record: Record):
        try:
            queue = self.queues[entity.name]
            queue.put_nowait(record)
        except (asyncio.QueueFull, KeyError) as e:
            self.logger.exception(
                f'[{entity.name}] failed to add url, {type(e)}: {e}. This is a bug, please report it.')
        else:
            self.logger.debug(f'[{entity.name}] added new record to the queue, current queue size is {queue.qsize()}')

    async def run_for(self, entity: QueueActionEntity):
        logger = with_prefix(self.logger, f'[{entity.name}] ')
        client = self.get_client(entity)
        queue = self.queues[entity.name]
        try:
            while True:
                record = await queue.get()
                self.logger.debug(f'(queued: {queue.qsize()}) processing record {record!r}')
                await self.handle_single_record(logger, client, entity, record)
                await asyncio.sleep(self.conf.consumption_delay)
                self.update_info(entity, record)
        except Exception:
            logger.exception(f'unexpected error in background task, terminating')

    def update_info(self, entity: QueueActionEntity, record: Record):
        info = self.info.get(entity.name)
        if info is None:
            return
        queue = self.queues[entity.name]
        size = queue.qsize()
        if size:
            info.set_status(f'current queue size is {size}', record)
        else:
            info.clear()

    async def run(self) -> None:
        for entity in self.entities.values():
            name = f'{self.conf.name}:{entity.name}'
            info = self.info.get(entity.name)
            _ = self.controller.create_task(self.run_for(entity), name=name, _info=info)
        try:
            await super().run()
            await asyncio.Future()
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.dump_queues()
            raise

    @abstractmethod
    async def handle_single_record(self, logger: logging.Logger, client: HttpClient,
                                   entity: QueueActionEntity, record: Record) -> None:
        """Called for each record waiting in specific entity's queue"""


class QueuesSerialized(DictRootModel):
    root: Dict[str, List[Record]] = {}

    def retrieve(self, key) -> Optional[asyncio.Queue]:
        if not key in self.root:
            return None
        queue: asyncio.Queue = asyncio.Queue()
        for item in reversed(self.root[key]):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                break
        return queue


class TaskActionConfig(HttpActionConfig):
    consumption_delay: NonNegativeFloat = 1
    """delay between start of processing of multiple records received at the same time, in seconds. Used to even out short bursts of activity"""


class TaskActionEntity(HttpActionEntity):
    pass


class TaskAction(HttpAction):

    def __init__(self, conf: TaskActionConfig, entities: Sequence[TaskActionEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: TaskActionConfig
        self.entities: Mapping[str, TaskActionEntity]  # type: ignore
        self.tasks: Dict[str, asyncio.Task] = {}
        self.start_token = asyncio.Lock()

    def handle(self, entity: TaskActionEntity, record: Record):
        logger = with_prefix(self.logger, f'[{entity.name}]')
        record_id = record.get_uid()
        if record_id in self.tasks:
            logger.debug(f'task for record {record_id} is already running')
            return
        name = f'{self.conf.name}:{entity.name} {record_id}'
        info = TaskStatus(self.conf.name, entity.name, record=record)
        client = self.get_client(entity)
        task = self.controller.create_task(self._handle_record_task(logger, client, entity, record, info), name=name, _info=info)
        task.add_done_callback(lambda _: self.tasks.pop(record_id))
        self.tasks[record_id] = task

    async def _handle_record_task(self, logger: logging.Logger, client: HttpClient,
                                  entity: TaskActionEntity, record: Record, info: TaskStatus) -> None:
        async with self.start_token:
            # ideally delay should be applied after the task creation, but it means adding yet another create_task()
            await asyncio.sleep(self.conf.consumption_delay)
        try:
            await self.handle_record_task(logger, client, entity, record, info)
        except Exception as e:
            logger.exception(f'unexpected exception while processing record {record!r}')

    @abstractmethod
    async def handle_record_task(self, logger: logging.Logger, client: HttpClient,
                                 entity: TaskActionEntity, record: Record, info: TaskStatus) -> None:
        """Scheduled as task for each record to be processed"""

    async def run(self) -> None:
        await super().run()
