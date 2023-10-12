import asyncio
import sqlite3
from abc import abstractmethod
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

import aiohttp
from pydantic import Field, FilePath, field_validator

from core import utils
from core.interfaces import Actor, ActorConfig, ActorEntity, Record
from core.utils import get_cache_ttl, show_diff


class TaskMonitorEntity(ActorEntity):
    update_interval: float


class BaseTaskMonitor(Actor):

    def __init__(self, conf: ActorConfig, entities: Sequence[TaskMonitorEntity]):
        super().__init__(conf, entities)
        self.tasks: Dict[str, asyncio.Task] = {}

    def handle(self, entity: ActorEntity, record: Record) -> None:
        self.on_record(entity, record)

    async def run(self):
        # start cyclic tasks
        await self.start_cyclic_tasks()
        # and wait forever
        await asyncio.Future()

    async def start_cyclic_tasks(self):
        by_entity_interval = defaultdict(list)
        for entity in self.entities.values():
            by_entity_interval[entity.update_interval].append(entity)
        by_group_interval = {interval / len(entities): entities for interval, entities in by_entity_interval.items()}
        for interval in sorted(by_group_interval.keys()):
            entities = by_group_interval[interval]
            asyncio.create_task(self.start_tasks_for(entities, interval))

    async def start_tasks_for(self, entities, interval):
        logger = self.logger.getChild('scheduler')
        if len(entities) == 0:
            logger.debug(f'called with no entities and {interval} interval')
            return
        names = ', '.join([f'{self.conf.name}.{entity.name}' for entity in entities])
        logger.info(f'will start {len(entities)} tasks with {entities[0].update_interval:.1f} update interval and {interval:.1f} offset for {names}')
        for entity in entities:
            logger.debug(f'starting task {entity.name} with {entity.update_interval} update interval')
            self.tasks[entity.name] = asyncio.create_task(self.run_for(entity), name=f'{self.conf.name}:{entity.name}')
            if entity == entities[-1]: # skip sleeping after last
                continue
            await asyncio.sleep(interval)
        logger.info(f'done starting tasks for {names}')

    @abstractmethod
    async def run_for(self, entity: TaskMonitorEntity):
        '''Task for specific entity that should check for new records based on update_interval and call self.on_record() for each'''


class TaskMonitor(BaseTaskMonitor):

    async def run_for(self, entity: TaskMonitorEntity):
        while True:
            try:
                await self.run_once(entity)
            except Exception:
                self.logger.exception(f'{self.conf.name}: task for entity {entity} failed, terminating')
                break
            await asyncio.sleep(entity.update_interval)

    async def run_once(self, entity: TaskMonitorEntity):
        records = await self.get_new_records(entity)
        for record in records:
            self.on_record(entity, record)

    @abstractmethod
    async def get_new_records(self, entity: TaskMonitorEntity) -> Sequence[Record]:
        '''Produce new records, optionally adjust update_interval'''


class HttpTaskMonitorEntity(TaskMonitorEntity):
    cookies_file: Optional[FilePath] = None


