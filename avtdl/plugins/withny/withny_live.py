import asyncio
import datetime
import json
import logging
import urllib.parse
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from aiohttp.abc import AbstractCookieJar
from pydantic import FilePath, PositiveInt, field_validator

from avtdl.core.db import BaseDbConfig, RecordDB
from avtdl.core.interfaces import Action, ActionEntity, Record, RuntimeContext
from avtdl.core.plugins import Plugins
from avtdl.core.request import Delay, HttpClient, RetrySettings, StateStorage
from avtdl.core.utils import JSONType, SessionStorage, get_cookie_value, jwt_decode, load_cookies
from avtdl.plugins.withny.extractors import WithnyRecord, parse_live_record, parse_schedule_record


class AuthToken:
    local_names = {
        'token': 'auth._token.local',
        'token_expiration': 'auth._token_expiration.local',
        'refresh_token': 'auth._refresh_token.local',
        'refresh_token_expiration': 'auth._refresh_token_expiration.local'
    }

    def __init__(self, token: str, token_expiration: str, refresh_token: str, refresh_token_expiration: str):
        self.token = token
        self.token_expiration = token_expiration
        self.refresh_token = refresh_token
        self.refresh_token_expiration = refresh_token_expiration

    def __repr__(self):
        return f'AuthToken(token={self.token}, token_expiration={self.token_expiration}, self.refresh_token={self.refresh_token}, self.refresh_token_expiration={self.refresh_token_expiration})'

    def expired(self) -> bool:
        return int(self.token_expiration) > int(datetime.datetime.now().timestamp() * 1000)

    def refreshable(self) -> bool:
        return int(self.refresh_token_expiration) > int(datetime.datetime.now().timestamp() * 1000)

    @property
    def plain_token(self) -> str:
        return urllib.parse.unquote(self.token)

    def set_cookies(self, jar: AbstractCookieJar):
        cookies: SimpleCookie = SimpleCookie()
        for k, v in self.local_names.items():
            cookies[v] = getattr(self, k)
            cookies[v]['domain'] = 'www.withny.fun'
            cookies[v]['path'] = '/'
        jar.update_cookies(cookies)

    @classmethod
    def from_login_data(cls, data: Dict[str, Any]) -> 'AuthToken':
        token = urllib.parse.quote(f'{data["tokenType"]} {data["token"]}')
        token_content = jwt_decode(data['token'])
        token_expiration = str(token_content['exp'] * 1000)
        refresh_token = data['refreshToken']
        refresh_token_expiration = str(int(token_expiration) + int(datetime.timedelta(days=29).total_seconds() * 1000))
        return cls(
            token,
            token_expiration,
            refresh_token,
            refresh_token_expiration
        )

    @classmethod
    def from_cookies(cls, jar: AbstractCookieJar) -> 'AuthToken':
        data = {data_key: get_cookie_value(jar, cookie_key) for data_key, cookie_key in cls.local_names.items()}
        for k, v in data.items():
            if v is None:
                raise ValueError(f'{k} value is missing from the jar')
        return cls(**data)


def has_expired(expiration_timestamp: str) -> bool:
    ts = int(expiration_timestamp)
    expiration_date = datetime.datetime.fromtimestamp(ts, tz=None)
    now = datetime.datetime.now(tz=None)
    return expiration_date > now + datetime.timedelta(minutes=1)


async def ensure_login(client: HttpClient, logger: logging.Logger) -> bool:
    try:
        auth = AuthToken.from_cookies(client.cookie_jar)
    except ValueError:
        auth = None
    if auth is None:
        logger.warning(f'failed to login: error loading data from cookies')
        return False
    elif has_expired(auth.refresh_token_expiration):
        # no point checking auth token here, since it does not outlive refresh token
        logger.warning(f'failed to login: cookies has expired')
        return False
    elif has_expired(auth.token_expiration):
        ok = await refresh_auth(client, logger)
        return ok
    else:
        # valid auth token is already present in cookies
        return True


async def perform_login(client: HttpClient, logger: logging.Logger, username: str, password: str) -> bool:
    """Make request to log in, update session's cookie jar"""
    url = 'https://www.withny.fun/api/auth/login'
    data = json.dumps({'email': username, 'password': password})
    headers = {'Referer': 'https://www.withny.fun/login', 'Content-Type': 'application/json'}
    return await make_auth_request(client, logger, url, data, headers)


async def refresh_auth(client: HttpClient, logger: logging.Logger) -> bool:
    """Make request to refresh auth token, update client's cookie jar"""
    try:
        auth = AuthToken.from_cookies(client.cookie_jar)
    except Exception as e:
        logger.debug(f'[login] failed to refresh token: error when parsing cookies: {type(e)}, {e}')
        return False
    url = 'https://www.withny.fun/api/auth/token'
    data = json.dumps({'refreshToken': auth.refresh_token})
    headers = {'Referer': 'https://www.withny.fun/', 'Content-Type': 'application/json'}
    return await make_auth_request(client, logger, url, data, headers)


