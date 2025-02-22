import asyncio
import datetime
import json
import logging
import urllib.parse
from http.cookies import SimpleCookie
from typing import Dict, Optional, Sequence

import aiohttp
from aiohttp.abc import AbstractCookieJar
from pydantic import PositiveInt

from avtdl.core import utils
from avtdl.core.db import BaseDbConfig, RecordDB
from avtdl.core.interfaces import Action, ActionEntity, Record, RuntimeContext
from avtdl.core.plugins import Plugins
from avtdl.core.utils import SessionStorage, find_one, get_cookie_value, jwt_decode
from avtdl.plugins.withny.extractors import WithnyRecord


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
    def from_login_data(cls, data: dict) -> 'AuthToken':
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


async def ensure_login(session: aiohttp.ClientSession, logger: logging.Logger, username: str, password: str) -> bool:
    try:
        auth = AuthToken.from_cookies(session.cookie_jar)
    except ValueError:
        auth = None
    if auth is None:
        success = await perform_login(session, logger, username, password)
        return success
    elif has_expired(auth.refresh_token_expiration):
        # no point checking auth token here, since it does not outlive refresh token
        success = await perform_login(session, logger, username, password)
        return success
    elif has_expired(auth.token_expiration):
        success = await refresh_auth(session, logger)
        return success
    else:
        # valid auth token is already present in cookies
        return True


async def perform_login(session: aiohttp.ClientSession, logger: logging.Logger, username: str, password: str) -> bool:
    """Make request to log in, update session's cookie jar"""
    url = 'https://www.withny.fun/api/auth/login'
    data = json.dumps({'email': username, 'password': password})
    headers = {'Referer': 'https://www.withny.fun/login', 'Content-Type': 'application/json'}
    return await make_auth_request(session, logger, url, data, headers)


async def refresh_auth(session: aiohttp.ClientSession, logger: logging.Logger) -> bool:
    """Make request to refresh auth token, update session's cookie jar"""
    try:
        auth = AuthToken.from_cookies(session.cookie_jar)
    except Exception as e:
        logger.debug(f'[login] failed to refresh token: error when parsing cookies: {type(e)}, {e}')
        return False
    url = 'https://www.withny.fun/api/auth/token'
    data = json.dumps({'refreshToken': auth.refresh_token})
    headers = {'Referer': 'https://www.withny.fun/', 'Content-Type': 'application/json'}
    return await make_auth_request(session, logger, url, data, headers)


async def make_auth_request(session: aiohttp.ClientSession, logger: logging.Logger, url: str, data,
                            headers: Optional[dict] = None):
    result = await utils.request_json(url, session, logger, method='POST', data=data, headers=headers, retry_times=2)
    if result is None:
        return False
    try:
        new_auth = AuthToken.from_login_data(result)
        new_auth.set_cookies(session.cookie_jar)
        return True
    except Exception as e:
        logger.exception(
            f'[login] failed to log in: error when parsing response: {type(e)}, {e}. Raw response: {result}')
        return False


async def fetch_stream_url(session: aiohttp.ClientSession, logger: logging.Logger, stream_id: str, auth: AuthToken) -> \
        Optional[str]:
    url = f'https://www.withny.fun/api/streams/{stream_id}/playback-url'
    headers = {'Referer': url, 'Authorization': auth.plain_token}
    result = await utils.request_json(url, session, logger, headers=headers, retry_times=2)
    if result is None:
        return None
    if not isinstance(result, str):
        logger.warning(f'unexpected stream url format: {result}')
        return None
    return result


async def fetch_cast_info(session: aiohttp.ClientSession, logger: logging.Logger, username: str) -> Optional[dict]:
    url = f'https://www.withny.fun/api/casts/{username}'
    result = await utils.request_json(url, session, logger, retry_times=2)
    if result is None:
        return None
    return result


