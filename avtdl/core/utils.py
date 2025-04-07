import asyncio
import base64
import datetime
import hashlib
import http.cookiejar
import http.cookies
import json
import logging
import os
import re
import urllib.parse
from collections import OrderedDict
from contextlib import ContextDecorator
from email.utils import mktime_tz
from enum import Enum
from http import cookiejar
from http.cookiejar import CookieJar
from pathlib import Path
from textwrap import shorten
from time import perf_counter
from typing import Any, Dict, Hashable, List, Mapping, MutableMapping, Optional, Tuple, Union

import aiohttp
import dateutil.parser
import lxml.html
from aiohttp.abc import AbstractCookieJar
from jsonpath import JSONPath

from avtdl.core.interfaces import Record

JSONType = Union[str, int, float, bool, None, Mapping[str, 'JSONType'], List['JSONType']]


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


class CookieStoreError(Exception):
    """Raised when save_cookies() failed"""


def save_cookies(cookies: AbstractCookieJar, path: str):
    try:
        cookie_jar = unconvert_cookiejar(cookies)
    except Exception as e:
        msg = f'error converting cookie jar: {e}'
        raise CookieStoreError(msg) from e
    try:
        cookie_jar.save(path, ignore_discard=True, ignore_expires=True)
    except Exception as e:
        msg = f'failed to store cookies to "{path}": {e}'
        raise CookieStoreError(msg) from e


def parse_to_timestamp(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    try:
        dt = dateutil.parser.parse(str(text))
    except Exception:
        return None
    return int(dt.timestamp())


def unconvert_cookiejar(cookies: AbstractCookieJar) -> cookiejar.MozillaCookieJar:
    cookie_jar = cookiejar.MozillaCookieJar()
    for morsel in cookies:
        domain = morsel.get('domain', '')
        expires = parse_to_timestamp(morsel.get('expires')) or 0
        cookie = http.cookiejar.Cookie(
            version=morsel.get('version') or 0,
            name=morsel.key,
            value=morsel.value,
            port=None,
            port_specified=False,
            domain=domain or '',
            domain_specified=bool(domain),
            domain_initial_dot=domain.startswith('.'),
            path=morsel.get('path', ''),
            path_specified=bool(morsel.get('path')),
            secure=morsel.get('secure') or False,
            expires=expires,
            discard=False,
            comment=morsel.get('comment'),
            comment_url=None,
            rest={},
        )
        cookie_jar.set_cookie(cookie)
    return cookie_jar


def parse_to_date_string(text: Union[int, str, None]) -> Optional[str]:
    if text is None:
        return None
    try:
        dt = dateutil.parser.parse(str(text))
    except Exception:
        return None
    date_string = dt.strftime('%a, %d-%b-%y %H:%M:%S GMT')
    return date_string


def convert_cookiejar(cookie_jar: cookiejar.CookieJar) -> aiohttp.CookieJar:
    """convert cookie jar produced by stdlib to format used by aiohttp"""
    cookies: http.cookies.BaseCookie = http.cookies.BaseCookie()
    for cookie in cookie_jar:
        name = cookie.name
        cookies[name] = cookie.value or ''
        cookies[name]['domain'] = cookie.domain or ''
        cookies[name]['path'] = cookie.path or ''
        cookies[name]['expires'] = parse_to_date_string(cookie.expires) or ''
        cookies[name]['secure'] = cookie.secure or ''
        cookies[name]['version'] = str(cookie.version) if cookie.version else ''
        cookies[name]['comment'] = cookie.comment or ''
    new_jar = aiohttp.CookieJar(quote_cookie=False)
    new_jar.update_cookies(cookies)
    return new_jar


def check_dir(path: Path, create=True) -> bool:
    """check if directory exists and writable, create if asked"""
    if path.is_dir() and os.access(path, mode=os.W_OK):
        return True
    elif create:
        logging.info(f'directory {path} does not exist, creating')
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
    """pretty-print keys that has different values in dict1 and dict2"""
    keys = {*dict1.keys(), *dict2.keys()}
    diff = []
    for k in keys:
        v1 = str(dict1.get(k, ''))
        repr_v1 = shorten(v1, 60)
        v2 = str(dict2.get(k, ''))
        repr_v2 = shorten(v2, 60)
        if v1 != v2 and json.dumps(v1, sort_keys=True) != json.dumps(v2, sort_keys=True):
            diff.append(f'[{k[:12]:12}]: {repr_v2:60} |->| {repr_v1:60}')
    return '\n'.join(diff)


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


def find_matching_field_name_and_value(record: Record, pattern: str, fields: Optional[List[str]] = None) -> Tuple[
    Optional[str], Optional[Any]]:
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
    text = 'text'
    repr = 'short'
    json = 'json'
    pretty_json = 'pretty_json'
    hash = 'hash'


class Fmt:
    """Helper class to interpolate format string from config using data from Record"""

    @classmethod
    def format(cls, fmt: str, record: Record, missing: Optional[str] = None, tz: Optional[datetime.tzinfo] = None,
               sanitize: bool = False, extra: Optional[Dict[str, Any]] = None) -> str:
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
                    logger.warning(
                        f'placeholder "{placeholder}" used by format string "{fmt}" is not a field of {record.__class__.__name__} ({record!r}), resulting command is unlikely to be valid')
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
    def format_path(cls, path: Union[str, Path], record: Record, missing: Optional[str] = None,
                    tz: Optional[datetime.tzinfo] = None, extra: Optional[Dict[str, Any]] = None) -> Path:
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
    def size(cls, size: Union[int, float]) -> str:
        for unit in ['B', 'kB', 'MB', 'GB', 'TB']:
            if size < 1024:
                break
            size /= 1024
        return f"{size:.2f} {unit}"

    @classmethod
    def duration(cls, seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02}:{seconds:02}"

    @classmethod
    def dtf(cls, dt: datetime.datetime) -> str:
        """format datetime to Discord timestamp"""
        ts = int(dt.timestamp())
        return f'<t:{ts}>'

    @classmethod
    def save_as(cls, record: Record, output_format: OutputFormat = OutputFormat.text) -> str:
        """Take a record and convert in to string as text/json or sha1"""
        if output_format == OutputFormat.text:
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
    If it also fails, let the exception propagate. Handling OSError is also
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


def write_file(path: Union[str, Path], content: str, encoding='utf8', backups: int = 0):
    if backups > 0:
        rotate_file(path, depth=backups)
    with open(path, 'wt', encoding=encoding) as fp:
        fp.write(content)


def rotate_file(path: Union[str, Path], depth: int = 10):
    """Move "path" to "path.1", "path.1" to "path.2" and so on down to depth parameter"""
    increment_postfix(path, depth)


def increment_postfix(path: Union[str, Path], maxdepth):
    path = Path(path)
    if not path.exists():
        return
    if re.match(r'\.(\d|[1-9]\d+)$', path.suffix):
        index = int(path.suffix.strip('.'))
        next_path = path.with_suffix(f'.{index + 1}')
    else:
        index = 0
        next_path = path.with_suffix(path.suffix + '.0')
    if index >= maxdepth:
        return
    increment_postfix(next_path, maxdepth)
    logging.getLogger('rotate').info(f'moving {path} to {next_path}')
    path.replace(next_path)


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).digest().hex()


