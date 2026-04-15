import asyncio
import datetime
import logging
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

import dateutil.parser
from pydantic import Field, FilePath, PositiveInt, SerializeAsAny, field_validator

from avtdl.core.actions import TaskAction, TaskActionConfig, TaskActionEntity
from avtdl.core.cookies import AnotherCookieJar, AnotherCurlCffiCookieJar, CookieStoreError, \
    load_cookies, save_cookies
from avtdl.core.db import BaseDbConfig, RecordDB
from avtdl.core.interfaces import Event, EventType, Record
from avtdl.core.plugins import Plugins
from avtdl.core.request import HttpClient, RetrySettings, StateStorage
from avtdl.core.runtime import RuntimeContext, TaskStatus
from avtdl.core.utils import JSONType, utcnow
from avtdl.plugins.withny.extractors import WithnyRecord, parse_live_record, parse_schedule_record


@Plugins.register('withny.live', Plugins.kind.ASSOCIATED_RECORD)
class WithnyLiveErrorEvent(Event):
    """Produced on failure to process a livestream"""
    event_type: str = EventType.error
    """text describing the nature of event, can be used to filter classes of events, such as errors"""
    record: SerializeAsAny[Optional[WithnyRecord]] = Field(exclude=True)
    """record that was being processed when this event happened"""

    def __str__(self):
        return f'Processing stream {self.record.stream_id} failed: {self.text}\n[{self.record.name}] {self.record.title}\n{self.record.url}'


def timestamp_now_ms() -> float:
    return datetime.datetime.now().timestamp() * 1000


class SessionToken:

    def __init__(self, access_token: str, expires: datetime.datetime):
        self.access_token = access_token
        self.expires = expires

    def expired(self) -> bool:
        return self.expires <= utcnow()

    @classmethod
    def from_json(cls, data: dict) -> 'SessionToken':
        expires = dateutil.parser.parse(data['expires'])
        access_token = data['accessToken']
        return cls(access_token=access_token, expires=expires)


def has_expired(expiration_timestamp: str) -> bool:
    return int(expiration_timestamp) < timestamp_now_ms() + 60000


def check_auth_cookies(jar: AnotherCookieJar):
    """Check if client's cookie jar contains authorization cookies that look valid, raise otherwise"""
    auth_cookies = ['__Secure-next-auth.session-token', '__Host-next-auth.csrf-token']
    old_auth_cookies = ['auth._token.local', 'auth._refresh_token.local']
    if not all(
        jar.get(name) is not None for name in auth_cookies
    ) and any(
        jar.get(name) is not None for name in old_auth_cookies
    ):
        raise ValueError(f'Cookies are missing login information. Export new cookies from a browser while logged in.')
    else:
        return


async def refresh_session(client: HttpClient, logger: logging.Logger) -> Optional[SessionToken]:
    """Request new session token, store next-auth cookies in client's cookie jar"""
    try:
        check_auth_cookies(client.cookie_jar)
    except ValueError as e:
        logger.warning(f'failed to get session token: {e}')
        return None
    url = 'https://www.withny.fun/api/auth/session'
    headers = {'Referer': 'https://www.withny.fun/',
               'Origin': 'https: // www.withny.fun',
               'Content-Type': 'application/json'}
    result = await client.request_json(url, method='GET', headers=headers, settings=RetrySettings(retry_times=1))
    if result is None:
        return None
    try:
        if not isinstance(result, dict):
            raise ValueError('unexpected response format')
        new_auth = SessionToken.from_json(result)
        return new_auth
    except Exception as e:
        logger.exception(
            f'[login] failed to get session key: error when parsing response: {type(e)}, {e}. Raw response: {result}')
        return None


async def fetch_stream_url(client: HttpClient,
                           logger: logging.Logger,
                           stream_id: str,
                           session: SessionToken) -> Optional[str]:
    url = f'https://www.withny.fun/api/streams/{stream_id}/playback-url'
    headers = {'Referer': url, 'Authorization': f'Bearer {session.access_token}'}
    result = await client.request_json(url, headers=headers, settings=RetrySettings(retry_times=2))
    if result is None:
        return None
    if not isinstance(result, str):
        logger.warning(f'unexpected stream url format: {result}')
        return None
    return result


