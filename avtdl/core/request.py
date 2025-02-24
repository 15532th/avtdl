import asyncio
import dataclasses
import datetime
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, MutableMapping, Optional, Tuple

import aiohttp

from avtdl.core.utils import Delay, get_retry_after, utcnow

HIGHEST_UPDATE_INTERVAL: float = 4000


@dataclass
class RequestSettings:
    logger_prefix: str = ''
    """prefix all log messages with given text"""

    base_update_interval: float = 1
    """normal update rate, used to calculate exponential backoff of delay on network failures"""
    wait_before_update: bool = True
    """make next call to request block until currently pending delay expires"""

    with_cache_headers: bool = True
    """add "If-Modified-Since" and "Etag" headers set by response to previous request to specific endpoint"""
    none_if_unmodified: bool = True
    """return None if response status code is 304 Not Modified"""
    raise_errors: bool = False
    """let exceptions from both network failures and error status codes propagate. When disabled return None instead"""

    retry_times: int = 1
    """transparent retrying: number of attempts"""
    retry_delay: float = 1
    """transparent retrying: delay before first retry attempt"""
    retry_multiplier: float = 1.2
    """transparent retrying: factor to increase retry delay compared to the previous attempt"""

    def with_prefix(self, prefix: str) -> 'RequestSettings':
        return dataclasses.replace(self, logger_prefix=prefix)


DEFAULT_SETTINGS = RequestSettings()


def with_prefix(logger: logging.Logger, prefix: str) -> logging.Logger:
    class Adapter(logging.LoggerAdapter):
        def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
            message = f'{prefix} {msg}' if prefix else msg
            return message, kwargs

    return Adapter(logger)  # type: ignore


@dataclass
class EndpointState:
    update_after: datetime.datetime = datetime.datetime.now(tz=datetime.timezone.utc)
    etag: Optional[str] = None
    last_modified: Optional[str] = None

    def set_update_delay(self, delay: float):
        self.update_after = utcnow() + datetime.timedelta(seconds=delay)

    def get_remaining_delay(self) -> float:
        delta = self.update_after - utcnow()
        delay = delta.total_seconds()
        return max(delay, 0)


class Request:

    def __init__(self, logger: logging.Logger, session: aiohttp.ClientSession):
        self.logger = logger
        self.session = session

        # endpoints are identified by tuple (method, url, params)
        self.endpoints: Dict[Tuple[str, str, Optional[Dict[str, str]]], EndpointState] = defaultdict(EndpointState)

    def decide_on_update_delay(self, logger: logging.Logger, url: str, status: str, headers: Dict[str, str], current_update_interval: float, base_update_interval: float) -> float:
        retry_after = get_retry_after(headers)
        if retry_after is not None:
            raw_header = headers.get("Retry-After")
            logger.debug(f'got Retry-After header with value {raw_header}')
            update_interval = max(float(retry_after), HIGHEST_UPDATE_INTERVAL)
            logger.warning(f'update interval set to {update_interval} seconds for {url} as requested by response headers')
            return update_interval
        else:


    async def raw(self, url: str, params: Optional[Dict[str, str]] = None, data: Optional[Any] = None,
                  data_json: Optional[Any] = None, headers: Optional[Dict[str, Any]] = None, method: str = 'GET',
                  settings: RequestSettings = DEFAULT_SETTINGS) -> Optional[aiohttp.ClientResponse]:
        state = self.endpoints[(method, url, params)]
        logger = with_prefix(self.logger, settings.logger_prefix)

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
                response.raise_for_status()
        except Exception as e:
            if isinstance(e, aiohttp.ClientResponseError):
                logger.warning(f'got code {e.status} ({e.message}) while fetching {url}')
                if text:
                    logger.debug(f'response body: "{text}"')

            else:
                logger.warning(f'error while fetching {url}: {e.__class__.__name__} {e}')

            update_interval = int(max(Delay.get_next(entity.update_interval), entity.update_interval))
            if entity.update_interval != update_interval:
                entity.update_interval = update_interval
                logger.warning(f'[{entity.name}] update interval set to {entity.update_interval} seconds for {url}')
            return None

        if response.status == 304:
            logger.debug(f'[{entity.name}] got {response.status} ({response.reason}) from {url}')
            if entity.update_interval != entity.base_update_interval:
                logger.info(
                    f'[{entity.name}] restoring update interval {entity.base_update_interval} seconds for {url} after getting 304 response')
                entity.update_interval = entity.base_update_interval
            return None
        # some servers do not have cache headers in 304 response, so only updating on 200
        entity.last_modified = response.headers.get('Last-Modified', None)
        entity.etag = response.headers.get('Etag', None)

        cache_control = response.headers.get('Cache-control')
        logger.debug(
            f'[{entity.name}] Last-Modified={entity.last_modified or "absent"}, ETAG={entity.etag or "absent"}, Cache-control="{cache_control or "absent"}"')

        if entity.adjust_update_interval:
            new_update_interval = get_cache_ttl(response.headers) or entity.base_update_interval
            new_update_interval = min(new_update_interval, 10 * entity.base_update_interval,
                                      HIGHEST_UPDATE_INTERVAL)  # in case ttl is overly long
            new_update_interval = max(new_update_interval, entity.base_update_interval)
            if entity.update_interval != new_update_interval:
                entity.update_interval = new_update_interval
                logger.info(f'[{entity.name}] next update in {entity.update_interval}')
        else:
            # restore update interval after backoff on failure
            if entity.update_interval != entity.base_update_interval:
                logger.info(
                    f'[{entity.name}] restoring update interval {entity.base_update_interval} seconds for {url}')
                entity.update_interval = entity.base_update_interval

        return response