async def make_auth_request(client: HttpClient, logger: logging.Logger, url: str, data,
                            headers: Optional[dict] = None):
    result = await client.request_json(url, method='POST', data=data, headers=headers,
                                       settings=RetrySettings(retry_times=2))
    if result is None:
        return False
    try:
        if not isinstance(result, dict):
            raise ValueError('unexpected response format')
        new_auth = AuthToken.from_login_data(result)
        new_auth.set_cookies(client.cookie_jar)
        return True
    except Exception as e:
        logger.exception(
            f'[login] failed to log in: error when parsing response: {type(e)}, {e}. Raw response: {result}')
        return False


async def fetch_stream_url(client: HttpClient,
                           logger: logging.Logger,
                           stream_id: str,
                           auth: AuthToken) -> Optional[str]:
    url = f'https://www.withny.fun/api/streams/{stream_id}/playback-url'
    headers = {'Referer': url, 'Authorization': auth.plain_token}
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
class WithnyLiveConfig(BaseDbConfig):
    pass


@Plugins.register('withny.live', Plugins.kind.ACTOR_ENTITY)
class WithnyLiveEntity(ActionEntity):
    cookies_file: Optional[FilePath]
    """path to a text file containing cookies in Netscape format"""
    poll_interval: PositiveInt = 30
    """how often live status of the stream that should have started by now is updated, in seconds"""
    poll_attempts: PositiveInt = 120
    """how many times live status of the stream that should have started by now is updated before giving up"""

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


@Plugins.register('withny.live', Plugins.kind.ACTOR)
class WithnyLive(Action):
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
        self.sessions = SessionStorage(self.logger)
        self.state_storage = StateStorage()
        self.tasks: Dict[str, asyncio.Task] = {}
        self.db = RecordDB(conf.db_path, logger=self.logger.getChild('db'))

    def handle(self, entity: WithnyLiveEntity, record: Record):
        if isinstance(record, WithnyRecord):
            stream_id = record.stream_id
            if record.stream_id in self.tasks:
                self.logger.debug(f'[{entity.name}] task for stream {stream_id} is already running')
                return
            task = self.controller.create_task(self.handle_stream(entity, record))
            task.add_done_callback(lambda _: self.tasks.pop(stream_id))
            self.tasks[stream_id] = task

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
        if response is None:
            update_interval = Delay.get_next(current_update_interval)
            data = None
        else:
            update_interval = response.next_update_interval(base_update_interval, current_update_interval, True)
            if response.no_content:
                data = None
            else:
                data = response.json()
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
            url = f'https://www.withny.fun/api/schedules/{record.schedule_id}'
            record_data, update_interval = await self.request_json(url, base_update_interval, update_interval, client)
            updated_record = self.parse_record(record_data, parse_schedule_record)
        if record.schedule_id is None or updated_record is None:
            url = f'https://www.withny.fun/api/streams/with-rooms?username={record.username}'
            data, update_interval = await self.request_json(url, base_update_interval, update_interval, client)
            record_data = find_stream_data(data, record.stream_id)
            updated_record = self.parse_record(record_data, parse_live_record)

        return updated_record, update_interval

    async def handle_stream(self, entity: WithnyLiveEntity, record: WithnyRecord):
        session = self.sessions.get_session(entity.cookies_file, name=entity.name)
        client = HttpClient(self.logger, session)

        # wait for the stream to go live, return if there is no point waiting anymore
        update_interval = float(entity.poll_interval)
        for attempt in range(entity.poll_attempts):
            await asyncio.sleep(update_interval)
            self.logger.debug(
                f'stream {record.stream_id} by {record.username}, attempt {attempt}: fetching live status')
            updated_record, update_interval = await self.fetch_updated_record(client, record, update_interval,
                                                                              entity.poll_interval)
            if updated_record is None:
                continue

            updated_record.origin = record.origin
            updated_record.chain = record.chain
            record = updated_record

            if record.end is not None:
                self.logger.debug(f'stream {record.stream_id} has ended at {record.end}: {record!r}')
                return
            elif record.start is not None:
                self.logger.debug(f'stream {record.stream_id}, attempt {attempt}: {record.username} is live')
                break
            elif record.scheduled is None:
                self.logger.debug(
                    f'stream {record.stream_id} has neither start nor scheduled date, dropping record {record!r}')
                return
            else:
                self.logger.debug(f'stream {record.stream_id}, attempt {attempt}: scheduled {record.scheduled}')
                time_left = record.scheduled - datetime.datetime.now(datetime.timezone.utc)
                delay = time_left.total_seconds()
                if delay > 0:
                    self.logger.debug(
                        f'stream {record.stream_id} is scheduled to {record.scheduled}, waiting for {time_left}: {record!r}')
                else:
                    delay = entity.poll_interval
                update_interval = delay

        else:
            self.logger.debug(
                f'stream {record.stream_id} is not live after {entity.poll_attempts} checks, dropping record {record!r}')
            return

        # stream is now live for sure, try fetching stream_url
        logged_in = await ensure_login(client, self.logger)
        if not logged_in:
            self.logger.warning(f'login failed, aborting processing')
            return
        auth = AuthToken.from_cookies(session.cookie_jar)
        stream_url = await fetch_stream_url(client, self.logger, record.stream_id, auth)
        if stream_url is None:
            self.logger.warning(f'failed to fetch stream_url for stream {record.stream_id}')
            return

        record.playlist_url = stream_url
        self.db.store_records([record], entity.name)
        self.on_record(entity, record)
