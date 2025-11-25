import abc
import asyncio
import datetime
import json
import logging
import re
import time
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from math import log2
from pathlib import Path
from textwrap import shorten
from typing import Any, Dict, Literal, Optional, Tuple, Union

import aiohttp
import multidict
from aiohttp.abc import AbstractCookieJar
from multidict import CIMultiDictProxy

from avtdl._version import __version__
from avtdl.core.utils import JSONType, convert_cookiejar, load_cookies, timeit, utcnow

HIGHEST_UPDATE_INTERVAL: float = 4000


@dataclass
class RetrySettings:
    retry_times: int = 0
    """transparent retrying: number of attempts"""
    retry_delay: float = 1
    """transparent retrying: delay before first retry attempt"""
    retry_multiplier: float = 1.2
    """transparent retrying: factor to increase retry delay compared to the previous attempt"""

    def __post_init__(self):
        if self.retry_times < 0:
            raise ValueError(f'retry_times must be positive, got "{self.retry_times}"')


@dataclass
class EndpointState:
    etag: Optional[str] = None
    last_modified: Optional[str] = None

    def update(self, headers: Union[CIMultiDictProxy, Dict[str, str]]):
        self.last_modified = headers.get('Last-Modified', None)
        self.etag = headers.get('Etag', None)


class StateStorage:
    """
    Store EndpointState objects grouped by (url, method, params) tuples
    """

    def __init__(self) -> None:
        self._storage: Dict[(Tuple[str, str, Optional[Any]]), EndpointState] = defaultdict(EndpointState)

    def get(self, url: str, method: str, params: Optional[Any]) -> EndpointState:
        encoded_params = urllib.parse.urlencode(sorted(params.items())) if params is not None else None
        return self._storage[(url, method, encoded_params)]


class Delay:
    """Provide method to calculate next delay for exponential backoff based on S-shaped curve"""

    A: float = 4600  # upper asymptote
    k: float = 0.88  # curve growth rate
    x0: float = 8  # x value corresponding to midpoint of the curve

    @classmethod
    def _sigmoid(cls, x: float) -> float:
        y = cls.A / (1 + 2 ** (-cls.k * (x - cls.x0)))
        return y

    @classmethod
    def _inv_sigmoid(cls, y: float) -> float:
        if y <= 0:
            return 0
        # raises ValueError if y >= cls.A
        x = cls.x0 - log2((cls.A - y) / y) / cls.k
        return x

    @classmethod
    def get_next(cls, current: float) -> float:
        """
        Find current value on S-shaped curve and return a next one

        Returns 0 if input is not higher than 0,
        returns cls.A if input higher than cls.A.
        """
        try:
            current_step = cls._inv_sigmoid(current)
        except ValueError:
            current_step = current
        next_step = current_step + 1
        next_delay = cls._sigmoid(next_step)
        return next_delay


def get_cache_ttl(headers: Union[multidict.CIMultiDictProxy, Dict[str, str]]) -> Optional[int]:
    """
    check for Expires and Cache-Control headers, return integer representing
    how many seconds is left until resource is outdated, if they are present
    and was parsed successfully
    """

    def get_expires_from_cache_control(headers: Union[multidict.CIMultiDictProxy, Dict[str, str]]) -> Optional[datetime.datetime]:
        cache_control = headers.get('Cache-Control', '')
        if 'must-revalidate' in cache_control.lower():
            return None
        try:
            [max_age] = re.findall('max-age=(\d+)', cache_control, re.IGNORECASE)
            max_age_value = datetime.timedelta(seconds=int(max_age))
        except (IndexError, TypeError, ValueError):
            return None

        try:
            date_value = parsedate_to_datetime(headers.get('Date', ''))
        except (TypeError, ValueError):
            date_value = None
        try:
            last_modified_value = parsedate_to_datetime(headers.get('Last-Modified', ''))
        except (TypeError, ValueError):
            last_modified_value = None

        calculated_update_date = date_value or last_modified_value or utcnow()
        expires = calculated_update_date + max_age_value
        return expires

    def get_expires_from_expires_header(headers: Union[multidict.CIMultiDictProxy, Dict[str, str]]) -> Optional[datetime.datetime]:
        try:
            expires_header = headers.get('Expires')
            if expires_header is None or expires_header == '0':
                return None
            expires_value = parsedate_to_datetime(expires_header)
            return expires_value
        except (TypeError, ValueError):
            return None

    expires = get_expires_from_cache_control(headers) or get_expires_from_expires_header(headers)
    if expires is None:
        return None

    delta = (expires - utcnow()).total_seconds()
    if delta < 0:
        return None

    return int(delta)


