from collections import defaultdict
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence

from avtdl.core.interfaces import Action, ActionEntity, ActorConfig, Monitor, MonitorEntity, Record
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
    def __init__(self, conf: QuitActionConfig, entities: Sequence[QuitActionEntity]):
        super().__init__(conf, entities)

    def handle(self, entity: QuitActionEntity, record: Record):
        if entity.local_counter > 0:
            entity.local_counter -= 1
            self.logger.info(f'[{entity.name}] decrementing local counter, {entity.local_counter} left')
        if entity.local_counter == 0:
            self.quit(entity)

        if entity.global_counter > 0:
            entity.global_counter -= 1
            self.logger.info(f'[{entity.name}] decrementing global counter, {entity.global_counter} left for this entity')
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
    def __init__(self, conf: ActorConfig, entities: Sequence[ActionEntity]):
        super().__init__(conf, entities)
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