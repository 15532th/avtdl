import datetime
from email.utils import parsedate_tz
import logging
import re
from http import cookiejar
from pathlib import Path
from typing import Optional

import multidict


def load_cookies(path: Optional[Path], raise_on_error: bool = False) -> Optional[cookiejar.CookieJar]:
    if path is None:
        return None
    cookie_jar = cookiejar.MozillaCookieJar(path)
    try:
        cookie_jar.load()
        logging.info(f"Successfully loaded cookies from {path}")
    except FileNotFoundError:
        if raise_on_error:
            raise
        return None
    except (cookiejar.LoadError, OSError) as e:
        if raise_on_error:
            raise
        logging.exception(f'Failed to load cookies from {path}: {e}')
        return None
    return cookie_jar

def get_cache_ttl(headers: multidict.CIMultiDictProxy) -> Optional[int]:
    '''Check for Expires and Cache-Control headers,
    return integer representing how many seconds has
    left until resource is outdated'''

    def get_expires_from_cache_control(headers) -> Optional[datetime.datetime]:
        try:
            cache_control = headers.get('Cache-control')
            max_age = re.search('max-age=(\d+)', cache_control)
            max_age_value = datetime.timedelta(seconds=int(max_age))

            last_modified = headers.get('Last-Modified')
            last_modified_value = datetime.datetime(*parsedate_tz(last_modified))

            expires  = last_modified_value + max_age_value
            return expires
        except (TypeError, ValueError):
            return None

    def get_expires_from_expires_header(headers) -> Optional[datetime.datetime]:
        try:
            expires_header = headers.get('Expires')
            if expires_header == '0':
                return None
            expires_value = datetime.datetime(*parsedate_tz(expires_header))
            return expires_value
        except (TypeError, ValueError):
            return None

    expires = get_expires_from_cache_control(headers) or get_expires_from_expires_header(headers)
    if expires is None:
        return None

    now = datetime.datetime.utcnow()
    delta = (expires - now).total_seconds()
    if delta < 0:
        return None

    return int(delta)