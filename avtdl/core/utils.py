import abc
import asyncio
import datetime
import hashlib
import http.cookies
import json
import logging
import os
import re
import time
from collections import OrderedDict
from contextlib import ContextDecorator
from email.utils import mktime_tz, parsedate_to_datetime
from enum import Enum
from http import cookiejar
from math import log2
from pathlib import Path
from textwrap import shorten
from time import perf_counter
from typing import Any, Callable, Dict, Hashable, Iterable, List, Optional, Set, Tuple, Union

import aiohttp
import lxml.html
import multidict
from jsonpath import JSONPath
from multidict import CIMultiDictProxy

from avtdl.core.interfaces import Record


def load_cookies(path: Optional[Path], raise_on_error: bool = False) -> Optional[cookiejar.CookieJar]:
    """load cookies from a text file in Netscape format"""
    logger = logging.getLogger('cookies')
    if path is None:
        return None
    cookie_jar = cookiejar.MozillaCookieJar(path)
    try:
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        logger.info(f"Successfully loaded cookies from {path}")
    except FileNotFoundError:
        logger.exception(f'Failed to load cookies from {path}: file not found')
        if raise_on_error:
            raise
        return None
    except (cookiejar.LoadError, OSError) as e:
        if raise_on_error:
            raise
        logger.exception(f'Failed to load cookies from {path}: {e}')
        return None
    return cookie_jar


def convert_cookiejar(cookie_jar: cookiejar.CookieJar) -> aiohttp.CookieJar:
    """convert cookie jar produced by stdlib to format used by aiohttp"""
    cookies: http.cookies.BaseCookie = http.cookies.BaseCookie()
    for cookie in cookie_jar:
        name = cookie.name
        cookies[name] = cookie.value or ''
        cookies[name]['domain'] = cookie.domain
        cookies[name]['path'] = cookie.path
        cookies[name]['expires'] = str(cookie.expires)
        cookies[name]['secure'] = cookie.secure
        cookies[name]['version'] = str(cookie.version)
        cookies[name]['comment'] = cookie.comment
    new_jar = aiohttp.CookieJar(quote_cookie=False)
    new_jar.update_cookies(cookies)
    return new_jar


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


def check_dir(path: Path, create=True) -> bool:
    """check if directory exists and writable, create if asked"""
    if path.is_dir() and os.access(path, mode=os.W_OK):
        return True
    elif create:
        logging.info(f'directory {path} does not exists, creating')
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            logging.warning(f'failed to create directory at {path}: {e}')
            return False
    else:
        return False


def make_datetime(items) -> datetime.datetime:
    """take 10-tuple and return datetime object with UTC timezone"""
    if len(items) == 9:
        items = *items, None
    if len(items) != 10:
        raise ValueError(f'Expected tuple with 10 elements, got {len(items)}')
    timestamp = mktime_tz(items)
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)


def parse_timestamp_us(timestamp: Union[str, int, None], ) -> Optional[datetime.datetime]:
    return parse_timestamp(timestamp, 6)


def parse_timestamp_ms(timestamp: Union[str, int, None], ) -> Optional[datetime.datetime]:
    return parse_timestamp(timestamp, 3)


def parse_timestamp(timestamp: Union[str, int, None], fraction: int) -> Optional[datetime.datetime]:
    """parse UNIX timestamp as datetime.datetime"""
    if timestamp is None:
        return None
    try:
        ts = int(timestamp)
        dt = datetime.datetime.fromtimestamp(int(ts / 10 ** fraction), tz=datetime.timezone.utc)
        return dt
    except Exception:
        return None


def show_diff(dict1: Dict[str, Any], dict2: Dict[str, Any]) -> str:
    """pretty-print keys which values in dict1 and dict2 are different"""
    keys = {*dict1.keys(), *dict2.keys()}
    diff = []
    for k in keys:
        v1 = str(dict1.get(k, ''))
        repr_v1 = shorten(v1, 60)
        v2 = str(dict2.get(k, ''))
        repr_v2 = shorten(v2, 60)
        if v1 != v2:
            diff.append(f'[{k[:12]:12}]: {repr_v2:60} |->| {repr_v1:60}')
    return '\n'.join(diff)


async def monitor_tasks(tasks: Iterable[asyncio.Task], logger: Optional[logging.Logger] = None) -> None:
    """given list of running tasks, wait for them and report any unhandled exceptions"""
    logger = logger or logging.getLogger()
    tasks = set(tasks)
    while True:
        if not tasks:
            break
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            if not task.done():
                continue
            if task.exception() is not None:
                logger.error(f'task {task.get_name()} has terminated with exception', exc_info=task.exception())
        tasks = pending


