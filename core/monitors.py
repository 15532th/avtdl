import asyncio
from datetime import datetime
import json
import sqlite3
from abc import abstractmethod
from collections import defaultdict
import logging
from typing import Sequence, Dict, Optional, Any

import aiohttp
from pydantic import FilePath, PrivateAttr

from core import utils
from core.interfaces import ActorEntity, Actor, ActorConfig, Record
from core.utils import get_cache_ttl


class TaskMonitorEntity(ActorEntity):
    update_interval: float


class BaseTaskMonitor(Actor):

    def __init__(self, conf: ActorConfig, entities: Sequence[TaskMonitorEntity]):
        super().__init__(conf, entities)
        self.tasks: Dict[str, asyncio.Task] = {}

    def handle(self, entity: ActorEntity, record: Record) -> None:
        self.logger.warning(f'TaskMonitor({self.conf.name}, {entity.name}) got Record despite not expecting any, might be sign of possible misconfiguration. Record: {record}')

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
        logger.info(f'will start {len(entities)} tasks with {entities[0].update_interval} update interval and {interval} offset for {names}')
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
    db_path: str = ':memory:'

class BaseFeedMonitorEntity(HttpTaskMonitorEntity):
    url: str
    adjust_update_interval: bool = True
    base_update_interval: PrivateAttr = None

    def model_post_init(self, __context: Any) -> None:
        self.base_update_interval = self.update_interval

class BaseFeedMonitor(HttpTaskMonitor):

    def __init__(self, conf: BaseFeedMonitorConfig, entities: Sequence[BaseFeedMonitorEntity]):
        super().__init__(conf, entities)
        self.db = RecordDB(conf.db_path, logger=self.logger.getChild('db'))

    async def request(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession, method='GET') -> Optional[aiohttp.ClientResponse]:
        '''Helper method to make http request. Does not retry, adjust entity.update_interval instead'''
        try:
            async with session.request(method, entity.url) as response:
                response.raise_for_status()
                # fully read http response to get it cached inside ClientResponse object
                # client code can then use it by awaiting .text() again without causing
                # network activity and potentially triggering associated errors
                _ = await response.text()
        except Exception as e:
            if isinstance(e, aiohttp.ClientResponseError):
                self.logger.warning(f'[{entity.name}] got code {e.status} ({e.message}) while fetching {entity.url}')
            else:
                self.logger.warning(f'[{entity.name}] error while fetching {entity.url}: {e}')

            update_interval = min(entity.update_interval * 2, entity.base_update_interval * 10, 4*3600)
            if entity.update_interval != update_interval:
                entity.update_interval = update_interval
                self.logger.warning(f'update interval set to {entity.update_interval} seconds for {entity.name} ({entity.url})')
            return None

        if entity.adjust_update_interval:
            update_interval = get_cache_ttl(response.headers) or entity.base_update_interval
            new_update_interval = max(update_interval, entity.base_update_interval)
            if entity.update_interval != new_update_interval:
                self.logger.debug(f'[{entity.name}] next update in {entity.update_interval}')
                entity.update_interval = new_update_interval
        else:
            # restore update interval after backoff on failure
            if entity.update_interval != entity.base_update_interval:
                self.logger.info(f'restoring update interval {entity.update_interval} seconds for {entity.name} ({entity.url})')
                entity.update_interval = entity.base_update_interval

        return response

    @abstractmethod
    async def get_records(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        '''Fetch and parse resource, return parsed records, both old and new'''

    @abstractmethod
    def get_record_id(self, record: Record) -> str:
        '''A string that unique identifies a record even if it has changed'''

    async def prime_db(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> None:
        '''if feed has no prior records fetch it once and mark all entries as old
        in order to not produce ten messages at once when feed first added'''
        if self.db.get_size(entity.name) == 0:
            self.logger.debug(f'[{entity.name}] database at "{self.conf.db_path}" has no records for "{entity.name}", assuming first run')
            await self.get_new_records(entity, session)

    def store_record(self, record: Record, entity: BaseFeedMonitorEntity):
        uid = self.get_record_id(record)
        parsed_at = datetime.utcnow()
        hashsum = record.hash()
        feed_name = entity.name
        class_name = record.__class__.__name__
        as_json = record.as_json()
        self.db.store(parsed_at, feed_name, uid, hashsum, class_name, as_json)

    def record_is_new(self, record: Record, entity: BaseFeedMonitorEntity) -> bool:
        uid = self.get_record_id(record)
        record_hash = record.hash()
        stored_record = self.db.fetch_row(uid)
        exists = stored_record is not None
        if exists:
            stored_record_instance = type(record).model_validate_json(stored_record['as_json'])
        if not self.db.row_exists(uid, record_hash):
            self.store_record(record, entity)
        return not exists

    def filter_new_records(self, records: Sequence[Record], entity: BaseFeedMonitorEntity) -> Sequence[Record]:
        new_records = []
        for record in records:
            if self.record_is_new(record, entity):
                new_records.append(record)
        return new_records

    async def get_new_records(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        records = await self.get_records(entity, session)
        new_records = self.filter_new_records(records, entity)
        return new_records

class RecordDB:

    def __init__(self, db_path, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger('RecordDB')
        try:
            self.db = sqlite3.connect(db_path)
            self.db.row_factory = sqlite3.Row
            self.cursor = self.db.cursor()
            record_structure = 'parsed_at datetime, feed_name text, uid text, hashsum text, class_name text, as_json text, PRIMARY KEY(uid, hashsum)'
            self.cursor.execute('CREATE TABLE IF NOT EXISTS records ({})'.format(record_structure))
            self.db.commit()
        except sqlite3.OperationalError as e:
            self.logger.error(
                f'error opening sqlite database at path "{db_path}", specified in "db_path" config variable: {e}. If file exists make sure it was produced by this application, otherwise check if new file can be created at specified location. Alternatively use special value ":memory:" to use in-memory database instead.')
            raise
        else:
            self.logger.debug(f'successfully connected to sqlite database at "{db_path}"')

    def store(self, parsed_at: datetime, feed_name: str, uid: str, hashsum: str, class_name: str, as_json: str) -> None:
        sql = 'INSERT INTO records VALUES(:parsed_at, :feed_name, :uid, :hashsum, :class_name, :as_json)'
        row = {'parsed_at': parsed_at, 'feed_name': feed_name, 'uid': uid, 'hashsum': hashsum, 'class_name': class_name, 'as_json': as_json}
        self.cursor.execute(sql, row)
        self.db.commit()

    def fetch_row(self, uid: str, hashsum: Optional[str] = None) -> Optional[sqlite3.Row]:
        if hashsum is not None:
            sql = "SELECT * FROM records WHERE uid=:uid AND hashsum=:hashsum ORDER BY parsed_at DESC LIMIT 1"
        else:
            sql = "SELECT * FROM records WHERE uid=:uid ORDER BY parsed_at DESC LIMIT 1"
        keys = {'uid': uid, 'hashsum': hashsum}
        self.cursor.execute(sql, keys)
        return self.cursor.fetchone()

    def row_exists(self, uid: str, hashsum: Optional[str] = None) -> bool:
        return self.fetch_row(uid, hashsum) is not None

    def get_size(self, feed_name: Optional[str] = None) -> int:
        '''return number of records, total or for specified feed, are stored in db'''
        if feed_name is None:
            sql = 'SELECT COUNT(1) FROM records'
        else:
            sql = 'SELECT COUNT(1) FROM records WHERE feed_name=:feed_name'
        keys = {'feed_name': feed_name}
        self.cursor.execute(sql, keys)
        return int(self.cursor.fetchone()[0])

