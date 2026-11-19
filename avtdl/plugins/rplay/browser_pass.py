import datetime
import logging
import re
from abc import ABC
from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence, Union

from multidict import CIMultiDictProxy

from avtdl.core.interfaces import utcnow
from avtdl.core.monitors import BaseFeedMonitorConfig, HttpTaskMonitor, HttpTaskMonitorEntity, load_headers
from avtdl.core.request import HttpClient, MaybeHttpResponse
from avtdl.core.request import RequestDetails as BaseRequestDetails
from avtdl.core.runtime import RuntimeContext
from avtdl.core.utils import with_prefix


@dataclass
class RequestDetails(BaseRequestDetails):
    has_turnstile: bool = False


class TurnstileSolverConfigMixin(BaseFeedMonitorConfig):
    turnstile_solver_url: str
    """url of an external challenge solver"""
    turnstile_solver_data: Optional[str] = None
    """body of the request sent to the external challenge solver, in plaintext. Also affects request method: POST when body is provided, GET otherwise"""
    turnstile_solver_headers: Dict[str, str] = {}
    """headers of the request sent to the external challenge solver"""
    turnstile_token_expression: str = r'"token": ?"([^"]+)"'
    """regular expression used to extract token from the solver response. Must contain exactly one group"""


@dataclass
class Token:
    x_turnstile_token: Optional[str]
    x_browser_pass: Optional[str] = None
    created_at: datetime.datetime = field(default_factory=utcnow)

    def has_expired(self) -> bool:
        """
        Token expiration

        x-turnstile-token token is supposedly single use and expires in 5 minutes if unused.
        It is exchanged for x-browser-pass, which contains its own expiration timestamp.
        """
        if self.x_browser_pass is None:
            return utcnow() - self.created_at > datetime.timedelta(minutes=5)
        else:
            try:
                expires_at = datetime.datetime.fromtimestamp(int(self.x_browser_pass.split('.')[0]) // 1000)
                return utcnow() < expires_at
            except Exception:
                return True

    @classmethod
    def from_request_headers(cls, headers: Union[Dict[str, str], CIMultiDictProxy[str], None]) -> Optional['Token']:
        if headers is None:
            return None
        token_value = headers.get('x-turnstile-token')
        pass_value = headers.get('x-browser-pass')
        if token_value is None and pass_value is None:
            return None
        return cls(x_turnstile_token=token_value, x_browser_pass=pass_value)

    def to_request_headers(self) -> Dict[str, str]:
        if self.x_browser_pass is not None:
            return {'x-browser-pass': self.x_browser_pass}
        elif self.x_turnstile_token is not None:
            return {'x-turnstile-token': self.x_turnstile_token}
        else:
            return {}

    def update_from_response_headers(self, headers: Union[Dict[str, str], CIMultiDictProxy[str], None]) -> None:
        if headers is not None:
            token_value = headers.get('x-browser-pass')
            if token_value is not None:
                self.x_browser_pass = token_value


class TurnstileSolverMixin(HttpTaskMonitor, ABC):

    def __init__(self, conf: TurnstileSolverConfigMixin, entities: Sequence[HttpTaskMonitorEntity],
                 ctx: RuntimeContext):
        super().__init__(conf, entities, ctx)
        self.conf: TurnstileSolverConfigMixin = conf
        self.logger = logging.getLogger('turnstile_solver')
        self.active_token: Optional[Token] = None

    async def fetch_token(self, client: HttpClient, logger: logging.Logger) -> Optional[Token]:
        method = 'POST' if self.conf.turnstile_solver_data is not None else 'GET'
        response = await client.request_text(url=self.conf.turnstile_solver_url,
                                             data=self.conf.turnstile_solver_data,
                                             headers=self.conf.turnstile_solver_headers,
                                             method=method)
        if response is None:
            logger.warning(f'failed to fetch turnstile token from "{self.conf.turnstile_solver_url}"')
            return None
        token_match = re.match(self.conf.turnstile_token_expression, response)
        if token_match is None:
            logger.warning(
                f'expression "{self.conf.turnstile_token_expression}" found no matches in solver response. Raw response: {response}')
            return None
        token = token_match.group(1)
        return Token(x_turnstile_token=token)

    async def request_endpoint(self, entity: HttpTaskMonitorEntity,
                               client: HttpClient,
                               request_details: RequestDetails) -> MaybeHttpResponse:
        """request_endpoint() wrapper with turnstile token handling"""

        # if request_details does not require turnstile, call is passed to base implementation
        if not request_details.has_turnstile:
            return await super().request_endpoint(entity, client, request_details)

        # otherwise base implementation is re-implemented with additional headers handling
        logger = with_prefix(self.logger, f'[{entity.name}] ')

        additional_headers = load_headers(entity.headers_file, logger)
        if additional_headers:
            request_details.headers = {**(request_details.headers or {}), **additional_headers}

        if self.active_token is None or self.active_token.has_expired():
            self.active_token = Token.from_request_headers(request_details.headers)

        if self.active_token is None or self.active_token.has_expired():
            self.active_token = await self.fetch_token(client, logger)

        if self.active_token is None or self.active_token.has_expired():
            logger.warning(f'request to "{request_details.url}" requires turnstile token')
            headers = {}
        else:
            headers = self.active_token.to_request_headers()

        request_details.headers = headers.update(request_details.headers or {})
        response = await super().request_endpoint(entity, client, request_details)
        if self.active_token is not None:
            self.active_token.update_from_response_headers(response.headers)
        else:
            self.active_token = Token.from_request_headers(response.headers)

        entity.update_interval = response.next_update_interval(entity.base_update_interval, entity.update_interval,
                                                               entity.adjust_update_interval)
        return response