async def monitor_tasks_set(tasks: Set[asyncio.Task], poll_interval: float = 5, logger: Optional[logging.Logger] = None) -> None:
    """given link to a set of tasks, check on them and remove completed or failed"""
    logger = logger or logging.getLogger()
    while True:
        if not tasks:
            await asyncio.sleep(poll_interval)
            continue
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION, timeout=poll_interval)
        for task in done:
            if not task.done():
                continue
            if task.exception() is not None:
                logger.error(f'task {task.get_name()} has terminated with exception', exc_info=task.exception())
            tasks.discard(task)


async def monitor_tasks_dict(tasks: Dict[str, asyncio.Task], poll_interval: float = 5, logger: Optional[logging.Logger] = None) -> None:
    """given link to a dict with tasks, check on them and remove completed or failed"""
    logger = logger or logging.getLogger()
    while True:
        if not tasks:
            await asyncio.sleep(poll_interval)
            continue
        done, pending = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_EXCEPTION, timeout=poll_interval)
        if not done:
            continue
        finished_tasks = {name: task for name, task in tasks.items() if task in done}

        for name, task in finished_tasks.items():
            if not task.done():
                continue
            if task.exception() is not None:
                logger.error(f'task {task.get_name()} has terminated with exception', exc_info=task.exception())
            tasks.pop(name)


async def request_raw(url: str, session: Optional[aiohttp.ClientSession], logger: Optional[logging.Logger] = None,
                      method: str = 'GET', params: Optional[Any] = None, data: Optional[Any] = None,
                      headers: Optional[Dict[str, Any]] = None, retry_times: int = 1, retry_delay: float = 1,
                      retry_multiplier: int = 1,
                      raise_errors: bool = False, raise_for_status: bool = True) -> Optional[aiohttp.ClientResponse]:
    logger = logger if logger else logging.getLogger('request')
    current_retry_delay = retry_delay
    for attempt in range(0, retry_times + 1):
        last_attempt = attempt == retry_times
        try:
            request: Callable = aiohttp.request
            if session is not None:
                request = session.request
                if session.headers is not None and headers is not None:
                    headers.update(session.headers)

            async with request(method=method, url=url, headers=headers, params=params, data=data) as response:
                if raise_for_status:
                    response.raise_for_status()
                _ = await response.text()
                return response

        except Exception as e:
            if not last_attempt:
                logger.warning(f'error when requesting {url}: {e}, retrying in {current_retry_delay:.02f} seconds')
                await asyncio.sleep(current_retry_delay)
                current_retry_delay *= retry_multiplier
                continue
            elif raise_errors:
                raise
            else:
                logger.warning(f'error when requesting {url}: {e}')
                return None
    return None


async def request(url: str, session: Optional[aiohttp.ClientSession] = None, logger: Optional[logging.Logger] = None,
                  method: str = 'GET', params: Optional[Any] = None, data: Optional[Any] = None,
                  headers: Optional[Dict[str, Any]] = None, retry_times: int = 1, retry_delay: float = 1,
                  retry_multiplier: int = 1,
                  raise_errors: bool = False) -> Optional[str]:
    logger = logger if logger else logging.getLogger('request')
    response = await request_raw(url, session, logger, method, params, data, headers, retry_times, retry_delay, retry_multiplier, raise_errors)
    if response is None:
        return None
    if response.status >= 300:
        # presuming that there is no point in retrying 3xx and aiohttp will follow redirects transparently
        logger.debug(f'got {response.status} ({response.reason}) from {url}')
        return None
    return await response.text()


async def request_json(url: str, session: Optional[aiohttp.ClientSession], logger: Optional[logging.Logger] = None,
                       method: str = 'GET', params: Optional[Any] = None, data: Optional[Any] = None,
                       headers: Optional[Dict[str, Any]] = None, retry_times: int = 1, retry_delay: float = 1,
                       retry_multiplier: int = 1,
                       raise_errors: bool = False) -> Optional[Any]:
    logger = logger if logger else logging.getLogger('request_json')
    text = await request(url, session, logger, method, params, data, headers, retry_times, retry_delay, retry_multiplier, raise_errors)
    if text is None:
        return None
    try:
        parsed = json.loads(text)
        return parsed
    except json.JSONDecodeError as e:
        logger.debug(f'error parsing response from {url}: {e}. Raw response data: "{text}"')
        if raise_errors:
            raise
        else:
            return None


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


