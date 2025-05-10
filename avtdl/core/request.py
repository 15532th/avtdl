import abc
import asyncio
import datetime
import json
import logging
import re
import time
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from math import log2
from typing import Any, Dict, Optional, Tuple, Union

import aiohttp
import multidict
from aiohttp.abc import AbstractCookieJar
from multidict import CIMultiDictProxy

from avtdl.core.utils import JSONType, timeit

HIGHEST_UPDATE_INTERVAL: float = 4000


@dataclass
class RetrySettings:
    retry_times: int = 1
    """transparent retrying: number of attempts"""
    retry_delay: float = 1
    """transparent retrying: delay before first retry attempt"""
    retry_multiplier: float = 1.2
    """transparent retrying: factor to increase retry delay compared to the previous attempt"""


@dataclass
class EndpointState:
    etag: Optional[str] = None
    last_modified: Optional[str] = None


class StateStorage:
    """
    Store EndpointState objects grouped by (url, method, params) tuples
    """

    def __init__(self) -> None:
        self._storage: Dict[(Tuple[str, str, Optional[Any]]), EndpointState] = defaultdict(EndpointState)

    def get(self, url: str, method: str, params: Optional[Any]) -> EndpointState:
        encoded_params = urllib.parse.urlencode(sorted(params.items())) if params is not None else None
        return self._storage[(url, method, encoded_params)]


def decide_on_update_interval(logger: logging.Logger, url: str, status: Optional[int],
                              headers: Optional[CIMultiDictProxy[str]], current_update_interval: float,
                              base_update_interval: float, adjust_update_interval: bool = True) -> float:
    update_interval: float

    if status is None or headers is None:  # response hasn't completed due to network error
        update_interval = Delay.get_next(current_update_interval)
        logger.warning(f'update interval set to {update_interval} seconds for {url}')
        return update_interval

    retry_after = get_retry_after(headers)
    if retry_after is not None:
        raw_header = headers.get("Retry-After")
        logger.debug(f'got Retry-After header with value {raw_header}')
        update_interval = max(float(retry_after), HIGHEST_UPDATE_INTERVAL)
        logger.warning(
            f'update interval set to {update_interval} seconds for {url} as requested by response headers')
    elif status >= 400:
        update_interval = max(Delay.get_next(current_update_interval), current_update_interval)
        logger.warning(f'update interval set to {update_interval} seconds for {url}')
    else:
        if adjust_update_interval:
            new_update_interval = get_cache_ttl(headers) or base_update_interval
            new_update_interval = min(new_update_interval, 10 * base_update_interval,
                                      HIGHEST_UPDATE_INTERVAL)  # in case ttl is overly long
            new_update_interval = max(new_update_interval, base_update_interval)
            if new_update_interval != current_update_interval:
                logger.info(f'next update in {new_update_interval}')
            update_interval = new_update_interval
        else:
            if current_update_interval != base_update_interval:
                logger.info(f'restoring update interval {base_update_interval} seconds for {url}')
            update_interval = base_update_interval

    return update_interval


class HttpClient:

    def __init__(self, logger: logging.Logger, session: aiohttp.ClientSession):
        self.logger = logger
        self.session = session

    @property
    def cookie_jar(self) -> AbstractCookieJar:
        return self.session.cookie_jar

    async def request(self, url: str,
                      params: Optional[Dict[str, str]] = None,
                      data: Optional[Any] = None,
                      data_json: Optional[Any] = None,
                      headers: Optional[Dict[str, Any]] = None,
                      method: str = 'GET',
                      state: EndpointState = EndpointState(),
                      settings: RetrySettings = RetrySettings()) -> Optional['HttpResponse']:
        response = None
        next_try_delay = settings.retry_delay
        for attempt in range(settings.retry_times):
            response = await self.request_once(url, params, data, data_json, headers, method, state)
            if response is not None and response.ok:
                break
            next_try_delay *= settings.retry_multiplier
        return response

    async def request_once(self, url: str,
                           params: Optional[Dict[str, str]] = None,
                           data: Optional[Any] = None,
                           data_json: Optional[Any] = None,
                           headers: Optional[Dict[str, Any]] = None,
                           method: str = 'GET',
                           state: EndpointState = EndpointState(),
                           ) -> Optional['HttpResponse']:
        logger = self.logger

        request_headers: Dict[str, Any] = headers or {}
        if self.session.headers is not None:
            request_headers.update(self.session.headers)
        if state.last_modified is not None and method in ['GET', 'HEAD']:
            request_headers['If-Modified-Since'] = state.last_modified
        if state.etag is not None:
            request_headers['If-None-Match'] = state.etag
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
            return None

        if not client_response.ok:
            logger.warning(
                f'got code {client_response.status} ({client_response.reason or "No reason"}) while fetching {url}')
            if text:
                logger.debug(f'response body: "{text}"')
        elif client_response.status != 304:
            # some servers do not have cache headers in 304 response, so only updating on 200
            state.last_modified = client_response.headers.get('Last-Modified', None)
            state.etag = client_response.headers.get('Etag', None)

            cache_control = client_response.headers.get('Cache-control')
            logger.debug(
                f'Last-Modified={state.last_modified or "absent"}, ETAG={state.etag or "absent"}, Cache-control="{cache_control or "absent"}" for {client_response.real_url}')

        response = HttpResponse.from_response(client_response, text, state, logger)

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
        if response is None:
            return None
        if response.no_content:
            return None
        return response.text

    async def request_json(self, url: str,
                           params: Optional[Dict[str, str]] = None,
                           data: Optional[Any] = None,
                           data_json: Optional[Any] = None,
                           headers: Optional[Dict[str, Any]] = None,
                           method: str = 'GET',
                           state: EndpointState = EndpointState(),
                           settings: RetrySettings = RetrySettings()) -> Optional[JSONType]:
        response = await self.request(url, params, data, data_json, headers, method, state, settings)
        if response is None:
            return None
        if response.no_content:
            return None
        return response.json()


