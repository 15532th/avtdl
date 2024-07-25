import asyncio
import json
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import aiohttp
from pydantic import Field, FilePath, field_validator

from avtdl.core.db import BaseDbConfig, RecordDB
from avtdl.core.interfaces import ActorConfig, Monitor, MonitorEntity, Record
from avtdl.core.utils import Delay, SessionStorage, get_cache_ttl, get_retry_after, load_cookies, \
    monitor_tasks, monitor_tasks_dict, show_diff

HIGHEST_UPDATE_INTERVAL = 4 * 3600


class TaskMonitorEntity(MonitorEntity):
    update_interval: float
    """how often the monitored source should be checked for new content, in seconds"""


class BaseTaskMonitor(Monitor):

    def __init__(self, conf: ActorConfig, entities: Sequence[TaskMonitorEntity]):
        super().__init__(conf, entities)
        self.tasks: Dict[str, Optional[asyncio.Task]] = {}

    async def run(self):
        startup_tasks = await self.start_cyclic_tasks()
        # keep an eye on possible exceptions in startup process
        self.tasks['startup_tasks_monitor'] = asyncio.create_task(monitor_tasks(startup_tasks, logger=self.logger), name='startup_tasks_monitor')

        await monitor_tasks_dict(self.tasks, logger=self.logger)

    async def start_cyclic_tasks(self) -> Sequence[asyncio.Task]:
        by_entity_interval = defaultdict(list)
        for entity in self.entities.values():
            by_entity_interval[entity.update_interval].append(entity)
        by_group_interval = defaultdict(list)
        for interval, entities in by_entity_interval.items():
            by_group_interval[interval / len(entities)].extend(entities)
        startup_tasks = []
        for interval in sorted(by_group_interval.keys()):
            entities = by_group_interval[interval]
            task = asyncio.create_task(self.start_tasks_for(entities, interval), name=f'start_cyclic_tasks_{interval}')
            startup_tasks.append(task)
        return startup_tasks

    async def start_tasks_for(self, entities, interval):
        logger = self.logger.parent.getChild('scheduler').getChild(self.conf.name)
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
        '''Task for a specific entity that should check for new records based on update_interval and call self.on_record() for each'''


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
    """path to a text file containing cookies in Netscape format"""
    headers: Optional[Dict[str, str]] = {'Accept-Language': 'en-US,en;q=0.9'}
    """custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead"""

    adjust_update_interval: bool = True
    """change delay before next update based on response headers. This setting doesn't affect timeouts after failed requests"""
    base_update_interval: float = Field(exclude=True, default=60)
    """internal variable to persist state between updates. Used to keep update_interval while timeout after update error is active"""
    last_modified: Optional[str] = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to keep Last-Modified header value"""
    etag: Optional[str] = Field(exclude=True, default=None)
    """internal variable to persist state between updates. Used to keep Etag header value"""

    @field_validator('cookies_file')
    @classmethod
    def check_cookies(cls, path: Optional[Path]):
        if path is None:
            return None
        try:
            load_cookies(path, raise_on_error=True)
        except Exception as e:
            raise ValueError(f'{e}') from e
        return path

    def model_post_init(self, __context: Any) -> None:
        self.base_update_interval = self.update_interval


class HttpTaskMonitor(BaseTaskMonitor):
    '''Maintain and provide for records aiohttp.ClientSession objects
    grouped by HttpTaskMonitorEntity.cookies_path, which means entities that use
    the same cookies file will share session'''

    def __init__(self, conf: ActorConfig, entities: Sequence[HttpTaskMonitorEntity]):
        super().__init__(conf, entities)
        self.sessions: SessionStorage = SessionStorage(self.logger)

    async def request_json(self, url: str, entity: HttpTaskMonitorEntity, session: aiohttp.ClientSession, method='GET', headers: Optional[Dict[str, str]] = None, params: Optional[Mapping] = None, data: Optional[Any] = None, data_json: Optional[Any] = None) -> Optional[Any]:
        text = await self.request(url, entity, session, method, headers, params, data, data_json)
        if text is None:
            return None
        try:
            parsed = json.loads(text)
            return parsed
        except json.JSONDecodeError as e:
            self.logger.debug(f'error parsing response from {url}: {e}. Raw response data: "{text}"')
            return None

    async def request(self, url: str, entity: HttpTaskMonitorEntity, session: aiohttp.ClientSession, method='GET', headers: Optional[Dict[str, str]] = None, params: Optional[Mapping] = None, data: Optional[Any] = None, data_json: Optional[Any] = None) -> Optional[str]:
        response = await self.request_raw(url, entity, session, method, headers, params, data, data_json)
        if response is None:
            return None
        return await response.text()

    async def request_raw(self, url: str, entity: HttpTaskMonitorEntity, session: aiohttp.ClientSession, method='GET', headers: Optional[Dict[str, str]] = None, params: Optional[Mapping] = None, data: Optional[Any] = None, data_json: Optional[Any] = None) -> Optional[aiohttp.ClientResponse]:
        '''Helper method to make http request. Does not retry, adjusts entity.update_interval instead'''
        if self.logger.parent is None:
            # should never happen since Actor().logger is constructed with getChild()
            logger = self.logger.getChild('request').getChild(self.conf.name)
        else:
            logger = self.logger.parent.getChild('request').getChild(self.conf.name)
        request_headers: Dict[str, Any] = headers or {}
        if session.headers is not None:
            request_headers.update(session.headers)
        if entity.last_modified is not None and method in ['GET', 'HEAD']:
            request_headers['If-Modified-Since'] = entity.last_modified
        if entity.etag is not None:
            request_headers['If-None-Match'] = entity.etag
        try:
            text = ''
            async with session.request(method, url, headers=request_headers, params=params, data=data, json=data_json) as response:
                # fully read http response to get it cached inside ClientResponse object
                # client code can then use it by awaiting .text() again without causing
                # network activity and potentially triggering associated errors
                text = await response.text()
                response.raise_for_status()
        except Exception as e:
            if isinstance(e, aiohttp.ClientResponseError):
                logger.warning(f'[{entity.name}] got code {e.status} ({e.message}) while fetching {url}')
                if text:
                    logger.debug(f'[{entity.name}] response body: "{text}"')
                retry_after = get_retry_after(response.headers)
                if retry_after is not None:
                    raw_header = response.headers.get("Retry-After")
                    logger.debug(f'[{entity.name}] got Retry-After header with value {raw_header}')
                    entity.update_interval = max(float(retry_after), HIGHEST_UPDATE_INTERVAL)
                    logger.warning(f'[{entity.name}] update interval set to {entity.update_interval} seconds for {url} as requested by response headers')
                    return None
            else:
                logger.warning(f'[{entity.name}] error while fetching {url}: {e.__class__.__name__} {e}')

            update_interval = int(max(Delay.get_next(entity.update_interval), entity.update_interval))
            if entity.update_interval != update_interval:
                entity.update_interval = update_interval
                logger.warning(f'[{entity.name}] update interval set to {entity.update_interval} seconds for {url}')
            return None

        if response.status == 304:
            logger.debug(f'[{entity.name}] got {response.status} ({response.reason}) from {url}')
            if entity.update_interval != entity.base_update_interval:
                logger.info(f'[{entity.name}] restoring update interval {entity.base_update_interval} seconds for {url} after getting 304 response')
                entity.update_interval = entity.base_update_interval
            return None
        # some servers do not have cache headers in 304 response, so only updating on 200
        entity.last_modified = response.headers.get('Last-Modified', None)
        entity.etag = response.headers.get('Etag', None)

        cache_control = response.headers.get('Cache-control')
        logger.debug(f'[{entity.name}] Last-Modified={entity.last_modified or "absent"}, ETAG={entity.etag or "absent"}, Cache-control="{cache_control or "absent"}"')

        if entity.adjust_update_interval:
            new_update_interval = get_cache_ttl(response.headers) or entity.base_update_interval
            new_update_interval = min(new_update_interval, 10 * entity.base_update_interval, HIGHEST_UPDATE_INTERVAL) # in case ttl is overly long
            new_update_interval = max(new_update_interval, entity.base_update_interval)
            if entity.update_interval != new_update_interval:
                entity.update_interval = new_update_interval
                logger.info(f'[{entity.name}] next update in {entity.update_interval}')
        else:
            # restore update interval after backoff on failure
            if entity.update_interval != entity.base_update_interval:
                logger.info(f'[{entity.name}] restoring update interval {entity.base_update_interval} seconds for {url}')
                entity.update_interval = entity.base_update_interval

        return response

    def _get_session(self, entity: HttpTaskMonitorEntity) -> aiohttp.ClientSession:
        session_id = self.sessions.get_session_id(entity.cookies_file, entity.headers)
        session = self.sessions.get_session_by_id(session_id)
        if session is None:
            session = self.sessions.get_session(entity.cookies_file, entity.headers)
        else:
            self.logger.debug(f'[{entity.name}] reusing session with cookies from {session_id}')
        return session

    async def run(self):
        self.sessions.run()
        await super().run()

    async def run_for(self, entity: HttpTaskMonitorEntity):
        try:
            session = self._get_session(entity)
            while True:
                await self.run_once(entity, session)
                await asyncio.sleep(entity.update_interval)
        except Exception:
            self.logger.exception(f'unexpected error in task for entity {entity.name}, task terminated')

    async def run_once(self, entity: TaskMonitorEntity, session: aiohttp.ClientSession):
        records = await self.get_new_records(entity, session)
        for record in records:
            self.on_record(entity, record)

    @abstractmethod
    async def get_new_records(self, entity: TaskMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        '''Produce new records, optionally adjust update_interval'''


class BaseFeedMonitorConfig(BaseDbConfig):
    pass


class BaseFeedMonitorEntity(HttpTaskMonitorEntity):
    url: str
    """url that should be monitored"""

    quiet_start: bool = False
    """throw away new records on the first update after application startup"""
    quiet_first_time: bool = True
    """throw away new records produced on first update of given url"""

class BaseFeedMonitor(HttpTaskMonitor):

    def __init__(self, conf: BaseFeedMonitorConfig, entities: Sequence[BaseFeedMonitorEntity]):
        super().__init__(conf, entities)
        self.db = RecordDB(conf.db_path, logger=self.logger.getChild('db'))

    @abstractmethod
    async def get_records(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        '''Fetch and parse resource, return parsed records, both old and new'''

    async def run(self):
        for entity in self.entities.values():
            session = self._get_session(entity)
            await self.prime_db(entity, session)
        await super().run()

    async def prime_db(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> None:
        '''if a feed has no prior records, fetch it once and mark all entries as old
        in order to not produce ten messages at once when the feed is first added'''
        size = self.db.get_size(entity.name)
        priming_required = False
        if entity.quiet_start:
            self.logger.info(f'[{entity.name}] option "quiet_start" enabled, all records until this moment will be marked as already seen')
            priming_required = True
        elif size == 0:
            self.logger.info(f'[{entity.name}] database at "{self.conf.db_path}" has no records for "{entity.name}", assuming first run')
            if entity.quiet_first_time:
                self.logger.debug(f'[{entity.name}] option "quiet_first_time" enabled, all records until this moment will be marked as already seen')
                priming_required = True
        if priming_required:
            n = len(await self.get_new_records(entity, session))
            self.logger.debug(f'[{entity.name}] number of records that was marked as already seen on first update: {n}')
        else:
            self.logger.info(f'[{entity.name}] {size} records stored in database')

    def store_records(self, records: Sequence[Record], entity: BaseFeedMonitorEntity):
        self.db.store_records(records, entity.name)

    def load_record(self, record: Record, entity: BaseFeedMonitorEntity) -> Optional[Record]:
        return self.db.load_record(record, entity.name)

    def record_is_new(self, record: Record, entity: BaseFeedMonitorEntity) -> bool:
        return not self.db.record_exists(record, entity.name)

    def record_got_updated(self, record: Record, entity: BaseFeedMonitorEntity) -> bool:
        return self.db.record_got_updated(record, entity.name)

    def _log_changes(self, record: Record, entity: BaseFeedMonitorEntity):
        normalized_record = type(record).model_validate_json(record.as_json())
        stored_record = self.load_record(record, entity)
        if stored_record is None:
            return
        stored_record_instance = type(record).model_validate_json(stored_record.as_json())
        msg = f'[{entity.name}] fetched record "{record.get_uid()}" (new: {record.hash()[:5]}, old: {stored_record_instance.hash()[:5]}) already exists but has changed:\n'
        self.logger.debug(msg + show_diff(normalized_record.model_dump(), stored_record_instance.model_dump()))

    def filter_new_records(self, records: Sequence[Record], entity: BaseFeedMonitorEntity) -> Sequence[Record]:
        new_records = []
        for record in records:
            if self.record_is_new(record, entity):
                new_records.append(record)
                self.logger.debug(f'[{entity.name}] fetched record is new: "{record.get_uid()}" (hash: {record.hash()[:5]})')
            if self.record_got_updated(record, entity):
                self._log_changes(record, entity)
                self.logger.debug(f'[{entity.name}] storing new version of record "{record.get_uid()}" (hash: {record.hash()[:5]})')
        self.store_records(records, entity)
        return new_records

    async def get_new_records(self, entity: BaseFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        records = await self.get_records(entity, session)
        new_records = self.filter_new_records(records, entity)
        return new_records


class PagedFeedMonitorConfig(BaseFeedMonitorConfig):
    pass


class PagedFeedMonitorEntity(BaseFeedMonitorEntity):
    max_continuation_depth: int = 10
    """when updating feed with pagination support, only continue for this many pages"""
    next_page_delay: float = 1
    """when updating feed with pagination support, wait this much before loading next page"""
    allow_discontinuity: bool = False # store already fetched records on failure to load one of older pages
    """when updating feed with pagination support, if this setting is enabled and error happens when loading a page, records from already parsed pages will not be dropped. It will allow update of the feed to finish, but older records from deeper pages will then never be parsed on consecutive updates"""
    fetch_until_the_end_of_feed_mode: bool = False
    """when updating feed with pagination support, enables special mode, which makes a monitor try loading and parsing all pages until the end, even if they have been already parsed. Designed for purpose of archiving entire feed content"""

    def model_post_init(self, __context: Any) -> None:
        if self.fetch_until_the_end_of_feed_mode:
            self.quiet_first_time = False
            self.quiet_start = False
        super().model_post_init(__context)


class PagedFeedMonitor(BaseFeedMonitor, ABC):
    '''Provide support for loading and parsing feeds with pagination or lazy loading'''

    @abstractmethod
    async def handle_first_page(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        '''Download and parse first page of the feed

        Returns two-elements tuple with processed records as first element and
        anything required to load and process next page as second.

        If loading or parsing page failed, warning is issued using self.logger, update_interval
        adjusted if required (by using self.request to fetch data or manually)
        and first element of the returned tuple is None.

        If there is no next page or there is no new records, or limit of continuation depth reached,
        then first element is an empty list and the second element is None'''

    @abstractmethod
    async def handle_next_page(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession, context: Optional[Any]) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        '''Download and parse continuation  page

        Parameters:
            entity (PagedFeedMonitorEntity): working entity
            session (aiohttp.ClientSession): session object to make requests with
            context (Optional[Any]): any data required to load next page, such as continuation token
        Returns same values as handle_first_page'''

    async def get_records(self, entity: PagedFeedMonitorEntity, session: aiohttp.ClientSession) -> Sequence[Record]:
        records: List[Record] = []
        current_page_records, continuation_context = await self.handle_first_page(entity, session)
        if current_page_records is None:
            return []
        records.extend(current_page_records)

        if entity.fetch_until_the_end_of_feed_mode:
            self.logger.info(
                f'[{entity.name}] "fetch_until_the_end_of_feed_mode" setting is enabled, will keep loading through already seen pages until the end. Disable it in config after it succeeds once')

        current_page = 1
        while True:
            if continuation_context is None:
                self.logger.debug(f'[{entity.name}] no continuation link on {current_page - 1} page, end of feed reached')
                if entity.fetch_until_the_end_of_feed_mode:
                    self.logger.info(f'[{entity.name}] reached the end of the feed at {current_page - 1} page, fetch_until_the_end_of_feed_mode can be disabled now')
                    entity.fetch_until_the_end_of_feed_mode = False
                break
            if not entity.fetch_until_the_end_of_feed_mode:
                if current_page > entity.max_continuation_depth:
                    self.logger.info(
                        f'[{entity.name}] reached continuation limit of {entity.max_continuation_depth}, aborting update')
                    break
                if not all(self.record_is_new(record, entity) for record in current_page_records):
                    self.logger.debug(f'[{entity.name}] found already stored records on {current_page - 1} page')
                    break
            self.logger.debug(f'[{entity.name}] all records on page {current_page - 1} are new, loading next one')

            current_page_records, continuation_context = await self.handle_next_page(entity, session, continuation_context)
            if current_page_records is None:
                if entity.allow_discontinuity or entity.fetch_until_the_end_of_feed_mode:
                    # when unable to load _all_ new records, return at least current progress
                    break
                else:
                    # when unable to load _all_ new records, throw away all already parsed and return nothing
                    # to not cause discontinuity in stored data
                    return []
            records.extend(current_page_records)
            self.logger.debug(f'[{entity.name}] while parsing page {current_page} got {len(current_page_records)} records')

            current_page += 1
            await asyncio.sleep(entity.next_page_delay)

        records = records[::-1]
        return records
