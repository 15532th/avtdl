import importlib.util
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict


class UnknownPluginError(KeyError):
    """Raised if plugin with given name is not registered"""

class Plugins:
    class kind(Enum):
        ACTOR = 'actor'
        ACTOR_CONFIG = 'actor_config'
        ACTOR_ENTITY = 'actor_entity'
        ASSOCIATED_RECORD = 'associated_record'

    known: Dict[kind, Dict[str, Any]] = {k: {} for k in kind}
    logger = logging.getLogger('plugins')

    @classmethod
    def _register(cls, name: str, kind: kind, factory: Callable):
        if kind == cls.kind.ASSOCIATED_RECORD:
            if cls.known[kind].get(name) is None:
                cls.known[kind][name] = []
            cls.known[kind][name].append(factory)
        else:
            cls.known[kind][name] = factory

    @classmethod
    def _get(cls, name: str, kind: kind):
        instance = cls.known[kind].get(name)
        if instance is None:
            known = ', '.join(cls.known[kind].keys())
            raise UnknownPluginError(f'"{name}" is not registered as {kind.value} plugin. Known {kind.value} plugins are {known}')
        return instance

    @classmethod
    def get_actor_factories(cls, name):
        actor_factory = cls._get(name, cls.kind.ACTOR)
        config_factory = cls._get(name, cls.kind.ACTOR_CONFIG)
        entity_factory = cls._get(name, cls.kind.ACTOR_ENTITY)
        return actor_factory, config_factory, entity_factory

    @classmethod
    def get_associated_records(cls, name):
        try:
            return cls._get(name, cls.kind.ASSOCIATED_RECORD)
        except KeyError:
            return []

    @classmethod
    def register(cls, name: str, kind: kind):
        def wrapper(func):
            cls._register(name, kind, func)
            return func
        return wrapper

    @classmethod
    def load(cls):
        from avtdl import plugins
        for item in Path(plugins.__file__).parent.glob('*'):
            if item.stem.startswith('__'):
                cls.logger.debug(f'skipping "{item}"')
                continue
            module_name = plugins.__name__ + '.' + item.stem
            try:
                m = importlib.import_module(module_name)
                __import__(module_name, fromlist=m.__all__)
            except Exception:
                cls.logger.exception(f'while trying to import {module_name}:')
                continue
            else:
                cls.logger.info('from {} loaded {}'.format(module_name, ', '.join(m.__all__)))
