import abc
import asyncio
import datetime
import json
import logging
import mimetypes
import re
import time
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from enum import Enum
from http.cookies import SimpleCookie
from math import log2
from pathlib import Path
from textwrap import shorten
from typing import Any, Dict, Literal, Optional, Tuple, Union

import aiohttp
import curl_cffi
import multidict
from multidict import CIMultiDictProxy
from pydantic import BaseModel

from avtdl._version import __version__
from avtdl.core.cookies import AnotherAiohttpCookieJar, AnotherCookieJar, AnotherCurlCffiCookieJar, convert_cookiejar, \
    load_cookies
from avtdl.core.utils import JSONType, timeit, utcnow

HIGHEST_UPDATE_INTERVAL: float = 4000

CHUNK_SIZE = 1024 ** 3


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


class RemoteFileInfo(BaseModel):
    url: str
    source_name: str
    extension: str = ''
    content_length: Optional[int] = None
    response_headers: dict

    @classmethod
    def from_url_response(cls, url: str, headers: Union[Dict[str, str], multidict.CIMultiDictProxy]) -> 'RemoteFileInfo':
        try:
            value = headers.get('Content-Length', 0)
            size = int(value)
        except Exception:
            size = None
        filename = cls.extract_filename(url, headers)
        if filename is None:
            name, extension = '', ''
        else:
            name, extension = filename.stem, filename.suffix
        return cls(url=url, content_length=size, source_name=name, extension=extension, response_headers=dict(headers))

    @classmethod
    def extract_filename(cls, url: str, headers: Union[Dict[str, str], multidict.CIMultiDictProxy]) -> Optional[Path]:
        filename = cls._get_filename_from_headers(headers) or cls._get_filename_from_url(url)
        if filename is not None and not filename.suffix:
            extension = cls._get_mime_extension(headers)
            if extension is not None:
                filename = filename.with_suffix(extension)
        return filename

    @staticmethod
    def _get_filename_from_headers(headers: Union[Dict[str, str], multidict.CIMultiDictProxy]) -> Optional[Path]:
        """get filename from Content-Disposition, with or without extension"""
        content_disposition = headers.get('Content-Disposition') or ''
        email = EmailMessage()
        email.add_header('Content-Disposition', content_disposition)
        name = email.get_filename()
        if name is not None:
            filename = Path(name)
            filename = Path(filename.name)
            return filename
        else:
            return None

    @staticmethod
    def _get_filename_from_url(url: str) -> Optional[Path]:
        """try extracting filename part from the url"""
        try:
            path = urllib.parse.urlparse(url).path
            if not path:
                return None
            path = urllib.parse.unquote_plus(path)
            filename = Path(path)
            if not filename.name:
                return None
            filename = Path(filename.name)
            return filename
        except Exception as e:
            return None

    @staticmethod
    def _get_mime_extension(headers: Union[Dict[str, str], multidict.CIMultiDictProxy]) -> Optional[str]:
        """return file extension deduced from Content-Type header"""
        mime_extension = None
        content_type = headers.get('Content-Type')
        if content_type is not None:
            extension = mimetypes.guess_extension(content_type, strict=False)
            if extension and extension.startswith('.'):
                mime_extension = extension
        return mime_extension


class HttpClient(abc.ABC):

    def __init__(self, logger: logging.Logger, cookies_file: Optional[Path], headers: Optional[Dict[str, Any]]):
        self.logger = logger

    @abc.abstractmethod
    async def close(self) -> None:
        """close underlying session, must be called before shutdown"""

    @property
    @abc.abstractmethod
    def cookie_jar(self) -> AnotherCookieJar:
        """return reference to the cookies jar associated with client's session"""

    @abc.abstractmethod
    async def request_once(self, url: str,
                           params: Optional[Dict[str, str]] = None,
                           data: Optional[Any] = None,
                           data_json: Optional[Any] = None,
                           headers: Optional[Dict[str, Any]] = None,
                           method: str = 'GET',
                           state: EndpointState = EndpointState(),
                           ) -> 'MaybeHttpResponse':
        """preform a single request to a text endpoint"""

    @abc.abstractmethod
    async def download_file(self, path: Path,
                            url: str,
                            params: Optional[Dict[str, str]] = None,
                            data: Optional[Any] = None,
                            data_json: Optional[Any] = None,
                            headers: Optional[Dict[str, Any]] = None,
                            method: str = 'GET') -> Optional['RemoteFileInfo']:
        """download binary file from `url` and store it into `path`"""


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


