from dataclasses import dataclass
from collections import defaultdict
from enum import Enum
import importlib.util
import logging
from pathlib import Path
import re
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

class ConfigurationError(Exception):
    '''Generic exception raised if parsing config failed'''

class ConfigParser:

    @classmethod
    def _parse_actor_section(cls, section: Dict, get_actor_factories: Callable, parent_name):
        check_section_is_type(section, parent_name, dict, f'sections {variants(TopSectionName)}')
        actors = {}
        for actor_type, items in section.items():
            ActorFactory, ConfigFactory, EntityFactory = get_actor_factories(actor_type)
            check_section_is_type(items, actor_type, dict, f'sections {variants(SectionName)}')
            defaults = get_section(items, actor_type, SectionName.defaults, {})
            entities_items = get_section(items, actor_type, SectionName.entities, section_type=list)
            entities = []
            for entiry_item in entities_items:
                data = {**defaults, **entiry_item}
                msg = f'in section {parent_name}: {actor_type}: failed to construct entity from data "{data}": '
                entity = try_constructing(EntityFactory, data, msg)
                entities.append(entity)
            config_dict = get_section(items, actor_type, SectionName.config, {})
            no_config_msg = 'config section is empty or absent' if config_dict == {} else f'error processing config section "{config_dict}"'
            config_dict['name'] = actor_type
            msg = f'in section {parent_name}: {actor_type}: {no_config_msg}:'
            config = try_constructing(ConfigFactory, config_dict, msg)
            actor = ActorFactory(config, entities)
            actors[actor_type] = actor
        return actors

    @classmethod
    def parse_monitors(cls, config_section: Dict) -> Dict[str, Monitor]:
        return cls._parse_actor_section(config_section, Plugins.get_monitor_factories, TopSectionName.monitors.value)

    @classmethod
    def parse_actions(cls, config_section: Dict) -> Dict[str, Action]:
        return cls._parse_actor_section(config_section, Plugins.get_action_factories, TopSectionName.actions.value)

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
        check_config_is_dict(conf)
        top_name = 'Configuration file'
        expected = 'dict of {} types'
        monitors_section = get_section(conf, top_name, TopSectionName.monitors, expected_description=expected.format(TopSectionName.monitors.value))
        monitors = ConfigParser.parse_monitors(monitors_section)
        actions_section = get_section(conf, top_name, TopSectionName.actions, expected_description=expected.format(TopSectionName.actions.value))
        actions = ConfigParser.parse_actions(actions_section)
        filters_section = get_section(conf, top_name, TopSectionName.filters, {}, expected_description=expected.format(TopSectionName.filters.value))
        filters = ConfigParser.parse_filters(filters_section)

        chains_section = get_section(conf, 'Configuration file', TopSectionName.chains)
        chains = ConfigParser.parse_chains(filters, chains_section)

        return monitors, actions, filters, chains


def get_section(conf, parent_name, section_name, default=..., section_type=dict, expected_description=None):
    section = conf.get(section_name.value, ...)
    section = section if section is not ... else default
    if section is not ...:
        check_section_is_type(section, section_name.value, section_type, expected_description)
        return section
    else:
        msg = f'{parent_name} is missing section "{section_name.value}"'
        raise ConfigurationError(msg)

def check_section_is_type(section, section_name, section_type, expected_description=None):
    try:
        check_type(section, section_type, expected_description)
    except ConfigurationError as e:
        msg = f'Section "{section_name}" has incorrect format: {e}'
        raise ConfigurationError(msg) from e

def check_type(item, expected_type, expected_description=None):
    if not isinstance(item, expected_type):
        gotten_type = type(item).__name__
        if gotten_type == 'NoneType':
            gotten_type = 'empty section'
        if expected_description is None:
            expected_description = expected_type.__name__
        msg = f'expected {expected_description}, got {gotten_type}'
        raise ConfigurationError(msg)

def check_config_is_dict(conf):
    try:
        expected = f'sections {variants(TopSectionName)}'
        check_type(conf, dict, expected)
    except ConfigurationError as e:
        msg = f'Configuration file has incorrect top-level structure: {e}'
        raise ConfigurationError(msg) from e

def variants(items):
    if issubclass(items, Enum):
        variants_list = [x.value for x in items.__members__.values()]
    elif isinstance(items, list):
        variants_list = [str(item) for item in items]
    elif isinstance(items, dict):
        variants_list = list(items.keys())
    elif isinstance(items, str):
        return items
    else:
        return str(items)
    return ', '.join(variants_list)

def try_constructing(factory: Callable, data: Dict, message: str):
    try:
        return factory(**data)
    except TypeError as e:
        message += re.sub(r'^.+__init__\(\)', '', str(e))
        raise ConfigurationError(message) from e
    except Exception as e:
        message = f'{message} {e}'
        raise ConfigurationError(message) from e
