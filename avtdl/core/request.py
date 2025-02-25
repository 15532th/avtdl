import asyncio
import datetime
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import aiohttp
from multidict import CIMultiDictProxy

from avtdl.core.utils import Delay, get_cache_ttl, get_retry_after, utcnow

HIGHEST_UPDATE_INTERVAL: float = 4000


@dataclass
class RequestSettings:
    adjust_update_interval: bool = True
    """use value from response Cache-Control or Expires headers to adjust update frequency"""

    base_update_interval: float = 1
    """normal update rate, used to calculate exponential backoff of delay on network failures"""
    wait_before_update: bool = True
    """make next call to request block until currently pending delay expires"""

    with_cache_headers: bool = True
    """add "If-Modified-Since" and "Etag" headers set by response to previous request to specific endpoint"""
    none_if_unmodified: bool = True
    """return None if response status code is 304 Not Modified"""
    raise_for_status: bool = True
    """raise exception on 4XX and 5XX response"""
    raise_errors: bool = False
    """let exceptions caused by network failures and error status codes propagate. When disabled return None instead"""

    retry_times: int = 1
    """transparent retrying: number of attempts"""
    retry_delay: float = 1
    """transparent retrying: delay before first retry attempt"""
    retry_multiplier: float = 1.2
    """transparent retrying: factor to increase retry delay compared to the previous attempt"""


DEFAULT_SETTINGS = RequestSettings()


@dataclass
class EndpointState:
    update_interval: float
    _update_after: datetime.datetime = datetime.datetime.now(tz=datetime.timezone.utc)
    etag: Optional[str] = None
    last_modified: Optional[str] = None

    def set_update_interval(self, interval: float):
        self.update_interval = interval
        self._update_after = utcnow() + datetime.timedelta(seconds=interval)

    def get_remaining_delay(self) -> float:
        delta = self._update_after - utcnow()
        delay = delta.total_seconds()
        return max(delay, 0)


class Request:

    def __init__(self, logger: logging.Logger, session: aiohttp.ClientSession):
        self.logger = logger
        self.session = session

        # endpoints are identified by tuple (method, url, params)
        self.endpoints: Dict[Tuple[str, str, Optional[Dict[str, str]]], EndpointState] = {}

    @staticmethod
    def decide_on_update_delay(logger: logging.Logger, url: str, status: Optional[int],
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

    async def raw(self, url: str,
                  params: Optional[Dict[str, str]] = None,
                  data: Optional[Any] = None,
                  data_json: Optional[Any] = None,
                  headers: Optional[Dict[str, Any]] = None,
                  method: str = 'GET',
                  settings: RequestSettings = DEFAULT_SETTINGS) -> Optional[aiohttp.ClientResponse]:
        resource_id = (method, url, params)
        if resource_id not in self.endpoints:
            self.endpoints[resource_id] = EndpointState(settings.base_update_interval)
        state = self.endpoints[resource_id]
        logger = self.logger

        if settings.wait_before_update:
            delay = state.get_remaining_delay()
            await asyncio.sleep(delay)

        request_headers: Dict[str, Any] = headers or {}
        if self.session.headers is not None:
            request_headers.update(self.session.headers)
        if state.last_modified is not None and method in ['GET', 'HEAD']:
            request_headers['If-Modified-Since'] = state.last_modified
        if state.etag is not None:
            request_headers['If-None-Match'] = state.etag

        try:
            text = ''
            async with self.session.request(method, url, headers=request_headers, params=params, data=data,
                                            json=data_json) as response:
                # fully read http response to get it cached inside ClientResponse object
                # client code can then use it by awaiting .text() again without causing
                # network activity and potentially triggering associated errors
                text = await response.text()
                if settings.raise_for_status:
                    response.raise_for_status()
        except Exception as e:
            response_status: Optional[int] = None
            response_headers: Optional[CIMultiDictProxy[str]] = None
            if isinstance(e, aiohttp.ClientResponseError):
                logger.warning(f'got code {e.status} ({e.message}) while fetching {url}')
                if text:
                    logger.debug(f'response body: "{text}"')
                response_status = e.status
                response_headers = e.headers  # type: ignore
            else:
                logger.warning(f'error while fetching {url}: {e.__class__.__name__} {e}')
            update_interval = self.decide_on_update_delay(logger, url, response_status, response_headers,
                                                          state.update_interval, settings.base_update_interval,
                                                          settings.adjust_update_interval)
            state.set_update_interval(update_interval)
            if settings.raise_errors:
                raise
            else:
                return None

        # some servers do not have cache headers in 304 response, so only updating on 200
        state.last_modified = response.headers.get('Last-Modified', None)
        state.etag = response.headers.get('Etag', None)

        cache_control = response.headers.get('Cache-control')
        logger.debug(
            f'Last-Modified={state.last_modified or "absent"}, ETAG={state.etag or "absent"}, Cache-control="{cache_control or "absent"}"')

        update_interval = self.decide_on_update_delay(logger, url, response.status, response.headers,
                                                      state.update_interval, settings.base_update_interval,
                                                      settings.adjust_update_interval)
        state.set_update_interval(update_interval)

        return response

    async def text(self, url: str,
                   params: Optional[Dict[str, str]] = None,
                   data: Optional[Any] = None,
                   data_json: Optional[Any] = None,
                   headers: Optional[Dict[str, Any]] = None,
                   method: str = 'GET',
                   settings: RequestSettings = DEFAULT_SETTINGS) -> Optional[str]:
        response = await self.raw(url, params, data, data_json, headers, method, settings)
        if response is None:
            return None
        return await response.text()

    async def json(self, url: str,
                   params: Optional[Dict[str, str]] = None,
                   data: Optional[Any] = None,
                   data_json: Optional[Any] = None,
                   headers: Optional[Dict[str, Any]] = None,
                   method: str = 'GET',
                   settings: RequestSettings = DEFAULT_SETTINGS) -> Optional[str]:
        response = await self.raw(url, params, data, data_json, headers, method, settings)
        if response is None:
            return None
        text = await response.text()
        try:
            parsed = json.loads(text)
            return parsed
        except json.JSONDecodeError as e:
            self.logger.debug(f'error parsing response from {url}: {e}. Raw response data: "{text}"')
            if settings.raise_errors:
                raise
            else:
                return None