class HttpTaskMonitor(BaseTaskMonitor):
    '''Maintain and provide for records aiohttp.ClientSession objects
    grouped by HttpTaskMonitorEntity.cookies_path, which means entities that use
    the same cookies file will share session'''

    def __init__(self, conf: ActorConfig, entities: Sequence[HttpTaskMonitorEntity]):
        self.sessions: Dict[str, aiohttp.ClientSession] = {}
        super().__init__(conf, entities)

    def _get_session(self, entity: HttpTaskMonitorEntity) -> aiohttp.ClientSession:
        session_id = str(entity.cookies_file)
        session = self.sessions.get(session_id)
        if session is None:
            cookies = utils.load_cookies(entity.cookies_file)
            session = aiohttp.ClientSession(cookies=cookies)
        return session

    async def run_for(self, entity: HttpTaskMonitorEntity):
        session = self._get_session(entity)
        async with session:
            while True:
                try:
                    await self.run_once(entity, session)
                except Exception:
                    self.logger.exception(f'{self.conf.name}: task for entity {entity} failed, terminating')
                    break
                await asyncio.sleep(entity.update_interval)

    async def run_once(self, entity: TaskMonitorEntity, session: aiohttp.ClientSession):
        records = await self.get_new_records(entity, session)
        for record in records:
            self.on_record(entity, record)

    @abstractmethod
    async def get_new_records(self, entity: TaskMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        '''Produce new records, optionally adjust update_interval'''


class BaseFeedMonitorConfig(ActorConfig):
    db_path: Union[Path, str] = ':memory:'

    @field_validator('db_path')
    @classmethod
    def str_to_path(cls, path: Union[Path, str]):
        if path == ':memory:':
            return path
        return Path(path)
    
class BaseFeedMonitorEntity(HttpTaskMonitorEntity):
    url: str
    adjust_update_interval: bool = True
    base_update_interval: float = Field(exclude=True, default=60)
    last_modified: Optional[str] = Field(exclude=True, default=None)
    etag: Optional[str] = Field(exclude=True, default=None)

    def model_post_init(self, __context: Any) -> None:
        self.base_update_interval = self.update_interval

class BaseFeedMonitor(HttpTaskMonitor):

    def __init__(self, conf: BaseFeedMonitorConfig, entities: Sequence[BaseFeedMonitorEntity]):
        super().__init__(conf, entities)
        self.db = RecordDB(conf.db_path, logger=self.logger.getChild('db'))

    async def request(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession, method='GET') -> Optional[aiohttp.ClientResponse]:
        '''Helper method to make http request. Does not retry, adjusts entity.update_interval instead'''
        logger = self.logger.getChild('request')
        request_headers: Dict[str, Any] = {}
        if entity.last_modified is not None and method in ['GET', 'HEAD']:
            request_headers['If-Modified-Since'] = entity.last_modified
        if entity.etag is not None:
            request_headers['If-None-Match'] = entity.etag
        try:
            async with session.request(method, entity.url, headers=request_headers) as response:
                response.raise_for_status()
                # fully read http response to get it cached inside ClientResponse object
                # client code can then use it by awaiting .text() again without causing
                # network activity and potentially triggering associated errors
                _ = await response.text()
        except Exception as e:
            if isinstance(e, aiohttp.ClientResponseError):
                logger.warning(f'[{entity.name}] got code {e.status} ({e.message}) while fetching {entity.url}')
            else:
                logger.warning(f'[{entity.name}] error while fetching {entity.url}: {e}')

            update_interval = min(entity.update_interval * 2, entity.base_update_interval * 10, 4*3600)
            if entity.update_interval != update_interval:
                entity.update_interval = update_interval
                logger.warning(f'[{entity.name}] update interval set to {entity.update_interval} seconds for {entity.url}')
            return None

        if response.status == 304:
            logger.debug(f'[{entity.name}] got {response.status} ({response.reason}) from {entity.url}')
            return None
        # some servers do not have cache headers in 304 response, so only updating on 200
        entity.last_modified = response.headers.get('Last-Modified', None)
        entity.etag = response.headers.get('Etag', None)

        cache_control = response.headers.get('Cache-control')
        logger.debug(f'[{entity.name}] Last-Modified={entity.last_modified or "absent"}, ETAG={entity.etag or "absent"}, Cache-control="{cache_control or "absent"}"')

        if entity.adjust_update_interval:
            update_interval = get_cache_ttl(response.headers) or entity.base_update_interval
            new_update_interval = max(update_interval, entity.base_update_interval)
            if entity.update_interval != new_update_interval:
                logger.info(f'[{entity.name}] next update in {entity.update_interval}')
                entity.update_interval = new_update_interval
        else:
            # restore update interval after backoff on failure
            if entity.update_interval != entity.base_update_interval:
                logger.info(f'[{entity.name}] restoring update interval {entity.update_interval} seconds for {entity.url}')
                entity.update_interval = entity.base_update_interval

        return response

    @abstractmethod
    async def get_records(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        '''Fetch and parse resource, return parsed records, both old and new'''

    @abstractmethod
    def get_record_id(self, record: Record) -> str:
        '''A string that unique identifies a record even if it has changed'''

    def _get_record_id(self, record: Record, entity: BaseFeedMonitorEntity) -> str:
        return '{}:{}'.format(entity.name, self.get_record_id(record))

    async def run(self):
        async with aiohttp.ClientSession() as session:
            for entity in self.entities.values():
                await self.prime_db(entity, session)
        await super().run()

    async def prime_db(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> None:
        '''if feed has no prior records fetch it once and mark all entries as old
        in order to not produce ten messages at once when feed first added'''
        size = self.db.get_size(entity.name)
        if size == 0:
            self.logger.info(f'[{entity.name}] database at "{self.conf.db_path}" has no records for "{entity.name}", assuming first run')
            await self.get_new_records(entity, session)
        else:
            self.logger.info(f'[{entity.name}] {size} records stored in database')

    def store_record(self, record: Record, entity: BaseFeedMonitorEntity):
        uid = self._get_record_id(record, entity)
        parsed_at = datetime.utcnow()
        hashsum = record.hash()
        feed_name = entity.name
        class_name = record.__class__.__name__
        as_json = record.as_json()
        row = {'parsed_at': parsed_at, 'feed_name': feed_name, 'uid': uid, 'hashsum': hashsum, 'class_name': class_name, 'as_json': as_json}
        self.db.store(row)

    def load_record(self, record: Record, entity: BaseFeedMonitorEntity) -> Optional[Record]:
        uid = self._get_record_id(record, entity)
        stored_record = self.db.fetch_row(uid)
        if stored_record is None:
            return None
        stored_record_instance = type(record).model_validate_json(stored_record['as_json'])
        return stored_record_instance

    def record_is_new(self, record: Record, entity: BaseFeedMonitorEntity) -> bool:
        uid = self._get_record_id(record, entity)
        return not self.db.row_exists(uid)

    def record_got_updated(self, record: Record, entity: BaseFeedMonitorEntity) -> bool:
        uid = self._get_record_id(record, entity)
        return self.db.row_exists(uid) and not self.db.row_exists(uid, record.hash())

    def _log_changes(self, record: Record, entity: BaseFeedMonitorEntity):
        normalized_record = type(record).model_validate_json(record.as_json())
        stored_record = self.load_record(record, entity)
        if stored_record is None:
            return
        stored_record_instance = type(record).model_validate_json(stored_record.as_json())
        msg = f'[{entity.name}] fetched record "{self.get_record_id(record)}" (new: {record.hash()[:5]}, old: {stored_record_instance.hash()[:5]}) already exists but has changed:\n'
        self.logger.debug(msg + show_diff(normalized_record.model_dump(), stored_record_instance.model_dump()))

    def filter_new_records(self, records: Sequence[Record], entity: BaseFeedMonitorEntity) -> Sequence[Record]:
        new_records = []
        for record in records:
            if self.record_is_new(record, entity):
                new_records.append(record)
                self.store_record(record, entity)
                self.logger.debug(f'fetched record is new: "{self.get_record_id(record)}" (hash: {record.hash()[:5]})')
            if self.record_got_updated(record, entity):
                self._log_changes(record, entity)
                self.store_record(record, entity)
                self.logger.debug(f'[{entity.name}] storing new version of record "{self.get_record_id(record)}" (hash: {record.hash()[:5]})')
        return new_records

    async def get_new_records(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        records = await self.get_records(entity, session)
        new_records = self.filter_new_records(records, entity)
        return new_records

class RecordDB(utils.RecordDB):
    table_structure = 'parsed_at datetime, feed_name text, uid text, hashsum text, class_name text, as_json text, PRIMARY KEY(uid, hashsum)'
    row_structure = ':parsed_at, :feed_name, :uid, :hashsum, :class_name, :as_json'
    id_field = 'uid'
    exact_id_field = 'hashsum'
    group_id_field = 'feed_name'
    sorting_field = 'parsed_at'

    def store(self, row: Dict[str, Any]) -> None:
        return super().store(row)

    def fetch_row(self, uid: str, hashsum: Optional[str] = None) -> Optional[sqlite3.Row]:
        return super().fetch_row(uid, hashsum)

    def row_exists(self, uid: str, hashsum: Optional[str] = None) -> bool:
        return super().row_exists(uid, hashsum)

    def get_size(self, feed_name: Optional[str] = None) -> int:
        '''return number of records, total or for specified feed, are stored in db'''
        return super().get_size(feed_name)

