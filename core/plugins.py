import importlib.util
import logging
from enum import Enum
from pathlib import Path
from typing import Dict, Union

from core.interfaces import Actor, ActorConfig, ActorEntity


class Plugins:
    class kind(Enum):
        ACTOR = 'actor'
        ACTOR_CONFIG = 'actor_config'
        ACTOR_ENTITY = 'actor_entity'

    known: Dict[kind, Dict] = {k: {} for k in kind}
    logger = logging.getLogger('plugins')

    @classmethod
    def _register(cls, name: str, kind: kind, factory: Union[Actor, ActorConfig, ActorEntity]):
        cls.known[kind][name] = factory

    @classmethod
    def _get(cls, name: str, kind: kind):
        instance = cls.known[kind].get(name)
        if instance is None:
            known = ', '.join(cls.known[kind].keys())
            raise KeyError(f'"{name}" is not registered as {kind.value} plugin. Known {kind.value} plugins are {known}')
        return instance

    @classmethod
    def get_actor_factories(cls, name):
        actor_factory = cls._get(name, cls.kind.ACTOR)
        config_factory = cls._get(name, cls.kind.ACTOR_CONFIG)
        entity_factory = cls._get(name, cls.kind.ACTOR_ENTITY)
        return actor_factory, config_factory, entity_factory

    @classmethod
    def register(cls, name: str, kind: kind):
        def wrapper(func):
            cls._register(name, kind, func)
            return func
        return wrapper

    @classmethod
    def load(cls, directory='plugins'):
        for item in Path(directory).glob('*'):
            module_name = '.'.join(item.parts)
            try:
                m = importlib.import_module(module_name)
                __import__(module_name, fromlist=m.__all__)
            except Exception:
                cls.logger.exception(f'while trying to import {module_name}:')
                continue
            else:
                cls.logger.info('from {} imported {}'.format(module_name, ', '.join(m.__all__)))
