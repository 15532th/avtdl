import http.cookiejar
import http.cookies
import logging
import urllib.parse
from abc import abstractmethod
from http import cookiejar
from pathlib import Path
from typing import List, Mapping, Optional, Union

import aiohttp
from aiohttp.abc import AbstractCookieJar

from avtdl.core.utils import parse_to_date_string, parse_to_timestamp


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


def save_cookies(cookies: 'AnotherCookieJar', path: str):
    try:
        cookie_jar = cookies.to_file_cookie_jar()
    except Exception as e:
        msg = f'error converting cookie jar: {e}'
        raise CookieStoreError(msg) from e
    try:
        cookie_jar.save(path, ignore_discard=True, ignore_expires=True)
    except Exception as e:
        msg = f'failed to store cookies to "{path}": {e}'
        raise CookieStoreError(msg) from e


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


def get_cookie_value(jar: Union[cookiejar.CookieJar, AbstractCookieJar], name: str) -> Optional[str]:
    found: List[Union[http.cookiejar.Cookie, http.cookies.Morsel]]
    if isinstance(jar, cookiejar.CookieJar):
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


class AnotherCookieJar:
    """Generic interface for various cookie jars from different http libraries"""

    @abstractmethod
    def update_cookies(self, cookies: Mapping[str, Union[str, http.cookies.Morsel]]):
        """Update self with values from argument"""

    @abstractmethod
    def set(self, key: str, value: str, url: str):
        """Set cookie value"""

    @abstractmethod
    def get(self, key: str) -> Optional[str]:
        """Get cookie value (without any metadata)"""

    @classmethod
    @abstractmethod
    def from_cookie_jar(cls, jar: cookiejar.CookieJar) -> 'AnotherCookieJar':
        """Convert from http.cookiejar.CookieJar"""

    @abstractmethod
    def to_file_cookie_jar(self) -> cookiejar.MozillaCookieJar:
        """Convert into MozillaCookieJar"""


class AnotherAiohttpCookieJar(AnotherCookieJar):
    def __init__(self, jar: aiohttp.CookieJar):
        self._cookies: aiohttp.CookieJar = jar

    def update_cookies(self, cookies: Mapping[str, Union[str, http.cookies.Morsel]]):
        self._cookies.update_cookies(cookies)

    def get(self, key: str) -> Optional[str]:
        return get_cookie_value(self._cookies, key)

    def set(self, key: str, value: str, url: str):
        set_cookie_value(self._cookies, key, value, url)

    @classmethod
    def from_cookie_jar(cls, jar: cookiejar.CookieJar) -> 'AnotherCookieJar':
        _jar = convert_cookiejar(jar)
        return cls(_jar)

    def to_file_cookie_jar(self) -> cookiejar.MozillaCookieJar:
        return unconvert_cookiejar(self._cookies)

