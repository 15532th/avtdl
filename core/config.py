from dataclasses import dataclass
from collections import defaultdict
from enum import Enum
import importlib.util
import logging
from pathlib import Path
from typing import Dict, Tuple, List, Union, Callable

from core.interfaces import Monitor, MonitorEntity, MonitorConfig, Action, ActionEntity, ActionConfig, Filter, Event
from core.chain import Chain
from core.plugins import Plugins

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
            config_dict = items.get('config', {})
            config_dict['name'] = actor_type
            config = ConfigFactory(**config_dict)
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
    def parse_chains(cls,
                     filters: Dict[str, Filter],
                     config_section: Dict) -> Dict[str, Chain]:
        chains = {}
        for name, chain_config in config_section.items():
            chain_filters = []
            for filter_type, filter_names in chain_config.get('filters', {}).items():
                for filter_name in filter_names:
                    if filter_name in filters:
                        chain_filters.append(filters[filter_name])
            chain_monitors = chain_config['monitors']
            chain_actions = chain_config['actions']
            chain_events = chain_config.get('events', {})

            chain = Chain(name, chain_filters, chain_monitors, chain_actions, chain_events)
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
        chains = ConfigParser.parse_chains(filters, chains_section)

        return monitors, actions, filters, chains
