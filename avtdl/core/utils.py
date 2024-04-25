import asyncio
import datetime
import hashlib
import http
import json
import logging
import os
import re
from collections import OrderedDict
from email.utils import mktime_tz, parsedate_to_datetime
from enum import Enum
from http import cookiejar
from math import log2
from pathlib import Path
from textwrap import shorten
from time import perf_counter_ns
from typing import Any, Callable, Dict, Hashable, Iterable, List, Optional, Union

import aiohttp
import lxml.html
import multidict

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
    cookies = http.cookies.BaseCookie()
    for cookie in cookie_jar:
        name = cookie.name
        cookies[name] = cookie.value
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

    def get_expires_from_cache_control(headers) -> Optional[datetime.datetime]:
        try:
            cache_control = headers.get('Cache-control')
            max_age = re.search('max-age=(\d+)', cache_control)
            max_age_value = datetime.timedelta(seconds=int(max_age))

            last_modified = headers.get('Last-Modified')
            last_modified_value = parsedate_to_datetime(last_modified)

            expires = last_modified_value + max_age_value
            return expires
        except (TypeError, ValueError):
            return None

    def get_expires_from_expires_header(headers) -> Optional[datetime.datetime]:
        try:
            expires_header = headers.get('Expires')
            if expires_header == '0':
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


def get_retry_after(headers: multidict.CIMultiDictProxy) -> Optional[int]:
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
        logging.warning(f'directory {path} does not exists, creating')
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


async def monitor_tasks(tasks: Iterable[asyncio.Task]) -> None:
    """given list of running tasks, wait for them and report any unhandled exceptions"""
    while True:
        if not tasks:
            break
        tasks = await check_tasks(tasks)


async def check_tasks(tasks: Iterable[asyncio.Task]) -> List[asyncio.Task]:
    """given list of running tasks, wait until any of them are done,
    report unhandled exceptions and return a list of these that are still running"""
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for task in done:
        if not task.done():
            continue
        if task.cancelled():
            continue
        if task.exception() is not None:
            logging.error(f'task {task.get_name()} has terminated with exception', exc_info=task.exception())
    return list(pending)


async def request_raw(url: str, session: Optional[aiohttp.ClientSession], logger: Optional[logging.Logger] = None,
                      method: str = 'GET', params: Optional[Any] = None, data: Optional[Any] = None,
                      headers: Optional[Dict[str, Any]] = None, retry_times: int = 1, retry_delay: float = 1,
                      retry_multiplier: int = 1,
                      raise_errors: bool = False) -> Optional[aiohttp.ClientResponse]:
    logger = logger if logger else logging.getLogger('request')
    current_retry_delay = retry_delay
    for attempt in range(0, retry_times + 1):
        last_attempt = attempt == retry_times
        try:
            if session is not None:
                if session.headers is not None and headers is not None:
                    headers.update(session.headers)
                async with session.request(method=method, url=url, headers=headers, params=params, data=data) as response:
                    response.raise_for_status()
                    _ = await response.text()
                    return response
            else:
                async with aiohttp.request(method=method, url=url, headers=headers, params=params, data=data) as response:
                    response.raise_for_status()
                    _ = await response.text()
                    return response

        except Exception as e:
            logger.warning(f'error when requesting {url}: {e}')
            if not last_attempt:
                logger.debug(f'next attempt in {current_retry_delay:.02f} seconds')
                await asyncio.sleep(current_retry_delay)
                current_retry_delay *= retry_multiplier
                continue
            elif raise_errors:
                raise
            else:
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


def timeit(func: Callable) -> Callable:
    """measure time "func()" call takes, print it in log"""

    def timer(*args, **kwargs) -> Any:
        begin = perf_counter_ns()
        result = func(*args, **kwargs)
        duration = perf_counter_ns() - begin
        logging.warning(f'{func.__name__}: {duration / 10 ** 6:10}')
        return result

    return timer


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
    """
    Return name of the first field of the record that contains pattern,
    return None if nothing found. If fields value specified only check
    fields listed in there.
    """
    for field, value in record:
        if fields is not None and field not in fields:
            continue
        if isinstance(value, Record):
            subrecord_search_result = find_matching_field(value, pattern)
            if subrecord_search_result is not None:
                return subrecord_search_result
        else:
            if str(value).find(pattern) > -1:
                return field
    return None


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
    def format(cls, fmt: str, record: Record, missing: Optional[str] = None, tz: Optional[datetime.timezone] = None, sanitize: bool = False) -> str:
        """Take string with placeholders like {field} and replace them with record fields"""
        logger = logging.getLogger().getChild('format')
        result = cls.strftime(fmt, datetime.datetime.now(tz))
        record_as_dict = record.model_dump()
        placeholders: List[str] = re.findall(r'({[^{}\\]+})', fmt)
        if not placeholders and result == fmt:
            logger.debug(f'format string "{fmt}" has no placeholders, it will be the same for all records')
        for placeholder in placeholders:
            field = placeholder.strip('{}')
            value = record_as_dict.get(field)
            if value is not None:
                if isinstance(value, datetime.datetime):
                    value = cls.date(value)
                else:
                    value = str(value)
                    if sanitize:
                        value = sanitize_filename(value)
                result = result.replace(placeholder, value)
            else:
                if missing is not None:
                    result = result.replace(placeholder, missing)
                else:
                    logger.warning(f'placeholder "{placeholder}" used by format string "{fmt}" is not a field of {record.__class__.__name__} ({record!r}), resulting command is unlikely to be valid')
        return result

    @classmethod
    def format_path(cls, path: Union[str, Path], record: Record, missing: Optional[str] = None, tz: Optional[datetime.timezone] = None) -> Path:
        """Take string with placeholders and replace them with record fields, but strip them from bad symbols"""
        fmt = str(path)
        formatted_path = cls.format(fmt, record, missing, tz=tz, sanitize=True)
        return Path(formatted_path)

    @classmethod
    def strftime(cls, fmt: str, dt: datetime.datetime) -> str:
        try:
            return dt.strftime(fmt)
        except ValueError as e:
            logger = logging.getLogger().getChild('format').getChild('strftime')
            logger.warning(f'error using formate template "{fmt}": {e}')
            return fmt

    @classmethod
    def date(cls, dt: datetime.datetime) -> str:
        return dt.strftime('%Y-%m-%d %H:%M')

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
