import asyncio
import json
import logging
import pathlib
import urllib.parse
from collections import defaultdict
from typing import Callable, Dict, List, Optional

import dateutil.zoneinfo
from aiohttp import web
from pydantic import BaseModel

from avtdl.core import formatters, info
from avtdl.core.cache import FileCache
from avtdl.core.chain import Chain
from avtdl.core.config import ConfigParser, ConfigurationError, SettingsSection
from avtdl.core.db import HistoryView
from avtdl.core.info import get_known_plugins, get_plugin_type, render_markdown
from avtdl.core.interfaces import AbstractRecordsStorage, Actor, Record, RuntimeContext, TaskStatus, \
    TerminatedAction
from avtdl.core.plugins import Plugins
from avtdl.core.utils import JSONType, strip_text, write_file
from avtdl.core.yaml import merge_data, yaml_dump

RECORDS_PER_PAGE = 32


def serialize_config(settings: SettingsSection,
                     actors: Dict[str, Actor],
                     chains: Dict[str, Chain]) -> str:
    config = ConfigParser.serialize(settings, actors, chains)
    conf = config.model_dump_json()
    return conf


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


def record_preview(record: Record, representation: str = 'text') -> str:
    if representation == 'text':
        return str(record).replace('\n', '<br>\n')
    elif representation == 'json':
        return record.as_json(indent=4)
    elif representation == 'short':
        return repr(record)
    else:
        return str(record)


class ActorModel(BaseModel):
    type: Optional[str]
    description: str
    config_schema: dict
    entity_schema: dict


