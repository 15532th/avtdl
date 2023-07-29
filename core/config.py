import importlib.util
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Callable, Dict, List, Tuple, Union

from pydantic import ValidationError

from core.chain import Chain
from core.interfaces import (Action, ActionConfig, ActionEntity, Event, Filter,
                             Monitor, MonitorConfig, MonitorEntity)
from core.plugins import Plugins


class TopSectionName(Enum):
    monitors: str = 'Monitors'
    filters: str = 'Filters'
    actions: str = 'Actions'
    events: str = 'Events'
    chains: str = 'Chains'

class SectionName(Enum):
    config: str = 'config'
    defaults: str = 'defaults'
    entities: str = 'entities'

class FilterSectionName(Enum):
    name: str = 'name'
    patterns: str = 'patterns'

class ConfigurationError(Exception):
    '''Generic exception raised if parsing config failed'''

def format_validation_error(e):
    errors = ['error parsing "{}" in "{}": {}'.format(err['input'], ' '.join(err['loc']), err['msg']) for err in e.errors()]
    return ', '.join(errors)

def try_parse(message_prefix):
    def add_error_prefix(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except (ValueError, KeyError, TypeError, ConfigurationError) as e:
                msg = f'{message_prefix}: {e}'
                raise ConfigurationError(msg) from e
        return wrapper
    return add_error_prefix

class ConfigParser:

    @classmethod
    def _parse_actor_section(cls, section: Dict, get_actor_factories: Callable):
        actors = {}
        for actor_type, items in section.items():
            parse_actor = try_parse(actor_type)(cls._parse_actor)
            actor = parse_actor(actor_type, items, get_actor_factories)
            actors[actor_type] = actor
        return actors

    @classmethod
    def _parse_actor(cls, actor_type: str, items: Dict, get_actor_factories: Callable):
        ActorFactory, ConfigFactory, EntityFactory = get_actor_factories(actor_type)
        check_has_keys(items, [SectionName.entities.value])
        defaults = get_section(items, SectionName.defaults.value, {})
        entities_items = get_section(items, SectionName.entities.value, section_type=list)
        entities = []
        for entity_item in entities_items:
            msg = f'{SectionName.entities.value}: failed to parse entity "{entity_item}"'
            check_entity_is_type(entity_item, dict, msg)
            data = {**defaults, **entity_item}
            msg = f'{SectionName.entities.value}: failed to construct {EntityFactory.__name__} entity from data "{data}"'
            entity = try_constructing(EntityFactory, **data, message=msg)
            entities.append(entity)
        config_dict = get_section(items, SectionName.config.value, {})
        if config_dict == {}:
            no_config_msg = 'config section is empty or absent'
        else:
            no_config_msg = f'error processing config section "{config_dict}"'
        config_dict['name'] = actor_type
        config = try_constructing(ConfigFactory, **config_dict, message=f'{no_config_msg}')

        actor_failed_msg = f'initialization of {ActorFactory.__name__} from {config} failed'
        actor = try_constructing(ActorFactory, config, entities, message=actor_failed_msg)
        return actor

    @classmethod
    @try_parse(TopSectionName.monitors.value)
    def parse_monitors(cls, config_section: Dict) -> Dict[str, Monitor]:
        return cls._parse_actor_section(config_section, Plugins.get_monitor_factories)

    @classmethod
    @try_parse(TopSectionName.actions.value)
    def parse_actions(cls, config_section: Dict) -> Dict[str, Action]:
        return cls._parse_actor_section(config_section, Plugins.get_action_factories)

    @classmethod
    @try_parse(TopSectionName.filters.value)
    def parse_filters(cls, config_section: Dict) -> Dict[str, Filter]:
        filters = {}
        for filter_type, filters_list in config_section.items():
            check_entity_is_type(filters_list, list, message_prefix=filter_type)
            FilterFactory = Plugins.get_filter_factory(filter_type)
            for entity in filters_list:
                msg = f'{filter_type}: error parsing entity "{entity}"'
                check_entity_is_type(entity, dict, message_prefix=msg)
                filter_instance = try_constructing(FilterFactory, **entity, message=msg)
                filters[entity['name']] = filter_instance
        return filters

    @classmethod
    @try_parse(TopSectionName.chains.value)
    def parse_chains(cls,
                     filters: Dict[str, Filter],
                     config_section: Dict) -> Dict[str, Chain]:
        chains = {}
        for name, chain_config in config_section.items():
            check_entity_is_type(chain_config, dict, message_prefix=name)
            parse_chain = try_parse(name)(cls._parse_chain)
            chains[name] = parse_chain(name, chain_config, filters)
        return chains

    @classmethod
    def _parse_chain(cls, name, chain_config, filters):
        chain_filters = []
        chain_filters_section = get_section(chain_config, TopSectionName.filters.value, {})
        for filter_type, filter_names in chain_filters_section.items():
            for filter_name in filter_names:
                if filter_name in filters:
                    chain_filters.append(filters[filter_name])
                else:
                    msg = f'filter with name "{filter_name}" not found in {TopSectionName.filters} section'
                    raise ConfigurationError(msg)
        chain_monitors = get_section(chain_config, TopSectionName.monitors.value)
        chain_actions = get_section(chain_config, TopSectionName.actions.value)
        chain_events = get_section(chain_config, TopSectionName.events.value, {})

        chain = Chain(name, chain_filters, chain_monitors, chain_actions, chain_events)
        return chain

    @classmethod
    def parse(cls, conf) -> Tuple[Dict[str, Monitor],
                                  Dict[str, Action],
                                  Dict[str, Filter],
                                  Dict[str, Chain]]:
        msg = 'Configuration file has incorrect top-level structure'
        expected = f'sections {variants(TopSectionName)}'
        check_entity_is_type(conf, dict, msg, expected)

        monitors_section = get_top_section(conf, TopSectionName.monitors)
        monitors = ConfigParser.parse_monitors(monitors_section)
        actions_section = get_top_section(conf, TopSectionName.actions)
        actions = ConfigParser.parse_actions(actions_section)
        filters_section = get_top_section(conf, TopSectionName.filters, {})
        filters = ConfigParser.parse_filters(filters_section)

        chains_section = get_top_section(conf, TopSectionName.chains)
        chains = ConfigParser.parse_chains(filters, chains_section)

        return monitors, actions, filters, chains

def check_has_keys(section: Dict, fields: List[str], message_prefix=None):
    check_entity_is_type(section, dict, message_prefix)
    for field in fields:
        if field not in section:
            prefix = f'{message_prefix}: ' if message_prefix else ''
            msg = prefix + f'missing required field "{field}" in section "{section}"'
            raise ConfigurationError(msg)

def get_top_section(conf, name: TopSectionName, default=...):
    expected = 'name of plugin'
    return get_section(conf, name.value, default, message_prefix=name.value, expected=expected)

def get_section(conf, section_name, default=..., section_type=dict, message_prefix=None, expected=None):
    if message_prefix is None:
        message_prefix = section_name
    section = conf.get(section_name, ...)
    section = section if section is not ... else default
    if section is ...:
        msg = f'missing section "{section_name}"'
        raise ConfigurationError(msg)
    check_entity_is_type(section, section_type, message_prefix, expected)
    return section

def check_entity_is_type(entity, entity_type, message_prefix=None, expected=None):
    try:
        check_type(entity, entity_type, expected)
    except ConfigurationError as e:
        prefix = f'{message_prefix}: ' if message_prefix else ''
        msg = prefix + f'{e}'
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

def enum_values(an_enum: Enum) -> List:
    return [x.value for x in an_enum.__members__.values()]

def variants(items):
    if issubclass(items, Enum):
        variants_list = enum_values(items)
    elif isinstance(items, list):
        variants_list = [str(item) for item in items]
    elif isinstance(items, dict):
        variants_list = list(items.keys())
    elif isinstance(items, str):
        return items
    else:
        return str(items)
    return ', '.join(variants_list)

def try_constructing(factory: Callable, *args, message: str = '', **kwargs):
    try:
        return factory(*args, **kwargs)
    except TypeError as e:
        error = re.sub(r'^.+__init__\(\)', '', str(e))
        message = f'{message}: {error}'
        raise ConfigurationError(message) from e
    except ValidationError as e:
        error = format_validation_error(e)
        message = f'{message}: {error}'
        raise ConfigurationError(message) from e
    except Exception as e:
        message = f'{message}: {e}'
        raise ConfigurationError(message) from e