def cast_is_live(cast_info: dict) -> Optional[bool]:
    state = find_one(cast_info, '$.ivsChannel.state')
    if state == 'live':
        return True
    elif state == 'offline':
        return False
    if state is None:
        return None
    else:
        return None


Plugins.register('withny.live', Plugins.kind.ASSOCIATED_RECORD)(WithnyRecord)


@Plugins.register('withny.live', Plugins.kind.ACTOR_CONFIG)
class WithnyLiveConfig(BaseDbConfig):
    login: Optional[str] = None
    """login of the account used for monitoring"""
    password: Optional[str] = None
    """password of the account used for monitoring"""
    poll_interval: PositiveInt = 30
    """how often live status of the stream that should have started by now is updated, in seconds"""
    poll_attempts: PositiveInt = 120
    """how many times live status of the stream that should have started by now is updated before giving up"""


@Plugins.register('withny.live', Plugins.kind.ACTOR_ENTITY)
class WithnyLiveEntity(ActionEntity):
    wait_for_live: bool = True
    """wait for livestream to go live before fetching stream url. When set to false, upcoming streams are ignored"""


@Plugins.register('withny.live', Plugins.kind.ACTOR)
class WithnyLive(Action):
    """
    Wait for livestream on Withny

    If incoming record comes from the "withny" monitor and represents an ongoing or upcoming livestream,
    wait for the stream start and try fetching direct `playlist_url` of the stream, then emit updated record
    down the chain.
    """

    def __init__(self, conf: WithnyLiveConfig, entities: Sequence[WithnyLiveEntity], ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.sessions = SessionStorage(self.logger)
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

    async def handle_stream(self, entity: WithnyLiveEntity, record: WithnyRecord):
        if record.end is not None:
            self.logger.debug(f'stream {record.stream_id} has ended at {record.end}: {record!r}')
            return
        if record.start is None:
            if not entity.wait_for_live:
                self.logger.debug(
                    f'stream {record.stream_id} has not started and "wait_for_live" is enabled, dropping record {record!r}')
                return
            elif record.scheduled is None:
                self.logger.debug(
                    f'stream {record.stream_id} has neither start nor scheduled date, dropping record {record!r}')
                return
            else:
                time_left = record.scheduled - datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                    seconds=30)
                time_left = max(time_left, datetime.timedelta())
                delay = time_left.total_seconds()
                self.logger.debug(
                    f'stream {record.stream_id} is scheduled to {record.scheduled}, waiting for {time_left}: {record!r}')
                # await asyncio.sleep(delay)
                await asyncio.sleep(0)
        session = self.sessions.get_session(name='withny.live')  # all tasks use the same session to reuse login cookies

        # stream should be live already or go live soon, wait for it
        for attempt in range(self.conf.poll_attempts):
            self.logger.debug(f'stream {record.stream_id}, attempt {attempt}: fetching live status of "{record.username}"')
            cast_info = await fetch_cast_info(session, self.logger, record.username)
            if cast_info is not None:
                is_live = cast_is_live(cast_info)
                if is_live:
                    self.logger.debug(f'stream {record.stream_id}, attempt {attempt}: "{record.username}" is live')
                    break
            await asyncio.sleep(self.conf.poll_interval)
        else:
            self.logger.debug(f'stream {record.stream_id} is not live after {self.conf.poll_attempts} checks, dropping record {record!r}')
            return

        # stream is now live for sure, try fetching stream_url
        logged_in = await ensure_login(session, self.logger, self.conf.login, self.conf.password)
        if not logged_in:
            self.logger.warning(f'login failed, aborting processing')
            return
        auth = AuthToken.from_cookies(session.cookie_jar)
        stream_url = await fetch_stream_url(session, self.logger, record.stream_id, auth)
        if stream_url is None:
            self.logger.warning(f'failed to fetch stream_url for stream {record.stream_id}')
            return

        record.playlist_url = stream_url
        self.db.store_records([record], entity.name)
        self.on_record(entity, record)
