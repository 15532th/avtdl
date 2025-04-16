import asyncio
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import aiohttp
from pydantic import Field, FilePath, PositiveFloat, field_serializer, field_validator

from avtdl.core.db import BaseDbConfig, RecordDB, RecordDbView
from avtdl.core.interfaces import AbstractRecordsStorage, ActorConfig, Monitor, MonitorEntity, Record, RuntimeContext
from avtdl.core.request import HttpClient, HttpResponse, StateStorage, decide_on_update_interval
from avtdl.core.utils import JSONType, SessionStorage, load_cookies, show_diff, with_prefix

HIGHEST_UPDATE_INTERVAL = 4 * 3600


class TaskMonitorEntity(MonitorEntity):
    update_interval: PositiveFloat
    """how often the monitored source should be checked for new content, in seconds"""


class BaseTaskMonitor(Monitor):

    def __init__(self, conf: ActorConfig, entities: Sequence[TaskMonitorEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)

    async def run(self):
        await self.start_cyclic_tasks()

    async def start_cyclic_tasks(self):
        by_entity_interval = defaultdict(list)
        for entity in self.entities.values():
            by_entity_interval[entity.update_interval].append(entity)
        by_group_interval = defaultdict(list)
        for interval, entities in by_entity_interval.items():
            by_group_interval[interval / len(entities)].extend(entities)
        for interval in sorted(by_group_interval.keys()):
            entities = by_group_interval[interval]
            _ = self.controller.create_task(self.start_tasks_for(entities, interval), name=f'start_cyclic_tasks_{interval}')

    async def start_tasks_for(self, entities: List[TaskMonitorEntity], interval: float) -> None:
        assert self.logger.parent is not None
        logger = self.logger.parent.getChild('scheduler').getChild(self.conf.name)
        if len(entities) == 0:
            logger.debug(f'called with no entities and {interval} interval')
            return
        names = ', '.join([f'{self.conf.name}.{entity.name}' for entity in entities])
        logger.info(f'will start {len(entities)} tasks with {entities[0].update_interval:.1f} update interval and {interval:.1f} offset for {names}')
        for entity in entities:
            logger.debug(f'starting task {entity.name} with {entity.update_interval} update interval')
            _ = self.controller.create_task(self.run_for(entity), name=f'{self.conf.name}:{entity.name}')
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
    """custom HTTP headers as "key": value" pairs. "Set-Cookie" header will be ignored, use `cookies_file` option instead. "Etag" and "Last-Modified" are set automatically if available in server response. Plugin might also overwrite other headers required to make requests to a specific endpoint"""

    adjust_update_interval: bool = True
    """change delay before the next update based on response headers. This setting doesn't affect timeouts after failed requests"""
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

    @field_serializer('update_interval')
    def restore_update_interval(self, _: float) -> float:
        return self.base_update_interval

    def model_post_init(self, __context: Any) -> None:
        self.base_update_interval = self.update_interval


class HttpTaskMonitor(BaseTaskMonitor):
    '''Maintain and provide for records aiohttp.ClientSession objects
    grouped by HttpTaskMonitorEntity.cookies_path, which means entities that use
    the same cookies file will share session'''

    def __init__(self, conf: ActorConfig, entities: Sequence[HttpTaskMonitorEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.sessions: SessionStorage = SessionStorage(self.logger)
        self.state_storage = StateStorage()

    async def request_json(self, url: str, entity: HttpTaskMonitorEntity, client: HttpClient, method='GET', headers: Optional[Dict[str, str]] = None, params: Optional[Any] = None, data: Optional[Any] = None, data_json: Optional[Any] = None) -> Optional[JSONType]:
        response = await self.request_raw(url, entity, client, method, headers, params, data, data_json)
        if response is None or response.no_content:
            return None
        return response.json()

    async def request(self, url: str, entity: HttpTaskMonitorEntity, client: HttpClient, method='GET', headers: Optional[Dict[str, str]] = None, params: Optional[Any] = None, data: Optional[Any] = None, data_json: Optional[Any] = None) -> Optional[str]:
        response = await self.request_raw(url, entity, client, method, headers, params, data, data_json)
        if response is None or response.no_content:
            return None
        return response.text

    async def request_raw(self, url: str, entity: HttpTaskMonitorEntity, client: HttpClient, method='GET', headers: Optional[Dict[str, str]] = None, params: Optional[Any] = None, data: Optional[Any] = None, data_json: Optional[Any] = None) -> Optional[HttpResponse]:
        '''Helper method to make http request. Does not retry, adjusts entity.update_interval instead'''
        state = self.state_storage.get(url, method, params)
        response = await client.request(url, params, data, data_json, headers, method, state)
        if response is None:
            entity.update_interval = decide_on_update_interval(client.logger, url, None, None, entity.update_interval, entity.base_update_interval, entity.adjust_update_interval)
        else:
            entity.update_interval = response.next_update_interval(entity.base_update_interval, entity.update_interval, entity.adjust_update_interval)
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
        name = f'ensure_closed for {self.logger.name} ({self!r})'
        _ = self.controller.create_task(self.sessions.ensure_closed(), name=name)
        await super().run()

    async def run_for(self, entity: HttpTaskMonitorEntity):
        try:
            session = self._get_session(entity)
            if self.logger.parent is not None:
                logger = self.logger.parent.getChild('request').getChild(self.conf.name)
            else:
                logger = self.logger.getChild('request') # should never happen
            logger = with_prefix(logger, f'[{entity.name}]')
            client = HttpClient(logger, session)
            while True:
                await self.run_once(entity, client)
                await asyncio.sleep(entity.update_interval)
        except Exception:
            self.logger.exception(f'unexpected error in task for entity {entity.name}, task terminated')

    async def run_once(self, entity: TaskMonitorEntity, client: HttpClient):
        records = await self.get_new_records(entity, client)
        for record in records:
            self.on_record(entity, record)

    @abstractmethod
    async def get_new_records(self, entity: TaskMonitorEntity, client: HttpClient) -> Sequence[Record]:
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

    def __init__(self, conf: BaseFeedMonitorConfig, entities: Sequence[BaseFeedMonitorEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: BaseFeedMonitorConfig = conf
        self.db = RecordDB(conf.db_path, logger=self.logger.getChild('db'))

    @abstractmethod
    async def get_records(self, entity: BaseFeedMonitorEntity, client: HttpClient) -> Sequence[Record]:
        '''Fetch and parse resource, return parsed records, both old and new'''

    async def run(self):
        for entity in self.entities.values():
            session = self._get_session(entity)
            if self.logger.parent is not None:
                logger = self.logger.parent.getChild('request').getChild(self.conf.name)
            else:
                logger = self.logger.getChild('request') # should never happen
            logger = with_prefix(logger, f'[{entity.name}]')
            client = HttpClient(logger, session)
            await self.prime_db(entity, client)
        await super().run()

    async def prime_db(self, entity: BaseFeedMonitorEntity, client: HttpClient) -> None:
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
            n = len(await self.get_new_records(entity, client))
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
        records_to_store = []
        for record in records:
            if self.record_is_new(record, entity):
                new_records.append(record)
                records_to_store.append(record)
                self.logger.debug(f'[{entity.name}] fetched record is new: "{record.get_uid()}" (hash: {record.hash()[:5]})')
            elif self.record_got_updated(record, entity):
                records_to_store.append(record)
                self._log_changes(record, entity)
                self.logger.debug(f'[{entity.name}] storing new version of record "{record.get_uid()}" (hash: {record.hash()[:5]})')
        self.store_records(records_to_store, entity)
        return new_records

    async def get_new_records(self, entity: BaseFeedMonitorEntity, client: HttpClient) -> Sequence[Record]:
        records = await self.get_records(entity, client)
        new_records = self.filter_new_records(records, entity)
        return new_records

    def get_records_storage(self, entity_name: Optional[str] = None) -> Optional[AbstractRecordsStorage]:
        return RecordDbView(self.db, entity_name)


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
    async def handle_first_page(self, entity: PagedFeedMonitorEntity, client: HttpClient) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        '''Download and parse first page of the feed

        Returns two-elements tuple with processed records as first element and
        anything required to load and process next page as second.

        If loading or parsing page failed, warning is issued using self.logger, update_interval
        adjusted if required (by using self.request to fetch data or manually)
        and first element of the returned tuple is None.

        If there is no next page or there is no new records, or limit of continuation depth reached,
        then first element is an empty list and the second element is None'''

    @abstractmethod
    async def handle_next_page(self, entity: PagedFeedMonitorEntity, client: HttpClient, context: Optional[Any]) -> Tuple[Optional[Sequence[Record]], Optional[Any]]:
        '''Download and parse continuation  page

        Parameters:
            entity (PagedFeedMonitorEntity): working entity
            client (HttpClient): request.HttpClient to make requests with
            context (Optional[Any]): any data required to load next page, such as continuation token
        Returns same values as handle_first_page'''

    async def get_records(self, entity: PagedFeedMonitorEntity, client: HttpClient) -> Sequence[Record]:
        records: List[Record] = []
        current_page_records, continuation_context = await self.handle_first_page(entity, client)
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

            current_page_records, continuation_context = await self.handle_next_page(entity, client,
                                                                                     continuation_context)
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
