
from dataclasses import dataclass
from collections import defaultdict
from enum import Enum
import importlib.util
import logging
from pathlib import Path
from typing import Dict, Tuple, List, Union, Callable

from core.interfaces import Monitor, MonitorEntity, MonitorConfig, Action, ActionEntity, ActionConfig, Filter, Event, EventMonitor
from core.chain import Chain

class TopSectionName(Enum):
    monitors: str = 'Monitors'
    filters: str = 'Filters'
    actions: str = 'Actions'
    chains: str = 'Chains'

class SectionName(Enum):
    config: str = 'config'
    defaults: str = 'defaults'
    entities: str = 'entities'

class ConfigParser:

    @classmethod
    def _parse_actor_section(cls, section: Dict, get_actor_factories: Callable):
        actors = {}
        for actor_type, items in section.items():
            ActorFactory, ConfigFactory, EntityFactory = get_actor_factories(actor_type)
            defaults = items.get('defaults', {})
            entities = []
            for entiry_item in items['entities']:
                entity = EntityFactory(**{**defaults, **entiry_item})
                entities.append(entity)

            config = ConfigFactory(**items.get('config', {}))
            actor = ActorFactory(config, entities)
            actors[actor_type] = actor
            return actors

    @classmethod
    def parse_monitors(cls, config_section: Dict) -> Dict[str, Monitor]:
        return cls._parse_actor_section(config_section, Plugins.get_monitor_factories)

    @classmethod
    def parse_actions(cls, config_section: Dict) -> Dict[str, Action]:
        return cls._parse_actor_section(config_section, Plugins.get_action_factories)

    @classmethod
    def parse_filters(cls, config_section: Dict) -> Dict[str, Filter]:
        filters = {}
        for filter_type, filters_list in config_section.items():
            FilterFactory = Plugins.get_filter_factory(filter_type)
            for entity in filters_list:
                filters[entity['name']] = FilterFactory(**entity)
        return filters

    @classmethod
    def parse_chains(cls, config_section: Dict,
                     monitors: Dict[str, Monitor],
                     actions: Dict[str, Action],
                     filters: Dict[str, Filter]) -> Dict[str, Chain]:
        chains = {}
        for name, chain_config in config_section.items():
            chain_monitors = []
            for monitors_list in chain_config['monitors'].items():
                monitor_type, entries_names = monitors_list
                monitor = monitors[monitor_type]
                chain_monitors.append((monitor, entries_names))
            chain_actions = []
            for actions_list in chain_config['actions'].items():
                action_type, entries_names = actions_list
                action = actions[action_type]
                chain_actions.append((action, entries_names))
            chain_filters = []
            for filter_type, filter_names in chain_config.get('filters', {}).items():
                for filter_name in filter_names:
                    if filter_name in filters:
                        chain_filters.append(filters[filter_name])
            chain_events = defaultdict(list)
            for event_type, actions_lists in chain_config.get('events', {}).items():
                for action_type, entries_names in actions_lists.items():
                    action = actions[action_type]
                    chain_events[event_type].append((action, entries_names))

            chain = Chain(name, chain_monitors, chain_actions, chain_filters, chain_events)
            chains[name] = chain
        return chains

    @classmethod
    def parse(cls, conf) -> Tuple[Dict[str, Monitor],
                                  Dict[str, Action],
                                  Dict[str, Filter],
                                  Dict[str, Chain]]:
        monitors_section = conf[TopSectionName.monitors.value]
        monitors = ConfigParser.parse_monitors(monitors_section)
        actions_section = conf[TopSectionName.actions.value]
        actions = ConfigParser.parse_actions(actions_section)
        filters_section = conf.get(TopSectionName.filters.value, {})
        filters = ConfigParser.parse_filters(filters_section)

        chains_section = conf[TopSectionName.chains.value]
        chains = ConfigParser.parse_chains(chains_section, monitors, actions, filters)

        return monitors, actions, filters, chains


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
    def _register(cls, name: str, kind: kind,
                  instance: Union[Monitor, MonitorEntity, MonitorConfig,
                                  Action, ActionEntity, ActionConfig,
                                  Filter]):
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


