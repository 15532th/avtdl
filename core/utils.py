import datetime
import logging
import os
import re
from email.utils import parsedate_to_datetime, mktime_tz
from http import cookiejar
from pathlib import Path
from textwrap import shorten
from typing import Optional, Dict, Any

import multidict


def load_cookies(path: Optional[Path], raise_on_error: bool = False) -> Optional[cookiejar.CookieJar]:
    logger = logging.getLogger('cookies')
    if path is None:
        return None
    cookie_jar = cookiejar.MozillaCookieJar(path)
    try:
        cookie_jar.load()
        logger.info(f"Successfully loaded cookies from {path}")
    except FileNotFoundError:
        if raise_on_error:
            raise
        return None
    except (cookiejar.LoadError, OSError) as e:
        if raise_on_error:
            raise
        logger.exception(f'Failed to load cookies from {path}: {e}')
        return None
    return cookie_jar

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

if __name__ == '__main__':
    d1 = {1:1, 2:2, 3:'b'}
    d2 = {2:2, 3:'a', 4:4}
    print(show_diff(d1, d2))