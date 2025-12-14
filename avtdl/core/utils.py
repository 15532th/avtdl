import base64
import datetime
import hashlib
import json
import logging
import os
import re
from collections import OrderedDict
from contextlib import ContextDecorator
from pathlib import Path
from textwrap import shorten
from time import perf_counter
from typing import Any, Dict, Hashable, Iterable, Iterator, List, Mapping, MutableMapping, Optional, Tuple, \
    Type, TypeVar, Union

import dateutil.parser
import dateutil.tz
from jsonpath import JSONPath
from pydantic import AnyHttpUrl, RootModel, ValidationError

from avtdl.core.interfaces import Record

JSONType = Union[str, int, float, bool, None, Mapping[str, 'JSONType'], List['JSONType']]


def parse_to_timestamp(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    try:
        dt = dateutil.parser.parse(str(text))
    except Exception:
        return None
    return int(dt.timestamp())


def parse_to_date_string(text: Union[int, str, None]) -> Optional[str]:
    if text is None:
        return None
    try:
        dt = dateutil.parser.parse(str(text))
    except Exception:
        return None
    date_string = dt.strftime('%a, %d-%b-%y %H:%M:%S GMT')
    return date_string


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


def find_all(data: JSONType, jsonpath: str, cache={}) -> List[JSONType]:
    if jsonpath not in cache:
        cache[jsonpath] = JSONPath(jsonpath)
    parser = cache[jsonpath]
    return parser.parse(data)


def find_one(data: JSONType, jsonpath: str) -> Optional[JSONType]:
    result = find_all(data, jsonpath)
    return result[0] if result else None


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


def is_url(maybe_url: Optional[str]) -> bool:
    if maybe_url is None:
        return False
    try:
        AnyHttpUrl(maybe_url)
        return True
    except ValidationError:
        return False


T = TypeVar('T')


def getitem(container: Dict[str, Any], key: str, expected_type: Type[T]) ->T:
    """
    return container.get(key), raise ValueError if result type doesn't match expected_type
    """
    item = container.get(key)
    if not isinstance(item, expected_type):
        raise ValueError(f'unexpected {key} format: expected {expected_type.__name__}, got {type(item).__name__}')
    return item


class Timezone:
    known: Dict[str, Any] = {}

    @classmethod
    def get_tz(cls, name: Optional[str]) -> Optional[datetime.tzinfo]:
        if name is None:
            return None
        tz = dateutil.tz.gettz(name)
        if tz is None:
            raise ValueError(f'Unknown timezone: {name}')
        cls.known[name] = tz
        return tz

    @classmethod
    def get_name(cls, tz: Optional[datetime.tzinfo]) -> Optional[str]:
        if tz is None:
            return None
        for name, timezone in cls.known.items():
            if tz == timezone:
                return name
        return tz.tzname(datetime.datetime.now())


class DictRootModel(RootModel):
    """Helper class implementing dict methods for dict-based root models"""
    root: dict

    def __getitem__(self, key):
        return self.root[key]

    def __setitem__(self, key, value):
        self.root[key] = value

    def keys(self):
        return self.root.keys()

    def values(self):
        return self.root.values()

    def items(self):
        return self.root.items()


class ListRootModel(RootModel):
    """Helper class implementing indexing and iteration list-based root models"""
    root: list

    def __getitem__(self, index: int):
        return self.root[index]

    def __len__(self) -> int:
        return len(self.root)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.root)

    def __contains__(self, item) -> bool:
        return item in self.root

    def append(self, item):
        self.root.append(item)

    def extend(self, items: Iterable):
        self.root.extend(items)


def strip_text(s: str, text: str) -> str:
    if s.startswith(text):
        return s[len(text):]
    return s


def format_validation_error(e: ValidationError, msg: str) -> str:
    errors = []
    for err in e.errors():
        user_input = str(err['input'])
        user_input = user_input if len(user_input) < 85 else user_input[:50] + ' [...] ' + user_input[-30:]
        location = ': '.join(str(l) for l in err['loc'])
        error_message = strip_text(err['msg'], 'Value error, ')
        error = 'error parsing "{}" in config section {}: {}'
        errors.append(error.format(user_input, location, error_message))
    return '\n    '.join([msg] + errors)