class timeit(ContextDecorator):
    """measure time call takes, print it in the log"""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.start: float = 0
        self.end: float = 0
        self.logger = logger

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def timedelta(self) -> datetime.timedelta:
        return datetime.timedelta(seconds=self.duration)

    def __enter__(self):
        self.start = perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end = perf_counter()
        if self.logger is not None:
            self.logger.debug(f'took {self.timedelta}')
        return False


class LRUCache:

    def __init__(self, max_size: int = 100):
        if max_size <= 0:
            raise ValueError('Maximum cache size must be a positive integer')
        self._max_size = max_size
        self._data: OrderedDict = OrderedDict()

    def put(self, item: Hashable):
        """Put item in the cache, resize the cache if needed"""
        if not item in self._data:
            self._data[item] = 1
        self._data.move_to_end(item)
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)


def find_matching_field(record: Record, pattern: str, fields: Optional[List[str]] = None) -> Optional[str]:
    name, _ = find_matching_field_name_and_value(record, pattern, fields)
    return name


def find_matching_field_value(record: Record, pattern: str, fields: Optional[List[str]] = None) -> Optional[str]:
    _, value = find_matching_field_name_and_value(record, pattern, fields)
    return value


def find_matching_field_name_and_value(record: Record, pattern: str, fields: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[Any]]:
    """
    Return name of the first field of the record that contains pattern,
    return None if nothing found. If fields value specified only check
    fields listed in there.
    """
    for field, value in record:
        if fields is not None and field not in fields:
            continue
        if isinstance(value, Record):
            subrecord_search_result = find_matching_field_name_and_value(value, pattern)
            if subrecord_search_result is not None:
                return subrecord_search_result
        else:
            if str(value).find(pattern) > -1:
                return field, value
    return None, None


def record_has_text(record: Record, text: str) -> bool:
    return find_matching_field(record, text) is not None


def sanitize_filename(name: str) -> str:
    """Replace symbols not allowed in file names on NTFS with underscores"""
    return re.sub(r'[\\/:*?"<>|]', "_", name)


class OutputFormat(str, Enum):
    str = 'text'
    repr = 'short'
    json = 'json'
    pretty_json = 'pretty_json'
    hash = 'hash'


class Fmt:
    """Helper class to interpolate format string from config using data from Record"""

    @classmethod
    def format(cls, fmt: str, record: Record, missing: Optional[str] = None, tz: Optional[datetime.timezone] = None, sanitize: bool = False, extra: Optional[Dict[str, Any]] = None) -> str:
        """Take string with placeholders like {field} and replace them with record fields"""
        logger = logging.getLogger().getChild('format')
        result = cls.strftime(fmt, datetime.datetime.now(tz))
        record_as_dict = record.model_dump()
        if extra is not None:
            record_as_dict.update(extra)
        placeholders: List[str] = re.findall(r'({[^{}\\]+})', fmt)
        for placeholder in placeholders:
            field = placeholder.strip('{}')
            value = record_as_dict.get(field)
            if value is not None:
                value = cls.format_value(value, sanitize)
                result = result.replace(placeholder, value)
            else:
                if missing is not None:
                    result = result.replace(placeholder, missing)
                else:
                    logger.warning(f'placeholder "{placeholder}" used by format string "{fmt}" is not a field of {record.__class__.__name__} ({record!r}), resulting command is unlikely to be valid')
        result = result.replace(r'\{', '{')
        result = result.replace(r'\}', '}')
        return result
    
    @classmethod
    def format_value(cls, value: Any, sanitize: bool = False) -> str:
        if value is None:
            value = ''
        elif isinstance(value, datetime.datetime):
            value = cls.date(value)
        else:
            value = str(value)
            if sanitize:
                value = sanitize_filename(value)
        return value

    @classmethod
    def format_path(cls, path: Union[str, Path], record: Record, missing: Optional[str] = None, tz: Optional[datetime.timezone] = None, extra: Optional[Dict[str, Any]] = None) -> Path:
        """Take string with placeholders and replace them with record fields, but strip them from bad symbols"""
        fmt = str(path)
        formatted_path = cls.format(fmt, record, missing, tz=tz, sanitize=True, extra=extra)
        return Path(formatted_path)

    @classmethod
    def strftime(cls, fmt: str, dt: datetime.datetime) -> str:
        if '%' in fmt:
            fmt = re.sub(r'(%[^aAwdbBmyYHIpMSfzZjUWcxX%GuV])', r'%\1', fmt)
        try:
            return dt.strftime(fmt)
        except ValueError as e:
            logger = logging.getLogger().getChild('format').getChild('strftime')
            logger.debug(f'error adding current date to template "{fmt}": {e}')
            return fmt

    @classmethod
    def date(cls, dt: datetime.datetime) -> str:
        return dt.strftime('%Y-%m-%d %H:%M')

    @classmethod
    def dtf(cls, dt: datetime.datetime) -> str:
        """format datetime to Discord timestamp"""
        ts = int(dt.timestamp())
        return f'<t:{ts}>'

    @classmethod
    def save_as(cls, record: Record, output_format: OutputFormat = OutputFormat.str) -> str:
        """Take a record and convert in to string as text/json or sha1"""
        if output_format == OutputFormat.str:
            return str(record)
        if output_format == OutputFormat.repr:
            return repr(record)
        if output_format == OutputFormat.json:
            return record.as_json()
        if output_format == OutputFormat.pretty_json:
            return record.as_json(2)
        if output_format == OutputFormat.hash:
            return record.hash()


