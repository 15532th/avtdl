import logging
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Generic, List, Tuple, Type, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from avtdl.core.chain import Chain, ChainConfigSection
from avtdl.core.loggers import LogLevel, override_loglevel, set_file_logger
from avtdl.core.plugins import Plugins


class ConfigurationError(Exception):
    '''Generic exception raised if parsing config failed'''

def format_validation_error(e: ValidationError) -> str:
    msg = 'Failed to process configuration file, following errors occurred: '
    errors = []
    for err in e.errors():
        user_input = str(err['input'])
        user_input = user_input if len(user_input) < 85 else user_input[:50] + ' [...] ' + user_input[-30:]
        location = ': '.join(str(l) for l in err['loc'])
        error = 'error parsing "{}" in config section {}: {}'
        errors.append(error.format(user_input, location, err['msg']))
    return '\n    '.join([msg] + errors)

def try_parsing(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ValidationError as e:
            error = format_validation_error(e)
            raise ConfigurationError(error) from e
    return wrapper

class SettingsSection(BaseModel):
    log_directory: Path = Path('logs')
    logfile_size: int = 1000000
    logfile_level: LogLevel = LogLevel.debug
    loglevel_override: Dict[str, LogLevel] = {'bus': LogLevel.info, 'chain': LogLevel.info, 'actor.request': LogLevel.info}

def configure_loggers(settings: SettingsSection):
    override_loglevel(settings.loglevel_override)
    set_file_logger(path=settings.log_directory, max_size=settings.logfile_size, level=settings.logfile_level)

class ActorConfigSection(BaseModel):
    config: dict = {}
    defaults: dict = {}
    entities: List[dict]

class Config(BaseModel):
    model_config = ConfigDict(extra='forbid')

    settings: SettingsSection = SettingsSection()
    actors: Dict[str, ActorConfigSection]
    chains: Dict[str, ChainConfigSection]


TConfig = TypeVar('TConfig')
TEntity = TypeVar('TEntity')

class SpecificActorConfigSection(BaseModel, Generic[TConfig, TEntity]):
    config: TConfig
    entities: List[TEntity]

class ActorParser:

    @staticmethod
    def flatten_actor_section(name: str, section: ActorConfigSection) -> ActorConfigSection:
        config = {**section.config, **{'name': name}}
        data: Dict[str, Any] = {'name': name, 'config': config, 'entities': []}
        for entity in section.entities:
            data['entities'].append({**section.defaults, **entity})
        return ActorConfigSection(**data)

    @staticmethod
    def load_actors_plugins_model(actor_section: dict) -> Dict[str, SpecificActorConfigSection]:
        actors_models: Dict[str, Any] = {}
        for name, section in actor_section.items():
            _, ConfigFactory, EntityFactory = Plugins.get_actor_factories(name)
            model = SpecificActorConfigSection[ConfigFactory, EntityFactory]
            actors_models[name] = (model, ...)
        actors_section_model = create_model('SpecificActors', **actors_models)
        return actors_section_model

    @classmethod
    def create_actors(cls, config_section: 'SpecificActors') -> Dict[str, Type]:
        actors = {}
        for name, actor_section in config_section:
            ActorFactory, _, _ = Plugins.get_actor_factories(name)
            actors[name] = ActorFactory(actor_section.config, actor_section.entities)
        return actors


class ConfigParser:

    @staticmethod
    def flatten_config(config: Config) -> Config:
        conf = config.model_dump()
        actors_section: Dict[str, ActorConfigSection] = {}
        for name, section in config.actors.items():
            actors_section[name] = ActorParser.flatten_actor_section(name, section)
        conf['actors'] = actors_section
        return Config(**conf)

    @staticmethod
    def load_models(config: Config) -> Type['SpecificConfig']:
        actors_model = ActorParser.load_actors_plugins_model(config.actors)
        SpecificConfigModel = create_model('SpecificConfig',
                                     actors=(actors_model, ...),
                                     chains=(Dict[str, ChainConfigSection], ...)
                                     )
        return SpecificConfigModel

    @classmethod
    def create_chains(cls, chains_section: Dict[str, ChainConfigSection]) -> Dict[str, Chain]:
        chains = {}
        for name, chain_config in chains_section.items():
            chains[name] = Chain(name, chain_config)
        return chains

    @classmethod
    @try_parsing
    def parse(cls, conf) -> Tuple[Dict[str, Any], Dict[str, Chain]]:
        # do basic structural validation of config file
        config = Config(**conf)

        configure_loggers(config.settings)
        Plugins.load()

        # after that entities transformation and specific plugins validation can be safely performed
        flatted_conf = cls.flatten_config(config)
        SpecificConfig = cls.load_models(config)
        specific_config = SpecificConfig(**flatted_conf.model_dump())

        actors = ActorParser.create_actors(specific_config.actors)
        chains = ConfigParser.create_chains(specific_config.chains)

        return actors, chains


def config_sancheck(actors, chains):
    """check for possible non-fatal misconfiguration and issue a warning"""
    for chain_name, chain_instance in chains.items():
        for actor_name, entities in chain_instance.conf:
            actor = actors.get(actor_name)
            if actor is None:
                logging.warning(
                    f'chain "{chain_name}" references actor "{actor_name}, absent in "Actors" section. It might be a typo in the chain configuration')
                continue
            orphans = set(entities) - actor.entities.keys()
            for orphan in orphans:
                logging.warning(
                    f'chain "{chain_name}" references "{actor_name}: {orphan}", but actor "{actor_name}" has no "{orphan}" entity. It might be a typo in the chain conf configuration')