class AioHttpClient(HttpClient):

    def __init__(self, logger: logging.Logger, cookies_file: Optional[Path], headers: Optional[Dict[str, Any]]):
        super().__init__(logger, cookies_file, headers)

        netscape_cookies = load_cookies(cookies_file)
        cookies = convert_cookiejar(netscape_cookies) if netscape_cookies else None
        session = aiohttp.ClientSession(cookie_jar=cookies, headers=headers)
        self.session = session

    async def close(self) -> None:
        if not self.session.closed:
            self.logger.debug(f'closing session')
            await self.session.close()

    @property
    def cookie_jar(self) -> AnotherCookieJar:
        return AnotherAiohttpCookieJar(self.session.cookie_jar) # type: ignore

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
            factory = HttpResponse
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

    async def request_once(self, url: str,
                           params: Optional[Dict[str, str]] = None,
                           data: Optional[Any] = None,
                           data_json: Optional[Any] = None,
                           headers: Optional[Dict[str, Any]] = None,
                           method: str = 'GET',
                           state: EndpointState = EndpointState(),
                           ) -> 'MaybeHttpResponse':
        logger = self.logger

        request_headers: Dict[str, str] = {}
        if self.session.headers is not None:
            request_headers.update(self.session.headers)
        if headers is not None:
            request_headers.update(headers)
        if state.last_modified is not None and method in ['GET', 'HEAD']:
            request_headers['If-Modified-Since'] = state.last_modified
        if state.etag is not None:
            request_headers['If-None-Match'] = state.etag
        server_hostname = request_headers.get('Host')
        request_headers = insert_useragent(request_headers)
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

        response = self.from_response(client_response, text, state, logger)
        return response

    async def download_file(self, path: Path,
                            url: str,
                            params: Optional[Dict[str, str]] = None,
                            data: Optional[Any] = None,
                            data_json: Optional[Any] = None,
                            headers: Optional[Dict[str, Any]] = None,
                            method: str = 'GET') -> Optional['RemoteFileInfo']:
        headers = insert_useragent(headers)
        try:
            timeout = aiohttp.ClientTimeout(total=0, connect=60, sock_connect=60, sock_read=60)
            async with self.session.request(method, url,
                                            params=params, data=data, json=data_json,
                                            timeout=timeout, headers=headers) as response:
                response.raise_for_status()
                remote_info = RemoteFileInfo.from_url_response(url, response.headers)
                self.logger.debug(f'downloading {str(remote_info.content_length) + " bytes" or ""} from "{url}"')
                with open(path, 'w+b') as fp:
                    async for data in response.content.iter_chunked(CHUNK_SIZE):
                        fp.write(data)
        except (OSError, asyncio.TimeoutError, aiohttp.ClientConnectionError, aiohttp.ClientResponseError) as e:
            self.logger.warning(f'failed to download "{url}": {type(e)} {e}')
            return None
        except Exception as e:
            self.logger.exception(f'failed to download "{url}": {e}')
            return None
        return remote_info

def headers_dict(h: curl_cffi.Headers) -> Dict[str, str]:
    return {k: v for k, v in h.items() if v is not None}