def get_retry_after(headers: Union[Dict[str, str], multidict.CIMultiDictProxy[str]]) -> Optional[int]:
    """return parsed value of Retry-After header, if present"""
    retry_after = headers.get('Retry-After')
    if retry_after is None:
        return None
    try:
        return int(retry_after)
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(retry_after)
        delay = int((retry_at - utcnow()).total_seconds())
        if delay > 0:
            return delay
    except (TypeError, ValueError):
        pass
    return None


def insert_useragent(headers: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if headers is None:
        return None
    if 'User-Agent' in headers:
        return headers
    headers = headers.copy()
    headers['User-Agent'] = f'avtdl {__version__}'
    return headers


@dataclass
class NoResponse:
    logger: logging.Logger
    e: Exception
    url: str
    text: None = None
    completed: bool = False
    ok: bool = False
    has_content: bool = False
    status: int = 0
    headers: None = None

    def has_json(self) -> bool:
        return False

    def json(self) -> JSONType:
        return None

    def next_update_interval(self, base: float, current: float, adjust_update_interval: bool = True) -> float:
        return decide_on_update_interval(self.logger, self.url, None, None, current, base, adjust_update_interval)


@dataclass
class HttpResponse:
    logger: logging.Logger
    text: str
    """response body in plaintext"""
    url: str
    """request url"""
    ok: bool
    """status is not 4xx or 5xx"""
    has_content: bool
    """status is 2xx"""
    status: int
    reason: str
    headers: Union[CIMultiDictProxy[str], Dict[str, str]]
    """response headers"""
    request_headers: Union[CIMultiDictProxy[str], Dict[str, str]]
    cookies: SimpleCookie
    """response cookies"""
    endpoint_state: EndpointState
    content_encoding: str
    """detected response content encoding"""
    completed: bool = True
    _json: Optional[JSONType] = None

    @classmethod
    def from_response(cls, response: aiohttp.ClientResponse, text: str, state: EndpointState, logger: logging.Logger):
        has_content = 200 <= response.status < 300
        factory: type[HttpResponse]
        if has_content:
            factory = DataResponse
        elif response.ok:
            factory = GoodResponse
        elif not response.ok:
            factory = BadResponse
        else:
            factory = cls
        response = factory(
            logger,
            text,
            str(response.url),
            response.ok,
            has_content,
            response.status,
            response.reason or 'No reason',
            response.headers,
            response.request_info.headers,
            response.cookies,
            state,
            response.get_encoding()
        )
        return response

    def has_json(self) -> bool:
        try:
            _ = self.json()
            return True
        except json.JSONDecodeError:
            return False

    def json(self) -> JSONType:
        if self._json is None:
            self._json = json.loads(self.text)
        return self._json

    def next_update_interval(self, base: float, current: float, adjust_update_interval: bool = True) -> float:
        return decide_on_update_interval(self.logger, self.url, self.status, self.headers, current, base,
                                         adjust_update_interval)


MaybeHttpResponse = Union[NoResponse, HttpResponse]


class BadResponse(HttpResponse):
    """HttpResponse that completed with error (status >= 400)"""
    ok: Literal[False] = False


class GoodResponse(HttpResponse):
    """HttpResponse that completed successfully (status < 400)"""
    ok: Literal[True] = True


class DataResponse(GoodResponse):
    """HttpResponse that completed successfully"""
    has_content: Literal[True] = True


class RateLimit:
    """
    Encapsulate state of rate limits for endpoint

    >>> async def endpoint_request(url: str, client: HttpClient, rate_limit: RateLimit):
    >>>     async with rate_limit:
    >>>         response = await client.request(url)
    >>>         rate_limit.submit_response(response)
    >>>

    Base rate limit, taking into account response status and RetryAfter header
    """

    DEFAULT_DELAY = 10

    def __init__(self, name: str, logger: Optional[logging.Logger] = None, base_delay: int = DEFAULT_DELAY) -> None:
        self.ready_at: datetime.datetime = utcnow()
        self.base_delay = base_delay
        self.current_delay = base_delay

        self.name = name
        self.logger = logger or logging.getLogger().getChild('rate_limit')
        self.lock = asyncio.Lock()
        self.perf_lock_acquired_at: float = 0

    async def __aenter__(self) -> 'RateLimit':
        with timeit() as t:
            await self.lock.acquire()
        self.perf_lock_acquired_at = t.end
        if self.delay > 0:
            self.logger.debug(
                f'[{self.name}] lock acquired in {t.timedelta}, {self.delay} seconds until rate limit reset')
            await asyncio.sleep(self.delay)
        else:
            self.logger.debug(f'[{self.name}] lock acquired in {t.timedelta}, there is no active rate limit')
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.lock.release()
        duration = datetime.timedelta(seconds=(time.perf_counter() - self.perf_lock_acquired_at))
        self.logger.debug(f'[{self.name}] lock released after {duration}')
        return False

    @property
    def delay(self) -> int:
        """seconds left until next request can be made"""
        delay = int((self.ready_at - utcnow()).total_seconds()) + 1
        reset_after = max(0, delay)
        return reset_after

    def submit_response(self, response: MaybeHttpResponse, logger: Optional[logging.Logger] = None):
        """
        Update limits values and reset time using data from response.

        Client code should call it after completing request. Client code might
        provide a custom Logger instance to help with identifying debug output
        from specific request.
        """
        logger = logger or self.logger
        self.current_delay = self._submit_response(response, logger)
        self.ready_at = (utcnow() + datetime.timedelta(seconds=self.current_delay))

    @abc.abstractmethod
    def _submit_response(self, response: MaybeHttpResponse, logger: logging.Logger) -> int:
        """parse response and return minimum delay until the next request, in seconds"""


class HttpRateLimit(RateLimit):

    def _submit_response(self, response: MaybeHttpResponse, logger: logging.Logger) -> int:
        return int(response.next_update_interval(self.base_delay, self.current_delay))


class BucketRateLimit(RateLimit):
    """Rate limit for token bucket type of endpoint"""

    def __init__(self, name: str, logger: Optional[logging.Logger] = None) -> None:
        super().__init__(name, logger)
        self.limit_total: int = 50
        self.limit_remaining: int = 10
        self.reset_at: int = int(utcnow().timestamp())

    def _fallback_delay(self, response: MaybeHttpResponse, logger: logging.Logger) -> int:
        return int(response.next_update_interval(self.base_delay, self.current_delay, True))

    def _submit_response(self, response: MaybeHttpResponse, logger: logging.Logger) -> int:
        logger = logger or self.logger

        if isinstance(response, NoResponse):
            return self._fallback_delay(response, logger)
        parsed_successfully = self._submit_headers(response, logger)
        if not parsed_successfully:
            return self._fallback_delay(response, logger)
        if self.limit_remaining >= 1:
            if response.ok:
                return 0
            return self._fallback_delay(response, logger)
        reset_after = max(0, self.reset_at - int(utcnow().timestamp()))
        return reset_after

    @abc.abstractmethod
    def _submit_headers(self, response: HttpResponse, logger: logging.Logger) -> bool:
        """
        Parse response headers and update self.limit_total, self.limit_remaining and self.reset_at,
        returns True if headers are present and successfully parsed
        """


class NoRateLimit(RateLimit):

    def __init__(self, name: str, logger: Optional[logging.Logger] = None) -> None:
        logger = logging.getLogger('noop')
        logger.setLevel(logging.CRITICAL)
        super().__init__(name, logger)

    def _submit_response(self, response: MaybeHttpResponse, logger: logging.Logger) -> float:
        return 0


@dataclass
class RequestDetails:
    """Represents parameters of a request to be made to specific endpoint"""
    url: str
    method: str = 'GET'
    params: Optional[Dict[str, Any]] = None
    data: Optional[Any] = None
    data_json: Optional[JSONType] = None
    headers: Optional[Dict[str, Any]] = None
    rate_limit: RateLimit = field(default_factory=lambda: NoRateLimit('default'))
    endpoint_state: EndpointState = field(default_factory=EndpointState)
    retry_settings: RetrySettings = field(default_factory=RetrySettings)


class Endpoint(abc.ABC):
    """
    Superclass providing utility methods for concrete Endpoints

    Concrete Endpoints must implement prepare() method taking arbitrary
    arguments, that returns RequestDetails instance.
    """

    @abc.abstractmethod
    def prepare(self, *args, **kwargs) -> RequestDetails:
        """Prepare a RequestDetails object based on passed arguments"""


def decide_on_update_interval(logger: logging.Logger, url: str, status: Optional[int],
                              headers: Union[CIMultiDictProxy[str], Dict[str, str], None],
                              current_update_interval: float, base_update_interval: float,
                              adjust_update_interval: bool = True) -> float:
    update_interval: float

    truncated_url = shorten(url, 256, break_long_words=True)
    if status is None or headers is None:  # response hasn't completed due to network error
        update_interval = max(Delay.get_next(current_update_interval), current_update_interval)
        logger.warning(f'update interval set to {update_interval} seconds for {truncated_url}')
        return update_interval

    retry_after = get_retry_after(headers)
    if retry_after is not None:
        raw_header = headers.get("Retry-After")
        logger.debug(f'got Retry-After header with value {raw_header}')
        update_interval = min(float(retry_after), HIGHEST_UPDATE_INTERVAL)
        if update_interval < base_update_interval:
            update_interval = base_update_interval
        logger.warning(f'update interval set to {update_interval} seconds for {truncated_url} as requested by Retry-After')
    elif status >= 400:
        update_interval = max(Delay.get_next(current_update_interval), current_update_interval)
        logger.warning(f'update interval set to {update_interval} seconds for {truncated_url}')
    else:
        if adjust_update_interval:
            new_update_interval = get_cache_ttl(headers) or base_update_interval
            new_update_interval = min(new_update_interval, 10 * base_update_interval)  # in case ttl is overly long
            new_update_interval = max(new_update_interval, base_update_interval)
            if new_update_interval != current_update_interval:
                logger.info(f'next update in {new_update_interval}')
            update_interval = new_update_interval
        else:
            if current_update_interval != base_update_interval:
                logger.info(f'restoring update interval {base_update_interval} seconds for {truncated_url}')
            update_interval = base_update_interval

    return update_interval


class HttpClient:

    def __init__(self, logger: logging.Logger, session: aiohttp.ClientSession):
        self.logger = logger
        self.session = session

    @property
    def cookie_jar(self) -> AbstractCookieJar:
        return self.session.cookie_jar

    async def request_once(self, url: str,
                           params: Optional[Dict[str, str]] = None,
                           data: Optional[Any] = None,
                           data_json: Optional[Any] = None,
                           headers: Optional[Dict[str, Any]] = None,
                           method: str = 'GET',
                           state: EndpointState = EndpointState(),
                           ) -> 'MaybeHttpResponse':
        logger = self.logger

        request_headers: Dict[str, Any] = headers or {}
        if self.session.headers is not None:
            request_headers.update(self.session.headers)
        if state.last_modified is not None and method in ['GET', 'HEAD']:
            request_headers['If-Modified-Since'] = state.last_modified
        if state.etag is not None:
            request_headers['If-None-Match'] = state.etag
        request_headers = insert_useragent(request_headers)
        server_hostname = self.session.headers.get('Host') or request_headers.get('Host') or None
        try:
            async with self.session.request(method, url, headers=request_headers, params=params, data=data,
                                            json=data_json, server_hostname=server_hostname) as client_response:
                # fully read http response to get it cached inside ClientResponse object
                # client code can then use it by awaiting .text() again without causing
                # network activity and potentially triggering associated errors
                text = await client_response.text()
        except Exception as e:
            logger.warning(f'error while fetching {url}: {e.__class__.__name__} {e}')
            return NoResponse(logger, e, url)

        if not client_response.ok:
            logger.warning(
                f'got code {client_response.status} ({client_response.reason or "No reason"}) while fetching {url}')
            logger.debug(f'request headers: "{client_response.request_info.headers}"')
            logger.debug(f'response headers: "{client_response.headers}"')
            logger.debug(f'response body: "{text}"')
        elif client_response.status != 304:
            # some servers do not have cache headers in 304 response, so only updating on 200
            state.update(client_response.headers)

            cache_control = client_response.headers.get('Cache-control')
            logger.debug(
                f'Last-Modified={state.last_modified or "absent"}, ETAG={state.etag or "absent"}, Cache-control="{cache_control or "absent"}" for {client_response.real_url}')

        response = HttpResponse.from_response(client_response, text, state, logger)
        return response

    async def request(self, url: str,
                      params: Optional[Dict[str, str]] = None,
                      data: Optional[Any] = None,
                      data_json: Optional[Any] = None,
                      headers: Optional[Dict[str, Any]] = None,
                      method: str = 'GET',
                      state: EndpointState = EndpointState(),
                      settings: RetrySettings = RetrySettings()) -> 'MaybeHttpResponse':
        response: MaybeHttpResponse = NoResponse(self.logger, Exception('request_once was never called'), url)
        next_try_delay = settings.retry_delay
        for attempt in range(settings.retry_times + 1):
            response = await self.request_once(url, params, data, data_json, headers, method, state)
            if response is not None and response.ok:
                break
            next_try_delay *= settings.retry_multiplier
        return response

    async def request_text(self, url: str,
                           params: Optional[Dict[str, str]] = None,
                           data: Optional[Any] = None,
                           data_json: Optional[Any] = None,
                           headers: Optional[Dict[str, Any]] = None,
                           method: str = 'GET',
                           state: EndpointState = EndpointState(),
                           settings: RetrySettings = RetrySettings()) -> Optional[str]:
        response = await self.request(url, params, data, data_json, headers, method, state, settings)
        if isinstance(response, DataResponse):
            return response.text
        return None

    async def request_json(self, url: str,
                           params: Optional[Dict[str, str]] = None,
                           data: Optional[Any] = None,
                           data_json: Optional[Any] = None,
                           headers: Optional[Dict[str, Any]] = None,
                           method: str = 'GET',
                           state: EndpointState = EndpointState(),
                           settings: RetrySettings = RetrySettings()) -> Optional[JSONType]:
        response = await self.request(url, params, data, data_json, headers, method, state, settings)
        if response.has_json():
            return response.json()
        return None

    async def request_endpoint(self, logger: logging.Logger, details: RequestDetails) -> MaybeHttpResponse:
        async with details.rate_limit:
            response = await self.request(url=details.url,
                                          params=details.params,
                                          data=details.data,
                                          data_json=details.data_json,
                                          headers=details.headers,
                                          method=details.method,
                                          state=details.endpoint_state,
                                          settings=details.retry_settings)
            details.rate_limit.submit_response(response, logger)
        return response


class SessionStorage:
    """
    Provide way to initialize, store and reuse ClientSession objects

    The "name" parameter can be used to get distinct sessions
    with the same cookies and headers.

    To ensure sessions shared between multiple tasks are safely closed,
    user must call SessionStorage.run() once from a running event loop
    and should not use "async with" on any of the session objects, as it
    might cause the session to be closed prematurely, making other tasks
    using it fail.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.sessions: Dict[str, aiohttp.ClientSession] = {}
        self.task: Optional[asyncio.Task] = None
        self.logger = (logger or logging.getLogger()).getChild('sessions')

    @staticmethod
    def get_session_id(cookies_file: Optional[Path], headers: Optional[Dict[str, Any]], name: str = '') -> str:
        return name + str((cookies_file, headers))

    def get_session_by_id(self, session_id: str) -> Optional[aiohttp.ClientSession]:
        return self.sessions.get(session_id)

    def session_exists(self, cookies_file: Optional[Path], headers: Optional[Dict[str, Any]], name: str = '') -> bool:
        session_id = self.get_session_id(cookies_file, headers, name)
        session = self.get_session_by_id(session_id)
        return session is not None

    def get_session(self, cookies_file: Optional[Path] = None, headers: Optional[Dict[str, Any]] = None,
                    name: str = '') -> aiohttp.ClientSession:
        session_id = self.get_session_id(cookies_file, headers, name)
        session = self.get_session_by_id(session_id)
        if session is None:
            netscape_cookies = load_cookies(cookies_file)
            cookies = convert_cookiejar(netscape_cookies) if netscape_cookies else None
            session = aiohttp.ClientSession(cookie_jar=cookies, headers=headers)
            self.sessions[session_id] = session
        return session

    async def ensure_closed(self) -> None:
        try:
            await asyncio.Future()
        except (asyncio.CancelledError, KeyboardInterrupt):
            await self.close()

    async def close(self) -> None:
        self.logger.debug('closing http sessions...')
        for session_id, session in self.sessions.items():
            if not session.closed:
                self.logger.debug(f'closing session "{session_id}"')
                await session.close()
        self.logger.debug('done')
