from functools import wraps
from pathlib import Path
from typing import Dict, List, Tuple, Generic, TypeVar, Type

from pydantic import BaseModel, ValidationError, create_model

from core.chain import Chain, ChainConfigSection
from core.interfaces import (Actor)
from core.loggers import blacklist_loggers, set_file_logger
from core.plugins import Plugins


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
        except ValidationError as e:
            error = format_validation_error(e)
            raise ConfigurationError(error) from e
    return wrapper

class Settings(BaseModel):
    plugins_directory: str = 'plugins'
    log_directory: Path = Path('logs')
    logfile_size: int = 1000000
    log_blacklist: List[str] = ['bus', 'chain', 'filters']

def configure_loggers(settings: Settings):
    blacklist_loggers(settings.log_blacklist)
    set_file_logger(path=settings.log_directory, max_size=settings.logfile_size)

class ActorConfigSection(BaseModel):
    config: dict = {}
    defaults: dict = {}
    entities: List[dict]

class Config(BaseModel):
    Settings: Settings = Settings()
    Actors: Dict[str, ActorConfigSection]
    Chains: Dict[str, ChainConfigSection]


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
    def load_actors_plugins_model(actor_section: dict) -> Type[BaseModel]:
        actors_models = {}
        for name, section in actor_section.items():
            _, ConfigFactory, EntityFactory = Plugins.get_actor_factories(name)
            model = SpecificActorConfigSection[ConfigFactory, EntityFactory]
            actors_models[name] = (model, ...)
        actors_section_model = create_model('SpecificActors', **actors_models)
        return actors_section_model

    @classmethod
    def create_actors(cls, config_section: Dict[str, SpecificActorConfigSection]):
        actors = {}
        for name, actor_section in config_section:
            ActorFactory, _, _ = Plugins.get_actor_factories(name)
            actors[name] = ActorFactory(actor_section.config, actor_section.entities)
        return actors


class ConfigParser:

    @classmethod
    def flatten_config(cls, config: Config) -> Config:
        conf = config.model_dump()
        conf['Actors'] = ActorParser.flatten_actors_section(config.Actors)
        return Config(**conf)

    @classmethod
    def load_models(cls, config: Config) -> Type[BaseModel]:
        actors_model = ActorParser.load_actors_plugins_model(config.Actors)
        SpecificConfigModel = create_model('SpecificConfig',
                                     Actors=(actors_model, ...),
                                     Chains=(Dict[str, ChainConfigSection], ...)
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
    def parse(cls, conf) -> Tuple[Dict[str, Actor], Dict[str, Chain]]:
        # do basic structural validation of config file
        config = Config(**conf)

        configure_loggers(config.Settings)
        Plugins.load(config.Settings.plugins_directory)

        # after that entities transformation and specific plugins validation can be safely performed
        flatted_conf = cls.flatten_config(config)
        SpecificConfig = cls.load_models(config)
        specific_config = SpecificConfig(**flatted_conf.model_dump())

        actors = ActorParser.create_actors(specific_config.Actors)
        chains = ConfigParser.create_chains(specific_config.Chains)

        return actors, chains


