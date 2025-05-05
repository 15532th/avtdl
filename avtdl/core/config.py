import logging
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Generic, List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, create_model, field_validator

from avtdl.core import utils
from avtdl.core.chain import Chain, ChainConfigSection
from avtdl.core.interfaces import Actor, RuntimeContext
from avtdl.core.loggers import LogLevel, override_loglevel, set_file_logger
from avtdl.core.plugins import Plugins
from avtdl.core.utils import strip_text


class ConfigurationError(Exception):
    """Generic exception raised if parsing config failed"""


def format_validation_error(e: ValidationError) -> str:
    msg = 'Failed to process configuration file, following errors occurred: '
    errors = []
    for err in e.errors():
        user_input = str(err['input'])
        user_input = user_input if len(user_input) < 85 else user_input[:50] + ' [...] ' + user_input[-30:]
        location = ': '.join(str(l) for l in err['loc'])
        error_message = strip_text(err['msg'], 'Value error, ')
        error = 'error parsing "{}" in config section {}: {}'
        errors.append(error.format(user_input, location, error_message))
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
    model_config = ConfigDict(use_attribute_docstrings=True)

    log_directory: Path = Field(default='logs', validate_default=True)
    """path to a directory where application will write log file"""
    logfile_size: int = Field(gt=0, default=1000000)
    """size of a single log file in bytes. After reaching this size the file will be replaced by a new one. Only last 10 files are kept inside the log directory"""
    logfile_level: LogLevel = LogLevel.debug
    """how detailed the output to log file is. Can be "DEBUG", "INFO", "WARNING" or "ERROR". It is recommended to keep log file loglevel set to "DEBUG" """
    loglevel_override: Dict[str, LogLevel] = {'bus': LogLevel.info, 'chain': LogLevel.info,
                                              'actor.request': LogLevel.info}
    """allows to overwrite loglevel of a specific logger. Used to prevent a single talkative logger from filling up the log file"""
    port: int = Field(gt=0, le=65535, default=8080)
    """web-interface port"""
    host: str = 'localhost'
    """web-interface host, typically "127.0.0.1", "0.0.0.0" or the machine external IP"""
    encoding: Optional[str] = None
    """configuration file encoding. Leave empty to use system-wide default. Note, that webui will forcibly overwrite empty value with "utf8" when saving new configuration"""
    cache_directory: Path = Field(default='cache/cache/', validate_default=True)
    """directory used for storing pre-downloaded images and other resources, used to display records in the web-interface.
    Send records through the "cache" plugin to download and store resources it references"""

    @field_validator('cache_directory')
    @classmethod
    def check_dir(cls, path: Path):
        ok = utils.check_dir(path)
        if ok:
            return path
        else:
            raise ValueError(f'check path "{path}" exists and is a writeable directory')


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


class SpecificActors(RootModel):
    root: Dict[str, SpecificActorConfigSection]


class ActorParser:

    @staticmethod
    def flatten_actor_section(name: str, section: ActorConfigSection) -> ActorConfigSection:
        config = {**section.config, **{'name': name}, **{'defaults': section.defaults}}
        data: Dict[str, Any] = {'name': name, 'config': config, 'entities': []}
        for entity in section.entities:
            data['entities'].append({**section.defaults, **entity})
        return ActorConfigSection(**data)

    @staticmethod
    def load_actors_plugins_model(actor_section: dict) -> SpecificActors:
        actors_models: Dict[str, Any] = {}
        for name, section in actor_section.items():
            _, ConfigFactory, EntityFactory = Plugins.get_actor_factories(name)
            model = SpecificActorConfigSection[ConfigFactory, EntityFactory]
            actors_models[name] = (model, ...)
        actors_section_model = create_model('SpecificActors', **actors_models)
        return actors_section_model

    @classmethod
    def create_actors(cls, config_section: SpecificActors, ctx: RuntimeContext) -> Dict[str, Actor]:
        actors = {}
        for name, actor_section in config_section:
            ActorFactory, _, _ = Plugins.get_actor_factories(name)
            actors[name] = ActorFactory(actor_section.config, actor_section.entities, ctx)
        return actors

    @classmethod
    def serialize_actor(cls, actor: Actor) -> ActorConfigSection:
        actor_config = actor.conf.model_dump()
        defaults = actor.conf.defaults
        entities = [entity.model_dump() for entity in actor.entities.values()]
        for entity in entities:
            for field, value in defaults.items():
                if field in entity and entity[field] == value:
                    entity.pop(field)
        section = ActorConfigSection(config=actor_config, defaults=defaults, entities=entities)
        return section


class SpecificConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    settings: SettingsSection
    actors: SpecificActors
    chains: Dict[str, ChainConfigSection]


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
    def load_models(config: Config) -> Type[SpecificConfig]:
        actors_model: SpecificActors = ActorParser.load_actors_plugins_model(config.actors)
        SpecificConfigModel: type[SpecificConfig] = create_model('SpecificConfig',
                                                                __base__= SpecificConfig,
                                                                 settings=(SettingsSection, ...),
                                                                 actors=(actors_model, ...),
                                                                 chains=(Dict[str, ChainConfigSection], ...)
                                                                 )
        return SpecificConfigModel

    @classmethod
    def create_chains(cls, chains_section: Dict[str, ChainConfigSection], ctx: RuntimeContext) -> Dict[str, Chain]:
        chains = {}
        for name, chain_config in chains_section.items():
            chains[name] = Chain(name, chain_config, ctx)
        return chains

    @classmethod
    @try_parsing
    def parse(cls, conf: dict, ctx: RuntimeContext) -> Tuple[SettingsSection, Dict[str, Any], Dict[str, Chain]]:
        # do basic structural validation of config file
        config = Config(**conf)

        ctx.set_extra('settings', config.settings)
        configure_loggers(config.settings)
        Plugins.load()

        # after that entities transformation and specific plugins validation can be safely performed
        flatted_conf = cls.flatten_config(config)
        SpecificConfig = cls.load_models(config)
        specific_config = SpecificConfig(**flatted_conf.model_dump())

        actors = ActorParser.create_actors(specific_config.actors, ctx)
        chains = ConfigParser.create_chains(specific_config.chains, ctx)

        return config.settings, actors, chains

    @classmethod
    def serialize(cls, settings: SettingsSection, actors: Dict[str, Actor], chains: Dict[str, Chain]) -> Config:
        actors_section = {name: ActorParser.serialize_actor(actor) for name, actor in actors.items()}
        chains_section = {name: chain.conf for name, chain in chains.items()}
        config = Config(settings=settings, actors=actors_section, chains=chains_section)
        return config

    @classmethod
    @try_parsing
    def validate(cls, conf: dict) -> 'SpecificConfig':
        """try parsing object, raise ConfigurationError on failure"""
        config = Config(**conf)
        flatted_conf = cls.flatten_config(config)
        SpecificConfig = cls.load_models(config)
        return SpecificConfig(**flatted_conf.model_dump())


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