class CurlCffiHttpClient(HttpClient):

    @dataclass
    class Options:
        use_own_ua: bool = False
        impersonate: curl_cffi.BrowserTypeLiteral = 'chrome'
        http_version: Optional[Literal['v1', 'v2', 'v2tls', 'v2_prior_knowledge', 'v3', 'v3only']] = 'v2tls'


    def __init__(self, logger: logging.Logger, cookies_file: Optional[Path], headers: Optional[Dict[str, Any]]):
        super().__init__(logger, cookies_file, headers)
        self.options = self.Options()

        netscape_cookies = load_cookies(cookies_file)
        self.session: curl_cffi.requests.AsyncSession = curl_cffi.requests.AsyncSession(cookies=netscape_cookies, headers=headers)

    async def close(self) -> None:
        self.logger.debug(f'closing session')
        await self.session.close()

    @property
    def cookie_jar(self) -> AnotherCookieJar:
        return AnotherCurlCffiCookieJar(self.session.cookies)

    @classmethod
    def from_response(cls, response: curl_cffi.Response, state: EndpointState, logger: logging.Logger):
        has_content = 200 <= response.status_code < 300
        factory: type[HttpResponse]
        if has_content:
            factory = DataResponse
        elif response.ok:
            factory = GoodResponse
        elif not response.ok:
            factory = BadResponse
        else:
            factory = HttpResponse
        response = factory(
            logger,
            response.text,
            str(response.url),
            response.ok,
            has_content,
            response.status_code,
            response.reason or 'No reason',
            headers_dict(response.headers),
            headers_dict(response.request.headers) if response.request is not None else {},
            response.cookies.jar,
            state,
            response.encoding
        )
        return response

    async def request_once(self, url: str,
                           params: Optional[Dict[str, str]] = None,
                           data: Optional[Any] = None,
                           data_json: Optional[Any] = None,
                           headers: Optional[Dict[str, Any]] = None,
                           method: str = 'GET',
                           state: EndpointState = EndpointState(),
                           ) -> 'MaybeHttpResponse':
        logger = self.logger

        request_headers: Dict[str, str] = {}
        if headers is not None:
            request_headers.update(headers)
        if state.last_modified is not None and method in ['GET', 'HEAD']:
            request_headers['If-Modified-Since'] = state.last_modified
        if state.etag is not None:
            request_headers['If-None-Match'] = state.etag
        if self.options.use_own_ua:
            request_headers = insert_useragent(request_headers)
        try:
            client_response: curl_cffi.Response = await self.session.request(
                method=method,  # type: ignore
                url=url,
                params=params,
                headers=request_headers,
                data=data,
                json=data_json,
                timeout=60,
                impersonate=self.options.impersonate,
                http_version=self.options.http_version
            )
        except Exception as e:
            logger.warning(f'error while fetching {url}: {e.__class__.__name__} {e}')
            return NoResponse(logger, e, url)

        if not client_response.ok:
            logger.warning(
                f'got code {client_response.status_code} ({client_response.reason or "No reason"}) while fetching {url}')
            if client_response.request is not None:
                logger.debug(f'request headers: "{client_response.request.headers}"')
            logger.debug(f'response headers: "{client_response.headers}"')
            logger.debug(f'response body: "{client_response.text}"')
        elif client_response.status_code != 304:
            # some servers do not have cache headers in 304 response, so only updating on 200
            state.update(headers_dict(client_response.headers))

            cache_control = client_response.headers.get('Cache-control')
            logger.debug(
                f'Last-Modified={state.last_modified or "absent"}, ETAG={state.etag or "absent"}, Cache-control="{cache_control or "absent"}" for {client_response.url}')

        response = self.from_response(client_response, state, logger)
        return response

    async def download_file(self, path: Path,
                            url: str,
                            params: Optional[Dict[str, str]] = None,
                            data: Optional[Any] = None,
                            data_json: Optional[Any] = None,
                            headers: Optional[Dict[str, Any]] = None,
                            method: str = 'GET') -> Optional['RemoteFileInfo']:
        if self.options.use_own_ua:
            headers = insert_useragent(headers)
        try:
            async with self.session.stream(method=method,  # type: ignore
                                                 url=url,
                                                 params=params,
                                                 headers=headers,
                                                 data=data,
                                                 json=data_json,
                                                 timeout=60,
                                                 ) as response:
                response.raise_for_status()
                remote_info = RemoteFileInfo.from_url_response(url, headers_dict(response.headers))
                self.logger.debug(f'downloading {str(remote_info.content_length) + " bytes" or ""} from "{url}"')
                with open(path, 'w+b') as fp:
                    async for data in response.aiter_content():
                        fp.write(data)
        except (OSError, curl_cffi.exceptions.RequestException) as e:
            self.logger.warning(f'failed to download "{url}": {type(e)} {e}')
            return None
        except Exception as e:
            self.logger.exception(f'failed to download "{url}": {e}')
            return None
        return remote_info


class Transport(str, Enum):
    AIOHTTP = 'aiohttp'
    CURL_CFFI = 'curl_cffi'

    @classmethod
    def get_implementation(cls, name: 'Transport') -> type[HttpClient]:
        if name == cls.AIOHTTP:
            return AioHttpClient
        elif name == cls.CURL_CFFI:
            return CurlCffiHttpClient
        else:
            raise NotImplementedError(f'unknown transport: "{name}"')


class ClientPool:
    """
    Store and reuse HttpClient instances and manage associated sessions livecycle
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.clients: Dict[str, HttpClient] = {}
        self.task: Optional[asyncio.Task] = None
        self.logger = (logger or logging.getLogger()).getChild('client_pool')

    @classmethod
    def get_client_id(cls, cookies_file: Optional[Path],
                      headers: Optional[Dict[str, Any]],
                      name: str = '',
                      logger: Optional[logging.Logger] = None) -> str:
        return name + str((cookies_file, headers)) + str(logger.name if logger is not None else '')

    def get_client_by_id(self, client_id: str) -> Optional[HttpClient]:
        """Return cached client instance if present"""
        return self.clients.get(client_id)

    def get_client(self, cookies_file: Optional[Path] = None,
                   headers: Optional[Dict[str, Any]] = None,
                   name: str = '',
                   logger: Optional[logging.Logger] = None,
                   transport: Transport = Transport.AIOHTTP) -> HttpClient:
        """return new or cached HttpClient instance"""
        client_id = self.get_client_id(cookies_file, headers, name, logger)
        if client_id in self.clients:
            return self.clients[client_id]
        logger = logger or logging.getLogger(f'HttpClient[{self.get_client_id(cookies_file, headers, name)}]')
        HttpClientImplementation = Transport.get_implementation(transport)
        client = HttpClientImplementation(logger, cookies_file, headers)
        self.clients[client_id] = client
        return client

    async def close(self) -> None:
        """close sessions for all cached clients"""
        self.logger.debug('closing http sessions...')
        for client_id, client in self.clients.items():
            await client.close()
        self.logger.debug('all http sessions closed')

    async def ensure_closed(self) -> None:
        try:
            await asyncio.Future()
        except (asyncio.CancelledError, KeyboardInterrupt):
            await self.close()
