from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Dict, List
from collections import defaultdict


@dataclass
class Record:
    '''Data entry, passed around from Monitors to Actions through Filters'''
    title: str
    url: str

    def __str__(self):
        return f'{self.title} ({self.url})'


@dataclass
class ActionConfig:
    pass

@dataclass
class ActionEntity:
    name: str


class EventsMixin:
    def __init__(self, conf: ActionConfig, entities: List[ActionEntity]):
        self.conf = conf
        self.entities = {}
        for entity in entities:
            self.entities[entity.name] = entity
        self.events = ['beginning', 'success', 'failure']
        self.callbacks: Dict[str, List[Callable]] = defaultdict(list)

    def on_event(self, event: str, record: Record):
        '''Implementation should call it at appropriate timing'''
        if event in self.callbacks:
            for cb in self.callbacks[event]:
                cb(record)

    def register(self, event: str, callback: Callable[[], Record]):
        '''Register callback to be called for every new record'''
        if event in self.entities:
            self.callbacks[event].append(callback)
        else:
            raise ValueError(f'Unable to register callback for {event}: no such feed')

class Action(EventsMixin, ABC):

    def __init__(self, conf: ActionConfig, entities: List[ActionEntity]):
        self.conf = conf
        self.entities = {}
        for entity in entities:
            self.entities[entity.name] = entity
        super().__init__(conf, entities)

    @abstractmethod
    def handle(self, entity_name: str, record: Record):
        '''Perform action on record if entity in self.entities'''

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

class Monitor(ABC):

    def __init__(self, conf: MonitorConfig, entities: List[MonitorEntity]):
        self.conf = conf
        self.entities = {}
        for entity in entities:
            self.entities[entity.name] = entity
        self.callbacks: Dict[str, List[Callable]] = defaultdict(list)

    def register(self, entity_name: str, callback: Callable[[str, Record], None]):
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

    @abstractmethod
    async def run(self):
        '''Will be runned as asyncio task once everything set up'''
        return


class Filter:

    @abstractmethod
    def match(self, record):
        '''Take record and return it if it matches some condition
        or otherwise process it, else return None'''
