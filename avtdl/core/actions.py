import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional, Sequence

from pydantic import FilePath, NonNegativeFloat

from avtdl.core.interfaces import Action, ActionEntity, ActorConfig, Record, RuntimeContext
from avtdl.core.request import HttpClient
from avtdl.core.utils import SessionStorage, with_prefix


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

    @abstractmethod
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
    pass


class QueueAction(HttpAction):

    def __init__(self, conf: QueueActionConfig, entities: Sequence[QueueActionEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: QueueActionConfig
        self.entities: Mapping[str, QueueActionEntity]  # type: ignore
        self.queues: Dict[str, asyncio.Queue] = {entity.name: asyncio.Queue() for entity in entities}

    def get_client(self, entity: QueueActionEntity) -> HttpClient:
        return super().get_client(entity)

    def handle(self, entity: ActionEntity, record: Record):
        try:
            queue = self.queues[entity.name]
            queue.put_nowait(record)
            self.logger.debug(f'[{entity.name}] added new record to the queue, current queue size is {queue.qsize()}')
        except (asyncio.QueueFull, KeyError) as e:
            self.logger.exception(
                f'[{entity.name}] failed to add url, {type(e)}: {e}. This is a bug, please report it.')

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
        except Exception:
            logger.exception(f'unexpected error in background task, terminating')

    async def run(self) -> None:
        for entity in self.entities.values():
            _ = self.controller.create_task(self.run_for(entity), name=f'{self.conf.name}:{entity.name}')
        await super().run()

    @abstractmethod
    async def handle_single_record(self, logger: logging.Logger, client: HttpClient,
                                   entity: QueueActionEntity, record: Record) -> None:
        """Called for each record waiting in specific entity's queue"""


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

    def get_client(self, entity: TaskActionEntity) -> HttpClient:
        return super().get_client(entity)

    def handle(self, entity: TaskActionEntity, record: Record):
        logger = with_prefix(self.logger, f'[{entity.name}]')
        record_id = record.get_uid()
        if record_id in self.tasks:
            logger.debug(f'task for record {record_id} is already running')
            return
        name = f'{self.conf.name}:{entity.name} {record_id}'
        client = self.get_client(entity)
        task = self.controller.create_task(self._handle_record_task(logger, client, entity, record), name=name)
        task.add_done_callback(lambda _: self.tasks.pop(record_id))
        self.tasks[record_id] = task

    async def _handle_record_task(self, logger: logging.Logger, client: HttpClient,
                                  entity: TaskActionEntity, record: Record) -> None:
        async with self.start_token:
            # ideally delay should be applied after the task creation, but it means adding yet another create_task()
            await asyncio.sleep(self.conf.consumption_delay)
        try:
            await self.handle_record_task(logger, client, entity, record)
        except Exception as e:
            logger.exception(f'unexpected exception while processing record {record!r}')

    @abstractmethod
    async def handle_record_task(self, logger: logging.Logger, client: HttpClient,
                                 entity: TaskActionEntity, record: Record) -> None:
        """Scheduled as task for each record to be processed"""

    async def run(self) -> None:
        await super().run()
