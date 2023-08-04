import importlib.util
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Callable, Dict, List, Tuple, Union, Any, Generic, TypeVar, Type

from pydantic import BaseModel, RootModel, ValidationError, field_validator, model_validator, create_model

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

def format_validation_error(e: ValidationError) -> str:
    msg = 'Failed to process configuration file, following errors occurred: '
    errors = []
    for err in e.errors():
        location = ': '.join(str(l) for l in err['loc'])
        error = 'error parsing "{}" in config section "{}": {}'
        errors.append(error.format(err['input'], location, err['msg']))
    return '\n    '.join([msg] + errors)

def try_parsing(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except TypeError as e:
            error = re.sub(r'^.+__init__\(\)', '', str(e))
            raise ConfigurationError(error) from e
        except ValidationError as e:
            error = format_validation_error(e)
            raise ConfigurationError(error) from e
    return wrapper

class ActorConfigSection(BaseModel):
    config: dict = {}
    defaults: dict = {}
    entities: List[dict]

class MonitorConfigSection(ActorConfigSection):
    pass

class ActionConfigSection(ActorConfigSection):
    pass

class ChainSection(BaseModel):
    Monitors: Dict[str, List[str]]
    Actions: Dict[str, List[str]]
    Filters: Dict[str, List[str]] = {}
    Events: dict = {}


class Config(BaseModel):
    Monitors: Dict[str, MonitorConfigSection]
    Actions: Dict[str, ActionConfigSection]
    Filters: Dict[str, List[dict]] = {}
    Chains: Dict[str, ChainSection]


TConfig = TypeVar('TConfig')
TEntity = TypeVar('TEntity')

class SpecificActorConfigSection(BaseModel, Generic[TConfig, TEntity]):
    config: TConfig
    entities: List[TEntity]

class ActorParser:

    @staticmethod
    def flatten_actor_section(name: str, section: ActorConfigSection) -> dict:
        config = {**section.config, **{'name': name}}
        data = {'name': name, 'config': config, 'entities': []}
        for entity in section.entities:
            data['entities'].append({**section.defaults, **entity})
        return data

    @classmethod
    def flatten_actors_section(cls, section: Dict[str, ActorConfigSection]):
        return {n: cls.flatten_actor_section(n, s) for n, s in section.items()}

    @staticmethod
    def load_actor_plugin_model(actor_section: dict, get_actor_factories: Callable) -> BaseModel:
        actors_models = {}
        for name, section in actor_section.items():
            ActorFactory, ConfigFactory, EntityFactory = get_actor_factories(name)
            model = SpecificActorConfigSection[ConfigFactory, EntityFactory]
            actors_models[name] = (model, ...)
        actors_section_model = create_model('SpecificActors', **actors_models)
        return actors_section_model

    @classmethod
    def create_actors(cls, config_section: Dict[str, SpecificActorConfigSection], get_actor_factories: Callable):
        actors = {}
        for name, actor_section in config_section:
            ActorFactory, _, _ = get_actor_factories(name)
            actors[name] = ActorFactory(actor_section.config, actor_section.entities)
        return actors


class FilterParser:

    @staticmethod
    def load_filter_plugin_model(filter_section: dict) -> BaseModel:
        filter_models = {}
        for name, section in filter_section.items():
            model = Plugins.get_filter_factory(name)
            filter_models[name] = (List[model], ...)
        filters_section_model = create_model('SpecificFilters', **filter_models)
        return filters_section_model

    @classmethod
    def parse_filters(cls, config_section: Dict) -> Dict[str, Filter]:
        filters = {}
        for filter_type, filters_list in config_section.items():
            FilterFactory = Plugins.get_filter_factory(filter_type)
            for entity in filters_list:
                filters[entity['name']] = FilterFactory(**entity)
        return filters

class ConfigParser:

    @staticmethod
    def get_monitors_section_model(config_section: dict) -> BaseModel:
        return ActorParser.load_actor_plugin_model(config_section, Plugins.get_monitor_factories)
    @staticmethod
    def create_monitors(config_section: Dict[str, SpecificActorConfigSection]) -> Dict[str, Monitor]:
        return ActorParser.create_actors(config_section, Plugins.get_monitor_factories)

    @staticmethod
    def get_actions_section_model(config_section: dict) -> BaseModel:
        return ActorParser.load_actor_plugin_model(config_section, Plugins.get_action_factories)
    @staticmethod
    def create_actions(config_section: Dict[str, SpecificActorConfigSection]) -> Dict[str, Action]:
        return ActorParser.create_actors(config_section, Plugins.get_action_factories)

    @classmethod
    def flatten_config(cls, config: Config) -> Config:
        conf = config.model_dump()
        conf['Monitors'] = ActorParser.flatten_actors_section(config.Monitors)
        conf['Actions'] = ActorParser.flatten_actors_section(config.Actions)
        return Config(**conf)

    @classmethod
    def load_models(cls, config: Config):
        monitors_model = cls.get_monitors_section_model(config.Monitors)
        actions_model = cls.get_actions_section_model(config.Actions)
        filters_model = FilterParser.load_filter_plugin_model(config.Filters)
        SpecificConfigModel = create_model('SpecificConfig',
                                     Monitors=(monitors_model, ...),
                                     Actions=(actions_model, ...),
                                     Filters=(filters_model, ...),
                                     Chains=(Dict[str, ChainSection], ...)
                                     )
        return SpecificConfigModel

    @classmethod
    def parse_chains(cls,
                     filters: Dict[str, Filter],
                     config_section: Dict) -> Dict[str, Chain]:
        chains = {}
        for name, chain_config in config_section.items():
            chains[name] = cls._parse_chain(name, chain_config, filters)
        return chains

    @classmethod
    def _parse_chain(cls, name, chain_config: ChainSection, filters):
        chain_filters = []
        chain_filters_section = chain_config.Filters
        for filter_type, filter_names in chain_filters_section.items():
            for filter_name in filter_names:
                if filter_name in filters:
                    chain_filters.append(filters[filter_name])
                else:
                    msg = f'filter with name "{filter_name}" not found in {TopSectionName.filters} section'
                    raise ConfigurationError(msg)
        chain_monitors = chain_config.Monitors
        chain_actions = chain_config.Actions
        chain_events = chain_config.Events

        chain = Chain(name, chain_filters, chain_monitors, chain_actions, chain_events)
        return chain

    @classmethod
    @try_parsing
    def parse(cls, conf) -> Tuple[Dict[str, Monitor],
                                  Dict[str, Action],
                                  Dict[str, Filter],
                                  Dict[str, Chain]]:
        # do basic structural validation of config file
        config = Config(**conf)
        # after that entities transformation and specific plugins validation can be safely performed
        specific_conf = cls.flatten_config(config)
        SpecificConfig = cls.load_models(config)
        specific_config = SpecificConfig(**specific_conf.model_dump())

        monitors = ConfigParser.create_monitors(specific_config.Monitors)
        actions = ConfigParser.create_actions(specific_config.Actions)
        filters = specific_config.Filters
        chains = specific_config.Chains

        return monitors, actions, filters, chains


