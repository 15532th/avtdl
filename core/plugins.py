import importlib.util
import logging
from enum import Enum
from pathlib import Path
from typing import Dict, Union

from core.chain import Chain
from core.interfaces import Action, ActionConfig, ActionEntity, Event, Filter, Monitor, MonitorConfig, MonitorEntity


class Plugins:
    class kind(Enum):
        MONITOR = 'monitor'
        MONITOR_CONFIG = 'monitor_config'
        MONITOR_ENTITY = 'monitor_entity'
        ACTION = 'action'
        ACTION_CONFIG = 'action_config'
        ACTION_ENTITY = 'action_entity'
        FILTER = 'filter'

    known: Dict[kind, Dict] = {k: {} for k in kind}

    @classmethod
    def _register(
        cls, name: str, kind: kind, instance: Union[Monitor, MonitorEntity, MonitorConfig,
                                                    Action, ActionEntity, ActionConfig, Filter]
    ):
        cls.known[kind][name] = instance

    @classmethod
    def _get(cls, name: str, kind: kind):
        instance = cls.known[kind].get(name)
        if instance is None:
            raise KeyError(f'"{name}" is not registered as "{kind}"')
        return instance

    @classmethod
    def get_monitor_factories(cls, name):
        monitor_factory = cls._get(name, cls.kind.MONITOR)
        config_factory = cls._get(name, cls.kind.MONITOR_CONFIG)
        entity_factory = cls._get(name, cls.kind.MONITOR_ENTITY)
        return (monitor_factory, config_factory, entity_factory)

    @classmethod
    def get_action_factories(cls, name):
        action_factory = cls._get(name, cls.kind.ACTION)
        config_factory = cls._get(name, cls.kind.ACTION_CONFIG)
        entity_factory = cls._get(name, cls.kind.ACTION_ENTITY)
        return (action_factory, config_factory, entity_factory)

    @classmethod
    def get_filter_factory(cls, name):
        filter_factory = cls._get(name, cls.kind.FILTER)
        return filter_factory

    @classmethod
    def register(cls, name: str, kind: kind):
        def wrapper(func):
            cls._register(name, kind, func)
            return func

        return wrapper

    @classmethod
    def load(cls, directory='plugins'):
        for item in Path(directory).glob('*'):
            try:
                module_name = '.'.join(item.parts)
                m = importlib.import_module(module_name)
                __import__(module_name, fromlist=m.__all__)
            except Exception:
                logging.exception(f'while trying to import {module_name}:')
                continue
            else:
                logging.info('from {} imported {}'.format(module_name, ', '.join(m.__all__)))
