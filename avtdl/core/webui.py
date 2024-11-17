import asyncio
import json
import logging
import pathlib
from typing import Dict, List, Literal, Optional

import dateutil.zoneinfo
import yaml
from aiohttp import web
from pydantic import BaseModel

from avtdl.core import info
from avtdl.core.chain import Chain
from avtdl.core.config import ConfigParser, ConfigurationError, SettingsSection
from avtdl.core.info import get_known_plugins, get_plugin_type
from avtdl.core.interfaces import Actor
from avtdl.core.plugins import Plugins
from avtdl.core.utils import strip_text, write_file


def serialize_config(settings: SettingsSection,
                     actors: Dict[str, Actor],
                     chains: Dict[str, Chain],
                     mode: Literal['json', 'yaml'] = 'json') -> str:
    config = ConfigParser.serialize(settings, actors, chains)
    conf = config.model_dump_json()
    if mode == 'json':
        return conf
    elif mode == 'yaml':
        return json_to_yaml(conf)
    else:
        assert False, 'serialize config got unexpected mode'


def json_to_yaml(conf: str) -> str:
    """convert json string to yaml string"""
    conf = json.loads(conf)
    data = yaml.safe_dump(conf, sort_keys=False, allow_unicode=True)
    return data


def json_dumps(obj):
    def default(o):
        if isinstance(o, pathlib.Path):
            return str(o)
        raise TypeError(f'Object of type {o.__class__.__name__} is not JSON serializable')

    return json.dumps(obj, default=default)


def get_actor_description(name: str) -> str:
    plugin, config, entity = Plugins.get_actor_factories(name)
    md_description = info.render_doc(plugin)
    html_description = info.render_markdown(md_description)
    return html_description


def get_schema(model: BaseModel) -> dict:
    schema = model.model_json_schema(mode='serialization')
    render_descriptions(schema)
    return schema


def render_descriptions(schema) -> None:
    if isinstance(schema, dict):
        if 'description' in schema:
            text = schema['description']
            if isinstance(text, str) and len(text) > 0:
                end = '' if text[-1] in '.?!,' else '.'
                text = text[0].upper() + text[1:] + end
            schema['description'] = info.render_markdown(text)
        else:
            for subschema in schema.values():
                render_descriptions(subschema)
    elif isinstance(schema, list) or isinstance(schema, tuple):
        for subschema in schema:
            render_descriptions(subschema)
    else:
        return


def get_conf_schema(actor_name: str) -> dict:
    plugin, config, entity = Plugins.get_actor_factories(actor_name)
    return get_schema(config)


def get_entity_schema(actor_name: str) -> dict:
    plugin, config, entity = Plugins.get_actor_factories(actor_name)
    return get_schema(entity)


class ActorModel(BaseModel):
    type: Optional[str]
    description: str
    config_schema: dict
    entity_schema: dict


class WebUI:
    WEBROOT: pathlib.Path = pathlib.Path(__file__).parent.parent.resolve() / 'ui'

    def __init__(self, config_path: pathlib.Path, settings: SettingsSection, actors: Dict[str, Actor], chains: Dict[str, Chain]):
        self.logger = logging.getLogger('webui')
        self.host = 'localhost'
        self.port = settings.port
        self.config_path = config_path
        self.settings = settings
        self.actors = actors
        self.chains = chains
        self.routes: List[web.AbstractRouteDef] = []

        self.routes.append(web.get('/favicon.ico', self.favicon))
        self.routes.append(web.get('/chains', self.show_chains))
        self.routes.append(web.get('/actors', self.actors_models))
        self.routes.append(web.get('/settings', self.settings_schema))
        self.routes.append(web.get('/config', self.show_config))
        self.routes.append(web.post('/config', self.store_config))
        self.routes.append(web.get('/timezones', self.timezones))
        self.routes.append(web.get('/', self.index))
        self.routes.append(web.static('/ui', self.WEBROOT))

    async def favicon(self, request: web.Request) -> web.Response:
        return web.Response()

    async def index(self, request):
        raise web.HTTPFound('/ui/config.html')

    async def timezones(self, request: web.Request) -> web.Response:
        zones = list(dateutil.zoneinfo.get_zonefile_instance().zones.keys())
        return web.json_response(zones)

    async def show_chains(self, request: web.Request) -> web.Response:
        data = {name: chain.conf.model_dump() for name, chain in self.chains.items()}
        return web.json_response(data)

    async def actors_models(self, request: web.Request) -> web.Response:
        data: Dict[str, dict] = {}
        for name in get_known_plugins():
            data[name] = ActorModel(
                type=get_plugin_type(name),
                description=get_actor_description(name),
                config_schema=get_conf_schema(name),
                entity_schema=get_entity_schema(name)
            ).model_dump()
        return web.json_response(data, dumps=json_dumps)

    async def settings_schema(self, request: web.Request) -> web.Response:
        schema = self.settings.model_json_schema(mode='serialization')
        render_descriptions(schema)
        return web.json_response(schema, dumps=json_dumps)

    async def show_config(self, request):
        mode = request.query.get('mode', 'json')
        data = serialize_config(self.settings, self.actors, self.chains, mode=mode)
        return web.Response(text=data)

    async def store_config(self, request: web.Request) -> web.Response:
        mode = request.query.get('mode', 'check')
        if not mode in ['check', 'store', 'reload']:
            raise web.HTTPBadRequest(text=f'unexpected "mode" parameter {mode}')
        try:
            conf = await request.json()
            _ = ConfigParser.validate(conf)
            if mode == 'check':
                return web.Response(text='Config has been validated successfully')

            raw_yaml = json_to_yaml(json.dumps(conf))
            try:
                write_file(self.config_path, raw_yaml, backups=10)
            except Exception as e:
                raise web.HTTPServerError(text=f'failed to store config in "{self.config_path}": {e or type(e)}')
            if mode == 'reload':
                # would initiate restart here
                pass
            else:
                return web.Response(text=f'Updated config successfully stored in "{self.config_path}". It will be used after next restart.')
        except ConfigurationError as e:
            if e.__cause__ is None:
                raise web.HTTPBadRequest(text=f'Malformed configuration error {type(e)}: {e}')
            data = e.__cause__.errors()
            for error in data:
                if 'url' in error:
                    error.pop('url')
                if 'ctx' in error:
                    error.pop('ctx')
                if 'msg' in error:
                    error['msg'] = strip_text(error['msg'], 'Value error, ')

            return web.json_response(data=data, dumps=json_dumps, status=422, reason='Bad config')
        except Exception as e:
            raise web.HTTPBadRequest(text=f'{type(e)}: {e}')
        raise web.HTTPFound(location=request.path, reason='Config OK')


async def run_app(webui: WebUI):
    app = web.Application()
    app.add_routes(webui.routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, webui.host, webui.port)
    webui.logger.debug('starting server...')
    await site.start()
    webui.logger.info(f'server is running on http://{webui.host}:{webui.port}')
    try:
        await asyncio.Future()
    except (KeyboardInterrupt, asyncio.CancelledError):
        webui.logger.info('stopping server...')
        await runner.cleanup()
        webui.logger.debug('server stopped')


async def run(config_path: pathlib.Path, settings: SettingsSection, actors: Dict[str, Actor], chains: Dict[str, Chain]):
    webui = WebUI(config_path, settings, actors, chains)
    await run_app(webui)
