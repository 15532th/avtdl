from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Sequence
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

@dataclass
class MonitorConfig:
    pass

@dataclass
class MonitorEntity:
    name: str

class Monitor(RunnableMixin, ABC):

    def __init__(self, conf: MonitorConfig, entities: Sequence[MonitorEntity]):
        self.conf = conf
        self.entities = {entity.name: entity for entity in entities}
        self.callbacks: Dict[str, List[Callable]] = defaultdict(list)

    def register(self, entity_name: str, callback: Callable[[Record], None]):
        '''Register callback to be called for every new record'''
        if entity_name in self.entities:
            self.callbacks[entity_name].append(callback)
        else:
            raise ValueError(f'Unable to register callback for {entity_name}: no such feed')

    def on_record(self, entity_name: str, record: Record):
        '''Implementation should call it for every new Record'''
        if entity_name in self.callbacks:
            for cb in self.callbacks[entity_name]:
                cb(record)

    def __repr__(self):
        return f'{self.__class__.__name__}({self.entities!r})'


class Event(Enum):
    start: str = 'start'
    end: str = 'end'
    error: str = 'error'

class EventMonitor(Monitor):

    def __init__(self) -> None:
        entities = [MonitorEntity(name.value) for name in Event]
        super().__init__(MonitorConfig(), entities)


@dataclass
class ActionConfig:
    pass

@dataclass
class ActionEntity:
    name: str

class Action(RunnableMixin, ABC):

    def __init__(self, conf: ActionConfig, entities: Sequence[ActionEntity]):
        self.conf = conf
        self.entities = {entity.name: entity for entity in entities}

    @abstractmethod
    def handle(self, entity_name: str, record: Record):
        '''Perform action on record if entity in self.entities'''

    def __repr__(self):
        return f'{self.__class__.__name__}({self.entities!r})'


class Filter:

    @abstractmethod
    def match(self, record):
        '''Take record and return it if it matches some condition
        or otherwise process it, else return None'''
