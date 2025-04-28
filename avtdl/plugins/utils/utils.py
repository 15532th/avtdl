import asyncio
from collections import defaultdict
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence

from pydantic import FilePath, NonNegativeFloat, NonNegativeInt

from avtdl.core.db import RecordDB
from avtdl.core.interfaces import Action, ActionEntity, ActorConfig, Monitor, MonitorEntity, Record, RuntimeContext
from avtdl.core.plugins import Plugins


class QuitMode(str, Enum):
    single = 'single'
    gather = 'gather'


@Plugins.register('utils.quit', Plugins.kind.ACTOR_CONFIG)
class QuitActionConfig(ActorConfig):
    pass


@Plugins.register('utils.quit', Plugins.kind.ACTOR_ENTITY)
class QuitActionEntity(ActionEntity):
    local_counter: int = -1
    """decrement counter on every received record, quit when it reaches zero"""
    global_counter: int = -1
    """decrement counter on every received record, quit when all global counters reaches zero"""


@Plugins.register('utils.quit', Plugins.kind.ACTOR)
class QuitAction(Action):
    """
    Used for testing purposes

    Triggers avtdl shutdown when specified number of records is received.
    """

    def __init__(self, conf: QuitActionConfig, entities: Sequence[QuitActionEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)

    def handle(self, entity: QuitActionEntity, record: Record):
        if entity.local_counter > 0:
            entity.local_counter -= 1
            self.logger.info(f'[{entity.name}] decrementing local counter, {entity.local_counter} left')
        if entity.local_counter == 0:
            self.quit(entity)

        if entity.global_counter > 0:
            entity.global_counter -= 1
            self.logger.info(
                f'[{entity.name}] decrementing global counter, {entity.global_counter} left for this entity')
        self.check_global_counter(entity)

    def check_global_counter(self, entity: QuitActionEntity):
        counters = [entity.global_counter for entity in self.entities.values() if entity.global_counter >= 0]
        if all(c == 0 for c in counters):
            self.quit(entity)

    def quit(self, entity: QuitActionEntity):
        msg = f'[{entity.name}] interrupting program execution'
        self.logger.info(msg)
        raise KeyboardInterrupt(msg)


Plugins.register('utils.producer', Plugins.kind.ACTOR_CONFIG)(ActorConfig)

Plugins.register('utils.producer', Plugins.kind.ACTOR_ENTITY)(MonitorEntity)


@Plugins.register('utils.producer', Plugins.kind.ACTOR)
class Producer(Monitor):
    """
    Used for testing purposes

    Produces records programmatically.
    """

    def produce(self, entity_name: str, record: Record):
        """Programmatically emit given record on behalf of own entity with given name"""
        entity = self.entities[entity_name]
        self.on_record(entity, record)


Plugins.register('utils.consumer', Plugins.kind.ACTOR_CONFIG)(ActorConfig)

Plugins.register('utils.consumer', Plugins.kind.ACTOR_ENTITY)(ActionEntity)


@Plugins.register('utils.consumer', Plugins.kind.ACTOR)
class Consumer(Action):
    """
    Used for testing purposes

    Keeps history of received records, allow registering handlers
    for records received by specific entity.
    """

    def __init__(self, conf: ActorConfig, entities: Sequence[ActionEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.history: Dict[str, List[Record]] = {entity.name: [] for entity in entities}
        self.callbacks: Dict[Optional[str], List[Callable]] = defaultdict(list)

    def handle(self, entity: ActionEntity, record: Record):
        if entity.name in self.history:
            self.history[entity.name].append(record)
        for callback in self.callbacks[None]:
            callback(entity, record)
        if entity.name in self.callbacks:
            for callback in self.callbacks[entity.name]:
                callback(entity, record)

    def register_callback(self, callback: Callable[[ActionEntity, Record], None], entity_name: Optional[str] = None):
        self.callbacks[entity_name].append(callback)


Plugins.register('utils.replay', Plugins.kind.ACTOR_CONFIG)(ActorConfig)


@Plugins.register('utils.replay', Plugins.kind.ACTOR_ENTITY)
class ReplayEntity(MonitorEntity):
    db_path: FilePath
    """path to the sqlite database file storing records"""
    entity_name: Optional[str] = None
    """if specified, only records belonging to entity with this name are replayed"""
    emit_limit: NonNegativeInt = 10
    """how many records should be replayed"""
    emit_interval: NonNegativeFloat = 0.01
    """delay between two consequentially produced records, in seconds"""
    reverse: bool = True
    """replace records from newest to oldest"""


@Plugins.register('utils.replay', Plugins.kind.ACTOR)
class Replay(Monitor):
    """
    Used for testing purposes

    Load records from db, emit them one by one
    """

    def __init__(self, conf: ActorConfig, entities: Sequence[ReplayEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.databases = {}
        for entity in entities:
            db = RecordDB(entity.db_path, logger=self.logger.getChild('name'))
            self.databases[entity.name] = db

    async def replay_task(self, entity: ReplayEntity):
        db = self.databases.get(entity.name)
        if db is None:
            self.logger.exception(f'no database is opened for entity {entity.name}')
            return
        records = db.load_page(entity.entity_name, 0, entity.emit_limit, entity.reverse)
        self.logger.debug(f'[{entity.name}] {len(records)} records to emit with {entity.emit_interval} interval')
        for record in records:
            self.on_record(entity, record)
            await asyncio.sleep(entity.emit_interval)
        self.logger.debug(f'[{entity.name}] done')

    async def run(self):
        for entity in self.entities.values():
            task = self.replay_task(entity)
            self.ctx.controller.create_task(task, name=f'{self.conf.name}:{entity.name}')
        await super().run()
