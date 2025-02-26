import json
import logging
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Dict, Optional

import aiohttp
from aiohttp.abc import AbstractCookieJar
from multidict import CIMultiDictProxy

from avtdl.core.utils import Delay, get_cache_ttl, get_retry_after

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
                      settings: RetrySettings = RetrySettings()) -> Optional['Response']:
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
                           ) -> Optional['Response']:
        logger = self.logger

        request_headers: Dict[str, Any] = headers or {}
        if self.session.headers is not None:
            request_headers.update(self.session.headers)
        if state.last_modified is not None and method in ['GET', 'HEAD']:
            request_headers['If-Modified-Since'] = state.last_modified
        if state.etag is not None:
            request_headers['If-None-Match'] = state.etag

        try:
            async with self.session.request(method, url, headers=request_headers, params=params, data=data,
                                            json=data_json) as client_response:
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
                f'Last-Modified={state.last_modified or "absent"}, ETAG={state.etag or "absent"}, Cache-control="{cache_control or "absent"}"')

        response = Response.from_response(client_response, text, state, logger)

        return response


@dataclass
class Response:
    logger: logging.Logger
    text: str
    url: str
    ok: bool
    status: int
    reason: str
    headers: CIMultiDictProxy[str]
    cookies: SimpleCookie
    endpoint_state: EndpointState

    @classmethod
    def from_response(cls, response: aiohttp.ClientResponse, text: str, state: EndpointState, logger: logging.Logger):
        response = cls(
            logger,
            text,
            str(response.url),
            response.ok,
            response.status,
            response.reason or 'No reason',
            response.headers,
            response.cookies,
            state
        )
        return response

    def json(self, raise_errors: bool = False) -> Optional[Any]:
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
