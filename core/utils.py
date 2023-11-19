import asyncio
import datetime
import http
import json
import logging
import os
import re
import sqlite3
from email.utils import mktime_tz, parsedate_to_datetime
from http import cookiejar
from math import log2
from pathlib import Path
from textwrap import shorten
from time import perf_counter_ns
from typing import Any, Dict, Optional, Union, Callable

import aiohttp
import multidict


def load_cookies(path: Optional[Path], raise_on_error: bool = False) -> Optional[cookiejar.CookieJar]:
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
    cookies = http.cookies.SimpleCookie()
    for cookie in cookie_jar:
        name = cookie.name
        cookies[name] = cookie.value
        cookies[name]['domain'] = cookie.domain
        cookies[name]['path'] = cookie.path
        cookies[name]['expires'] = str(cookie.expires)
        cookies[name]['secure'] = cookie.secure
        cookies[name]['version'] = str(cookie.version)
        cookies[name]['comment'] = cookie.comment
    new_jar = aiohttp.CookieJar()
    new_jar.update_cookies(cookies)
    return new_jar

def get_cache_ttl(headers: multidict.CIMultiDictProxy) -> Optional[int]:
    '''Check for Expires and Cache-Control headers,
    return integer representing how many seconds is
    left until resource is outdated'''

    def get_expires_from_cache_control(headers) -> Optional[datetime.datetime]:
        try:
            cache_control = headers.get('Cache-control')
            max_age = re.search('max-age=(\d+)', cache_control)
            max_age_value = datetime.timedelta(seconds=int(max_age))

            last_modified = headers.get('Last-Modified')
            last_modified_value = parsedate_to_datetime(last_modified)

            expires  = last_modified_value + max_age_value
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
    retry_after =  headers.get('Retry-After')
    if retry_after is None:
        return None
    try:
        return int(retry_after)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(retry_after)
    except (TypeError, ValueError):
        pass
    return None


def check_dir(path: Path, create=True) -> bool:
    '''check if directory exists and writable, create if asked'''
    if path.is_dir() and os.access(path, mode=os.W_OK):
        return True
    elif create:
        logging.warning(f'directory {path} does not exists, creating')
        try:
            os.mkdir(path)
            return True
        except OSError as e:
            logging.warning(f'failed to create directory at {path}: {e}')
            return False
    else:
        return False

def make_datetime(items) -> datetime.datetime:
    '''take 10-tuple and return datetime object with UTC timezone'''
    if len(items) == 9:
       items = *items, None
    if len(items) != 10:
        raise ValueError(f'Expected tuple with 10 elements, got {len(items)}')
    timestamp = mktime_tz(items)
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)

def show_diff(dict1: Dict[str, Any], dict2: Dict[str, Any]) -> str:
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
                async with session.request(method=method, url=url, headers=headers, params=params, data=data) as response:
                    response.raise_for_status()
                    _ = await response.text()
                    return response
            else:
                async with aiohttp.request(method=method, url=url, headers=headers) as response:
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


class RecordDB:
    table_name = 'records'
    table_structure = 'parsed_at datetime, feed_name text, uid text, hashsum text, class_name text, as_json text, PRIMARY KEY(uid, hashsum)'
    row_structure = ':parsed_at, :feed_name, :author, :video_id, :url, :title, :summary, :published, :updated, :scheduled, :views'
    id_field = 'uid'
    exact_id_field = 'hashsum'
    group_id_field = 'feed_name'
    sorting_field = 'parsed_at'

    def __init__(self, db_path: Union[str,Path], logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger('RecordDB')
        try:
            if not db_path == ':memory:' and not Path(db_path).exists():
                check_dir(Path(db_path).parent)
            self.db = sqlite3.connect(db_path)
            self.db.row_factory = sqlite3.Row
            self.cursor = self.db.cursor()
            self.cursor.execute('CREATE TABLE IF NOT EXISTS {} ({})'.format(self.table_name, self.table_structure))
            self.db.commit()
        except sqlite3.OperationalError as e:
            self.logger.error(
                f'error opening sqlite database at path "{db_path}", specified in "db_path" config variable: {e}. If file exists make sure it was produced by this application, otherwise check if new file can be created at specified location. Alternatively use special value ":memory:" to use in-memory database instead.')
            raise
        else:
            self.logger.debug(f'successfully connected to sqlite database at "{db_path}"')

    def store(self, row: Dict[str, Any]) -> None:
        sql = "INSERT INTO {} VALUES({})".format(self.table_name, self.row_structure)
        self.cursor.execute(sql, row)
        self.db.commit()

    def fetch_row(self, uid: Any, exact_id: Optional[Any] = None) -> Optional[sqlite3.Row]:
        if exact_id is not None:
            sql = f'SELECT * FROM records WHERE {self.id_field}=:uid AND {self.exact_id_field}=:exact_id ORDER BY {self.sorting_field} DESC LIMIT 1'
        else:
            sql = f'SELECT * FROM records WHERE {self.id_field}=:uid ORDER BY {self.sorting_field} DESC LIMIT 1'
        keys = {'uid': uid, 'exact_id': exact_id}
        self.cursor.execute(sql, keys)
        return self.cursor.fetchone()

    def row_exists(self, uid: Any, exact_id: Optional[Any] = None) -> bool:
        return self.fetch_row(uid, exact_id) is not None

    def get_size(self, group: Optional[Any] = None) -> int:
        '''return number of records, total or for specified feed, are stored in db'''
        if group is None:
            sql = f'SELECT COUNT(1) FROM {self.table_name}'
        else:
            sql = f'SELECT COUNT(1) FROM {self.table_name} WHERE {self.group_id_field}=:group'
        keys = {'group': group}
        self.cursor.execute(sql, keys)
        return int(self.cursor.fetchone()[0])


class Delay:
    '''Provide method to calculate next delay for exponential backoff based on S-shaped curve'''

    A: float = 4600 # upper asymptote
    k: float = 0.88 # curve growth rate
    x0: float = 8 # x value corresponding to midpoint of the curve

    @classmethod
    def _sigmoid(cls, x: float) -> float:
        y = cls.A / (1 + 2 ** (-cls.k * (x - cls.x0)))
        return y

    @classmethod
    def _inv_sigmoid(cls, y: float) -> float:
        x = cls.x0 - log2((cls.A - y) / y) / cls.k
        return x

    @classmethod
    def get_next(cls, current: float) -> float:
        '''Find current value on S-shaped curve and return a next one'''
        current_step = cls._inv_sigmoid(current)
        next_step = current_step + 1
        next_delay = cls._sigmoid(next_step)
        return next_delay


def timeit(func: Callable) -> Callable:
  def timer(*args, **kwargs) -> Any:
      begin = perf_counter_ns()
      result = func(*args, **kwargs)
      duration = perf_counter_ns() - begin
      logging.warning(f'{func.__name__}: {duration/10**6:10}')
      return result
  return timer