def get_cookie_value(jar: Union[CookieJar, AbstractCookieJar], name: str) -> Optional[str]:
    found: List[Union[http.cookiejar.Cookie, http.cookies.Morsel]]
    if isinstance(jar, CookieJar):
        found = [x for x in jar if x.name == name]
    else:
        found = [x for x in jar if x.key == name]
    if not found:
        return None
    return found[0].value


def set_cookie_value(jar: AbstractCookieJar, key: str, value: str, url: str):
    morsel: http.cookies.Morsel = http.cookies.Morsel()
    morsel.set(key, value, value)
    morsel['domain'] = urllib.parse.urlparse(url).netloc
    morsel['path'] = urllib.parse.urlparse(url).path
    jar.update_cookies(morsel)


def find_all(data: JSONType, jsonpath: str, cache={}) -> List[JSONType]:
    if jsonpath not in cache:
        cache[jsonpath] = JSONPath(jsonpath)
    parser = cache[jsonpath]
    return parser.parse(data)


def find_one(data: JSONType, jsonpath: str) -> Optional[JSONType]:
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


def strip_text(s: str, text: str) -> str:
    if s.startswith(text):
        return s[len(text):]
    return s


def jwt_decode(token: str) -> dict:
    """Decode JWT token and return payload. Signature is not validated"""
    header, payload, signature = token.split('.')
    payload_json = base64.b64decode(payload.encode('utf-8') + b'====')
    payload_dict = json.loads(payload_json)
    return payload_dict


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


def with_prefix(logger: logging.Logger, prefix: str) -> logging.Logger:
    class Adapter(logging.LoggerAdapter):
        def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
            message = f'{prefix} {msg}' if prefix else msg
            return message, kwargs

    return Adapter(logger, extra=dict())  # type: ignore