@dataclass
class HttpResponse:
    logger: logging.Logger
    text: str
    url: str
    ok: bool
    no_content: bool
    status: int
    reason: str
    headers: CIMultiDictProxy[str]
    request_headers: CIMultiDictProxy[str]
    cookies: SimpleCookie
    endpoint_state: EndpointState
    content_encoding: str

    @classmethod
    def from_response(cls, response: aiohttp.ClientResponse, text: str, state: EndpointState, logger: logging.Logger):
        response = cls(
            logger,
            text,
            str(response.url),
            response.ok,
            response.status < 200 or response.status >= 300,
            response.status,
            response.reason or 'No reason',
            response.headers,
            response.request_info.headers,
            response.cookies,
            state,
            response.get_encoding()
        )
        return response

    def json(self, raise_errors: bool = False) -> Optional[JSONType]:
        try:
            parsed = json.loads(self.text)
            return parsed
        except json.JSONDecodeError as e:
            self.logger.debug(f'error parsing response from {self.url}: {e}. Raw response data: "{self.text}"')
            if raise_errors:
                raise
            else:
                return None

    def next_update_interval(self, base: float, current: float, adjust_update_interval: bool = True) -> float:
        return decide_on_update_interval(self.logger, self.url, self.status, self.headers, current, base,
                                         adjust_update_interval)


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


def get_cache_ttl(headers: multidict.CIMultiDictProxy) -> Optional[int]:
    """
    check for Expires and Cache-Control headers, return integer representing
    how many seconds is left until resource is outdated, if they are present
    and was parsed successfully
    """

    def get_expires_from_cache_control(headers: multidict.CIMultiDictProxy) -> Optional[datetime.datetime]:
        try:
            cache_control = headers.get('Cache-Control', '')
            [max_age] = re.findall('max-age=(\d+)', cache_control, re.IGNORECASE)
            max_age_value = datetime.timedelta(seconds=int(max_age))

            last_modified = headers.get('Last-Modified', '')
            last_modified_value = parsedate_to_datetime(last_modified)

            expires = last_modified_value + max_age_value
            return expires
        except (TypeError, ValueError, IndexError):
            return None

    def get_expires_from_expires_header(headers: multidict.CIMultiDictProxy) -> Optional[datetime.datetime]:
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

    now = datetime.datetime.now(tz=datetime.timezone.utc)
    delta = (expires - now).total_seconds()
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
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        delay = int((retry_at - now).total_seconds())
        if delay > 0:
            return delay
    except (TypeError, ValueError):
        pass
    return None


class RateLimit:
    """
    Encapsulate state of rate limits for "bucket of tokens" type of endpoint

    >>> async def endpoint_request(url: str, client: HttpClient):
    >>>     async with RateLimit('endpoint name') as rate_limit:
    >>>         response = await client.request(url)
    >>>         rate_limit.submit_headers(response.headers)
    >>>

    Request itself, with error handling, is done by the endpoint class,
    which then should then call RateLimit.submit_headers with response headers
    to update the limit values and expiration time.
    """

    def __init__(self, name: str, logger: Optional[logging.Logger] = None) -> None:
        self.limit_total: int = 50
        self.limit_remaining: int = 10
        self.reset_at: int = int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp())

        self.name = name
        self.logger = logger or logging.getLogger().getChild('rate_limit')
        self.lock = asyncio.Lock()
        self.perf_lock_acquired_at: float = 0

    async def __aenter__(self) -> 'RateLimit':
        with timeit() as t:
            await self.lock.acquire()
        self.perf_lock_acquired_at = t.end
        if self.limit_remaining <= 1:
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
    def reset_at_date(self) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(self.reset_at, tz=datetime.timezone.utc)

    @property
    def reset_after(self) -> int:
        now = int(datetime.datetime.now().timestamp())
        reset_after = max(0, self.reset_at - now)
        return reset_after

    @property
    def delay(self) -> int:
        if self.limit_remaining <= 1:
            return self.reset_after + 1
        return 0

    @abc.abstractmethod
    def _submit_headers(self, headers: Union[Dict[str, str], CIMultiDictProxy[str]], logger: logging.Logger):
        """parse response headers and update self.limit_total, self.limit_remaining and self.reset_at"""

    def submit_headers(self, headers: Union[Dict[str, str], CIMultiDictProxy[str]],
                       logger: Optional[logging.Logger] = None):
        """
        Update limits values and reset time using data from headers.

        Client code should call it after completing request. Client code might
        provide a custom Logger instance to help with identifying debug output
        from specific request.
        """
        logger = logger or self.logger

        retry_after = get_retry_after(headers)
        if retry_after is not None:
            self.limit_remaining = 0
            self.reset_at = int(
                (datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(seconds=retry_after)).timestamp())
            logger.debug(
                f'[{self.name}] Retry-After is present and set to {retry_after}, setting reset_at to {self.reset_at}')
            return

        self._submit_headers(headers, logger)