class WebUI:
    WEBROOT: pathlib.Path = pathlib.Path(__file__).parent.parent.resolve() / 'ui'
    CACHE_ROUTE = '/cache'
    RESTART_DELAY: int = 3

    def __init__(self, config_path: pathlib.Path, config, ctx: RuntimeContext, settings: SettingsSection,
                 actors: Dict[str, Actor], chains: Dict[str, Chain]):
        self.logger = logging.getLogger('webui')
        self.host = settings.host
        self.port = settings.port
        self.config_path = config_path
        self.config_base = config
        self.ctx = ctx
        self.settings = settings
        self.actors = actors
        self.chains = chains
        self.restart_pending = False
        self.cache = FileCache(self.settings.cache_directory, '.part')
        self._actors_models = self.generate_actors_models()
        self.routes: List[web.AbstractRouteDef] = []

        self.routes.append(web.get('/favicon.ico', self.favicon))
        self.routes.append(web.get('/chains', self.show_chains))
        self.routes.append(web.get('/actors', self.actors_models))
        self.routes.append(web.get('/settings', self.settings_schema))
        self.routes.append(web.get('/config', self.show_config))
        self.routes.append(web.post('/config', self.store_config))
        self.routes.append(web.get('/timezones', self.timezones))
        self.routes.append(web.get('/motd', self.motd))
        self.routes.append(web.get('/history', self.history))
        self.routes.append(web.get('/tasks', self.tasks))

        self.routes.append(web.get('/ui/info/info.html', self.info_webui))

        self.routes.append(web.get('/viewable', self.viewable_plugins))
        self.routes.append(web.get('/records', self.records))
        self.routes.append(web.static(self.CACHE_ROUTE, self.cache.cache_directory))

        self.routes.append(web.get('/', self.index))
        self.routes.append(web.static('/ui', self.WEBROOT))

    async def favicon(self, request: web.Request) -> web.Response:
        return web.Response()

    async def index(self, request):
        raise web.HTTPFound('/ui/conf/config.html')

    async def timezones(self, request: web.Request) -> web.Response:
        zones = list(dateutil.zoneinfo.get_zonefile_instance().zones.keys())
        return web.json_response(zones)

    async def show_chains(self, request: web.Request) -> web.Response:
        data = {name: chain.conf.model_dump() for name, chain in self.chains.items()}
        return web.json_response(data)

    @staticmethod
    def generate_actors_models() -> Dict[str, dict]:
        data: Dict[str, dict] = {}

        for name in get_known_plugins():
            data[name] = ActorModel(
                type=get_plugin_type(name),
                description=get_actor_description(name),
                config_schema=get_conf_schema(name),
                entity_schema=get_entity_schema(name)
            ).model_dump()
        return data

    async def actors_models(self, request: web.Request) -> web.Response:
        return web.json_response(self._actors_models, dumps=json_dumps)

    async def settings_schema(self, request: web.Request) -> web.Response:
        schema = self.settings.model_json_schema(mode='serialization')
        render_descriptions(schema)
        return web.json_response(schema, dumps=json_dumps)

    async def show_config(self, request: web.Request):
        data = serialize_config(self.settings, self.actors, self.chains)
        return web.Response(text=data)

    async def store_config(self, request: web.Request) -> web.Response:
        mode = request.query.get('mode', 'check')
        if not mode in ['check', 'store', 'reload']:
            raise web.HTTPBadRequest(text=f'unexpected "mode" parameter {mode}')
        try:
            conf = await request.json()
            parsed_config = ConfigParser.validate(conf)
            config_encoding = parsed_config.settings.encoding

            updated_config = merge_data(self.config_base, conf)
            raw_yaml = yaml_dump(updated_config)
            if mode == 'check':
                return web.Response(text='Config has been validated successfully')

            self.config_base = updated_config

            try:
                write_file(self.config_path, raw_yaml, encoding=config_encoding, backups=10)
            except Exception as e:
                raise web.HTTPInternalServerError(
                    text=f'failed to store config in "{self.config_path}": {e or type(e)}')
            if mode == 'reload':
                self.restart_pending = True
                self.ctx.controller.terminate_after(self.RESTART_DELAY, TerminatedAction.RESTART)
                return web.Response(
                    text=f'Updated config successfully stored in "{self.config_path}". Restarting in a few seconds (the page will reload).')
            else:
                text = f'Updated config successfully stored in "{self.config_path}". It will be used after next restart.'
                return web.Response(text=text)
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
        except web.HTTPError:
            raise
        except Exception as e:
            raise web.HTTPBadRequest(text=f'{type(e)}: {e}')
        raise web.HTTPFound(location=request.path, reason='Config OK')

    async def motd(self, request: web.Request) -> web.Response:
        if self.restart_pending:
            raise web.HTTPServiceUnavailable(headers={'Retry-After': str(self.RESTART_DELAY)})
        motd = f'''
Server is up and running, working directory is "{pathlib.Path('.').resolve()}".
Configuration contains {len(self.actors)} actors and {len(self.chains)} chains, loaded from "{self.config_path.resolve()}".
'''
        data = {'motd': motd}
        return web.json_response(data, dumps=json_dumps)

    async def info_webui(self, request: web.Request) -> web.Response:

        template_path = self.WEBROOT / 'info/info.html'
        template = template_path.read_text(encoding='utf8')

        document_path = self.WEBROOT / 'info/info.md'
        document = document_path.read_text(encoding='utf8')
        body = render_markdown(document)

        html = template.replace('{{body}}', body)
        return web.Response(text=html, content_type='text/html')

    async def history(self, request: web.Request) -> web.Response:
        actor = request.query.get('actor')
        entity = request.query.get('entity')
        chain = request.query.get('chain', '')
        representation = request.query.get('repr', 'text')
        if actor is None or entity is None:
            raise web.HTTPBadRequest(
                text=f'not enough arguments. Got actor="{actor}", entity="{entity}", chain="{chain}"')
        incoming = self.ctx.bus.get_history(actor, entity, chain, 'in')
        outgoing = self.ctx.bus.get_history(actor, entity, chain, 'out')
        data_structure = [
            (f'Incoming records (most recent)', incoming),
            (f'Outgoing records (most recent)', outgoing)
        ]
        data = {}

        for title, content in data_structure:
            rows = []
            for record in content:
                row = [int(record.created_at.timestamp() * 1000),
                       record.origin,
                       record.chain,
                       record_preview(record, representation)]
                rows.append(row)
            data[title] = rows
        return web.json_response(data, dumps=json_dumps)

    @staticmethod
    def render_status_data(status_list: List[TaskStatus], actor: Optional[str]) -> dict:
        if not status_list:
            return {}
        headers = ['State', 'Actor', 'Entity', 'Record']
        data: dict = defaultdict(lambda: {'headers': headers, 'rows': []})
        for status in status_list:
            if status.actor_name is None:
                continue
            if actor is not None and status.actor_name != actor:
                continue
            record = record_preview(status.record) if status.record else ''
            row = [status.status, status.actor_name, status.entity_name, record]
            data[status.actor_name]['rows'].append(row)
        return data

    async def tasks(self, request: web.Request) -> web.Response:
        actor_name = request.query.get('actor')
        actor = self.actors.get(actor_name) if actor_name is not None else None
        if actor_name is not None and actor is None:
            raise web.HTTPBadRequest(text=f'actor "{actor_name}" is not found')
        status_list = self.ctx.controller.get_status()
        data = self.render_status_data(status_list, actor_name)
        return web.json_response(data, dumps=json_dumps)

    async def viewable_plugins(self, request: web.Request) -> web.Response:
        runtime: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(dict)
        internal: Dict[str, Dict[str, Dict[str, str]]] = defaultdict(dict)
        viewable: Dict[str, str] = {}
        for actor_name, actor in self.actors.items():
            entities = {}
            for entity_name in actor.entities.keys():
                query = urllib.parse.urlencode({'actor': actor_name, 'entity': entity_name})
                entities[entity_name] = query
            actor_type = get_plugin_type(actor_name) or 'Other'
            if actor.conf.name == 'view':
                viewable = entities
            elif actor.get_records_storage() is not None:
                internal[actor_type][actor_name] = entities
            else:
                runtime[actor_type][actor_name] = entities
        data: dict = {}
        if viewable:
            data['View stored records'] = viewable
        if internal:
            data['View internal database'] = internal
        if runtime:
            data['View runtime history'] = runtime
        return web.json_response(data, dumps=json_dumps)

    def _get_embed_image_rewriter(self, record: Record) -> Callable[[str], Optional[str]]:
        def rewriter(image_url: str) -> Optional[str]:
            image_file = self.cache.retrieve(record, image_url)
            if image_file is None:
                return None
            try:
                relative = image_file.relative_to(self.cache.cache_directory)
            except Exception as e:
                msg = f'cached file "{image_file}" for url "{image_url}" is not relative to "{self.cache.cache_directory}"'
                self.logger.warning(msg)
                return None
            resource = str(self.CACHE_ROUTE / relative)
            return resource
        return rewriter

    def render_record(self, record: Record) -> JSONType:
        embeds = formatters.MessageFormatter.make_embeds(record, False)
        rewriter = self._get_embed_image_rewriter(record)
        for embed in embeds:
            formatters.MessageFormatter.rewrite_embed_links(embed, rewriter)
        message = formatters.MessageFormatter.make_message(embeds)
        return message

    async def records(self, request: web.Request) -> web.Response:
        actor_name = request.query.get('actor')
        entity_name = request.query.get('entity')
        page_num = request.query.get('page')
        page_size = request.query.get('size', RECORDS_PER_PAGE)

        if page_num is not None:
            try:
                page = int(page_num)
            except ValueError:
                raise web.HTTPBadRequest(text=f'page number must be positive integer')
        else:
            page = None
        try:
            per_page = int(page_size)
        except ValueError:
            raise web.HTTPBadRequest(text=f'page size must be positive integer')

        if actor_name is None:
            raise web.HTTPBadRequest(text=f'missing "actor" parameter')
        if actor_name not in self.actors:
            raise web.HTTPBadRequest(text=f'actor {actor_name} not found')
        if entity_name is None:
            raise web.HTTPBadRequest(text=f'missing "entity" parameter')

        actor = self.actors[actor_name]

        if entity_name not in actor.entities:
            raise web.HTTPBadRequest(text=f'actor {actor_name} does not have entity "{entity_name}"')

        db: Optional[AbstractRecordsStorage] = actor.get_records_storage(entity_name)
        if db is None:
            # HistoryView instance should have been returned from base Actor.get_records_storage implementation,
            # but it would lead to circular imports between interfaces.py and db.py
            db = HistoryView(actor, entity_name)
        records = db.load_page(page, per_page)
        total_pages = db.page_count(per_page)
        records_view = [self.render_record(record) for record in records]

        data = {
            'total': total_pages,
            'current': page or total_pages,
            'records': records_view
        }
        return web.json_response(data, dumps=json_dumps)


async def run_app(webui: WebUI):
    app = web.Application()
    app.add_routes(webui.routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, webui.host, webui.port)
    webui.logger.debug('starting server...')
    try:
        await site.start()
    except Exception as e:
        webui.logger.exception(f'failed to start server: {e}')
        return
    webui.logger.info(f'server is running on http://{webui.host}:{webui.port}')
    try:
        await asyncio.Future()
    except (KeyboardInterrupt, asyncio.CancelledError):
        webui.logger.info('stopping server...')
        await runner.cleanup()
        webui.logger.debug('server stopped')


async def run(config_path: pathlib.Path, config, ctx: RuntimeContext, settings: SettingsSection,
              actors: Dict[str, Actor], chains: Dict[str, Chain]):
    webui = WebUI(config_path, config, ctx, settings, actors, chains)
    await run_app(webui)