def html_to_text(html: str) -> str:
    """take html fragment, try to parse it and extract text values using lxml"""
    try:
        root = lxml.html.fromstring(html)

        # text_content() skips <img> content altogether
        # walk tree manually and for images containing links
        # add them to text representation
        for elem in root.iter():
            if elem.tag == 'img':
                image_link = elem.get('src')
                if image_link is not None:
                    elem.text = f'\n{image_link}\n'
        text = root.text_content()
        return text
    except Exception as e:
        logger = logging.getLogger('html_to_text')
        logger.warning(e)
        return html


def read_file(path: Union[str, Path], encoding=None) -> str:
    """
    Read and return file content in provided encoding

    If decoding file content in provided encoding fails, try again using utf8.
    If it also fails, let the exception to propagate. Handling OSError is also
    left to caller.
    """
    with open(path, 'rt', encoding=encoding) as fp:
        try:
            text = fp.read()
            return text
        except UnicodeDecodeError:
            pass
    with open(path, encoding='utf8') as fp:
        text = fp.read()
        return text


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).digest().hex()


def get_cookie_value(jar: aiohttp.CookieJar, key: str) -> Optional[str]:
    for morsel in jar:
        if morsel.key == key:
            return morsel.value
    return None


def find_all(data: Union[dict, list], jsonpath: str, cache={}) -> list:
    if jsonpath not in cache:
        cache[jsonpath] = JSONPath(jsonpath)
    parser = cache[jsonpath]
    return parser.parse(data)


def find_one(data: Union[dict, list], jsonpath: str) -> Optional[Any]:
    result = find_all(data, jsonpath)
    return result[0] if result else None


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
       return name +  str((cookies_file, headers))

    def get_session_by_id(self, session_id: str) -> Optional[aiohttp.ClientSession]:
        return self.sessions.get(session_id)

    def session_exists(self, cookies_file: Optional[Path], headers: Optional[Dict[str, Any]], name: str = '') -> bool:
        session_id = self.get_session_id(cookies_file, headers, name)
        session = self.get_session_by_id(session_id)
        return session is not None

    def get_session(self, cookies_file: Optional[Path] = None, headers: Optional[Dict[str, Any]] = None, name: str = '') -> aiohttp.ClientSession:
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
            self.logger.debug('closing http sessions...')
            for session_id, session in self.sessions.items():
                if not session.closed:
                    self.logger.debug(f'closing session "{session_id}"')
                    await session.close()
            self.logger.debug('done')

    def run(self) -> None:
       if self.task is None:
           name = f'ensure_closed for {self.logger.name} ({self!r})'
           self.task = asyncio.create_task(self.ensure_closed(), name=name)


class RateLimit:
    """
    Encapsulate state of rate limits for "bucket of tokens" type of endpoint

    >>> async def endpoint_request(url):
    >>>     async with RateLimit('endpoint name') as rate_limit:
    >>>         response = await request_raw(url)
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
            self.logger.debug(f'[{self.name}] lock acquired in {t.timedelta}, {self.delay} seconds until rate limit reset')
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

    def submit_headers(self, headers: Union[Dict[str, str], CIMultiDictProxy[str]], logger: Optional[logging.Logger] = None):
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
            self.reset_at = int((datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(seconds=retry_after)).timestamp())
            logger.debug(f'[{self.name}] Retry-After is present and set to {retry_after}, setting reset_at to {self.reset_at}')
            return

        self._submit_headers(headers, logger)