def find_stream_data(data: Optional[JSONType], stream_id: str) -> Optional[dict]:
    if data is None:
        return None
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict):
            continue
        item_id = item.get('uuid')
        if stream_id != item_id:
            continue
        return item
    return None


Plugins.register('withny.live', Plugins.kind.ASSOCIATED_RECORD)(WithnyRecord)


@Plugins.register('withny.live', Plugins.kind.ACTOR_CONFIG)
class WithnyLiveConfig(TaskActionConfig, BaseDbConfig):
    pass


@Plugins.register('withny.live', Plugins.kind.ACTOR_ENTITY)
class WithnyLiveEntity(TaskActionEntity):
    cookies_file: FilePath
    """path to a text file containing cookies in Netscape format"""
    poll_interval: PositiveInt = 60
    """how often live status of the stream that should have started by now is updated, in seconds"""
    poll_attempts: PositiveInt = 30
    """how many times live status of the stream that should have started by now is updated before giving up"""

    @field_validator('cookies_file')
    @classmethod
    def check_cookies(cls, path: FilePath):
        try:
            jar = load_cookies(path, raise_on_error=True)
        except Exception as e:
            raise ValueError(f'{e}') from e
        assert jar is not None
        another_jar = AnotherCurlCffiCookieJar.from_cookie_jar(jar)
        try:
            check_auth_cookies(another_jar)
        except ValueError as e:
            raise ValueError(f'\n    {e}')
        return path


@Plugins.register('withny.live', Plugins.kind.ACTOR)
class WithnyLive(TaskAction):
    """
    Wait for livestream on Withny

    If incoming record comes from the "withny" monitor and represents an ongoing or upcoming livestream,
    waits for the stream start and tries to fetch direct `playlist_url` of the stream, then emits updated record
    down the chain. Number and frequency of attempts is limited by `poll_attempts` and `poll_interval` settings.

    Requires cookies from a logged in account to work.
    """
    def __init__(self, conf: WithnyLiveConfig, entities: Sequence[WithnyLiveEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: WithnyLiveConfig
        self.state_storage = StateStorage()
        self.db = RecordDB(conf.db_path, logger=self.logger.getChild('db'))
        self.sessions: Dict[str, SessionToken] = {}

    async def request_json(self, url: str,
                           base_update_interval: float,
                           current_update_interval: float,
                           client: HttpClient, method='GET',
                           headers: Optional[Dict[str, str]] = None,
                           params: Optional[Any] = None,
                           data: Optional[Any] = None,
                           data_json: Optional[Any] = None) -> Tuple[Optional[JSONType], float]:
        '''Helper method to make http request to a json endpoint'''
        state = self.state_storage.get(url, method, params)
        response = await client.request(url, params, data, data_json, headers, method, state)
        update_interval = response.next_update_interval(base_update_interval, current_update_interval, True)
        if response.has_json():
            data = response.json()
        else:
            data = None
        return data, update_interval

    def parse_record(self, data: Optional[JSONType], parser: Callable) -> Optional[WithnyRecord]:
        if data is None or not isinstance(data, dict):
            return None
        try:
            record = parser(data)
            return record
        except Exception as e:
            self.logger.exception(f'failed to parse stream record: {e}')
            self.logger.debug(f'raw record data: {data}')
            return None

    async def fetch_updated_record(self,
                                   client: HttpClient,
                                   record: WithnyRecord,
                                   update_interval: float,
                                   base_update_interval: float) -> Tuple[Optional[WithnyRecord], float]:
        updated_record = None
        if record.schedule_id is not None:
            if record.scheduled is not None and record.scheduled > utcnow():
                self.logger.debug(f'stream {record.stream_id} is scheduled, using schedule_id')
                url = f'https://www.withny.fun/api/schedules/{record.schedule_id}'
                maybe_record_data, update_interval = await self.request_json(url, base_update_interval,
                                                                             update_interval, client)
                updated_record = self.parse_record(maybe_record_data, parse_schedule_record)
        if record.schedule_id is None or updated_record is None:
            self.logger.debug(f'stream {record.stream_id} is not scheduled or schedule update failed, using stream_id')
            url = f'https://www.withny.fun/api/streams/with-rooms?username={record.username}'
            maybe_data, update_interval = await self.request_json(url, base_update_interval, update_interval, client)
            maybe_record_data = find_stream_data(maybe_data, record.stream_id)
            updated_record = self.parse_record(maybe_record_data, parse_live_record)
            if updated_record is not None:
                if updated_record.schedule_id is not None or updated_record.scheduled is not None:
                    self.logger.warning(f'record {updated_record} should be live but is scheduled: id={updated_record.schedule_id}, scheduled={updated_record.scheduled}')

        return updated_record, update_interval

    async def ensure_session(self, client: HttpClient, entity: WithnyLiveEntity) -> Optional[SessionToken]:
        session = self.sessions.get(entity.name)
        if session is not None and not session.expired():
            return session
        new_session = await refresh_session(client, self.logger)
        if new_session is None:
            return None
        self.logger.debug(f'[{entity.name}] storing refreshed cookies to "{entity.cookies_file}"')
        try:
            save_cookies(client.cookie_jar, str(entity.cookies_file))
        except CookieStoreError as e:
            self.logger.warning(f'[{entity.name}] {e}')
        return new_session

    async def handle_record_task(self, logger: logging.Logger, client: HttpClient, entity: WithnyLiveEntity,
                                 record: Record, info: TaskStatus) -> None:
        if not isinstance(record, WithnyRecord):
            return

        # wait for the stream to go live, return if there is no point waiting anymore
        update_interval = float(entity.poll_interval)
        for attempt in range(entity.poll_attempts):
            await asyncio.sleep(update_interval)
            msg = f'stream {record.stream_id} by {record.username}, attempt {attempt}: fetching live status'
            self.logger.debug(msg)
            updated_record, update_interval = await self.fetch_updated_record(client, record, update_interval,
                                                                              entity.poll_interval)
            if updated_record is None:
                continue

            updated_record.origin = record.origin
            updated_record.chain = record.chain
            record = updated_record

            if record.end is not None:
                self.logger.debug(f'stream {record.stream_id} has ended at {record.end}: {record!r}')
                msg = 'stream has already ended'
                self.on_record(entity, WithnyLiveErrorEvent(text=msg, record=record))
                return
            elif record.start is not None:
                self.logger.debug(f'stream {record.stream_id}, attempt {attempt}: {record.username} is live')
                break
            elif record.scheduled is None:
                self.logger.debug(
                    f'stream {record.stream_id} has neither start nor scheduled date, dropping record {record!r}')
                msg = 'stream has neither start nor scheduled date'
                self.on_record(entity, WithnyLiveErrorEvent(text=msg, record=record))
                return
            else:
                self.logger.debug(f'stream {record.stream_id}, attempt {attempt}: scheduled {record.scheduled}')
                time_left = record.scheduled - datetime.datetime.now(datetime.timezone.utc)
                delay = time_left.total_seconds()
                if delay > 0:
                    msg = f'stream {record.stream_id} is scheduled to {record.scheduled}, waiting for {time_left}: {record!r}'
                    self.logger.debug(msg)
                    msg = f'waiting for stream to start in {time_left}'
                    info.set_status(msg)
                else:
                    delay = entity.poll_interval
                    msg = f'[{attempt}/{entity.poll_attempts}] waiting for stream to start, next check in {delay:.0f}'
                    info.set_status(msg, record)
                update_interval = delay

        else:
            self.logger.debug(
                f'stream {record.stream_id} is not live after {entity.poll_attempts} checks, dropping record {record!r}')
            msg = f"stream didn't go live after {entity.poll_interval * entity.poll_attempts} seconds"
            self.on_record(entity, WithnyLiveErrorEvent(text=msg, record=record))
            return

        # stream is now live for sure, try fetching stream_url
        auth = await self.ensure_session(client, entity)
        if not auth:
            self.logger.warning(f'login failed, aborting processing')
            self.on_record(entity, WithnyLiveErrorEvent(text='login failed', record=record))
            return
        stream_url = await fetch_stream_url(client, self.logger, record.stream_id, auth)
        if stream_url is None:
            self.logger.warning(f'failed to fetch stream_url for stream {record.stream_id}')
            self.on_record(entity, WithnyLiveErrorEvent(text='retrieving playlist url failed', record=record))
            return

        record.playlist_url = stream_url
        self.db.store_records([record], entity.name)
        self.on_record(entity, record)
