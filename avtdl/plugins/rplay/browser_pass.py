import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Union

from multidict import CIMultiDictProxy

from avtdl.core.interfaces import utcnow
from avtdl.core.monitors import BaseFeedMonitorConfig, HttpTaskMonitorEntity, load_headers
from avtdl.core.request import HttpClient, MaybeHttpResponse, NoResponse, RequestDetails as BaseRequestDetails
from avtdl.core.utils import with_prefix


@dataclass
class RequestDetails(BaseRequestDetails):
    has_turnstile: bool = False


class TurnstileSolverConfigMixin(BaseFeedMonitorConfig):
    turnstile_solver_url: Optional[str] = None
    """url of an external challenge solver"""
    turnstile_solver_data: Optional[str] = None
    """body of the request sent to the external challenge solver, in plaintext. Also affects request method: POST when body is provided, GET when left empty"""
    turnstile_solver_headers: Dict[str, str] = {'Content-Type': 'application/json'}
    """headers of the request sent to the external challenge solver"""
    turnstile_token_expression: str = r'"token": ?"([^"]+)"'
    """regular expression used to extract token from the solver response. Must contain exactly one group"""
    turnstile_failsafe: bool = True
    """prevent requests to protected endpoint from being sent if retrieving token from solver has failed for any reason"""


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
            expires_at = self.browser_pass_expires_at()
            return utcnow() > expires_at if expires_at is not None else True

    def browser_pass_expires_at(self) -> Optional[datetime.datetime]:
        if self.x_browser_pass is None:
            return None
        try:
            ts = int(self.x_browser_pass.split('.')[0]) // 1000
            expires_at = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            return expires_at
        except Exception:
            return None

    def browser_pass_ttl(self) -> datetime.timedelta:
        """Time left until x-browser-pass expiration"""
        expires_at = self.browser_pass_expires_at()
        if expires_at is None or expires_at < utcnow():
            return datetime.timedelta(seconds=0)
        return expires_at - utcnow()

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


class TurnstileSolver:

    def __init__(self, conf: TurnstileSolverConfigMixin, logger: logging.Logger):
        self.conf: TurnstileSolverConfigMixin = conf
        self.logger = logger
        self.active_token: Optional[Token] = None

    async def fetch_token(self, client: HttpClient, logger: logging.Logger) -> Optional[Token]:
        if self.conf.turnstile_solver_url is None:
            logger.warning(f'request to fetch turnstile token is made but provider url is not configured. This is a bug, please report it')
            return None
        method = 'POST' if self.conf.turnstile_solver_data is not None else 'GET'
        response = await client.request_text(url=self.conf.turnstile_solver_url,
                                             data=self.conf.turnstile_solver_data,
                                             headers=self.conf.turnstile_solver_headers,
                                             method=method)
        if response is None:
            logger.warning(f'failed to fetch turnstile token from "{self.conf.turnstile_solver_url}"')
            return None
        token_match = re.search(self.conf.turnstile_token_expression, response)
        if token_match is None:
            logger.warning(
                f'expression "{self.conf.turnstile_token_expression}" found no matches in solver response. Raw response: {response}')
            return None
        token = token_match.group(1)
        return Token(x_turnstile_token=token)

    async def request_endpoint(self, entity: HttpTaskMonitorEntity,
                               client: HttpClient,
                               request_details: RequestDetails) -> MaybeHttpResponse:
        """
        Reimplementation of HttpTaskMonitor.request_endpoint() with turnstile token handling

        If RequestDetails.has_turnstile is False behaves like the original.
        """
        logger = with_prefix(self.logger, f'[{entity.name}] ')

        additional_headers = load_headers(entity.headers_file, logger)
        if additional_headers:
            request_details.headers = {**(request_details.headers or {}), **additional_headers}
        if request_details.has_turnstile:
            if self.active_token is None or self.active_token.has_expired():
                state = 'is absent' if self.active_token is None else 'has expired'
                logger.debug(f'request to {request_details.url} requires token, but token {state}. Checking headers file')
                self.active_token = Token.from_request_headers(request_details.headers)

            if self.active_token is None or self.active_token.has_expired():
                if self.conf.turnstile_solver_url is not None:
                    state = 'is absent' if self.active_token is None else 'has expired'
                    logger.debug(f'token in headers file {state}. Trying to fetch from provider ({self.conf.turnstile_solver_url})')
                    self.active_token = await self.fetch_token(client, logger)

            if self.active_token is None or self.active_token.has_expired():
                state = 'is absent' if self.active_token is None else 'has expired'
                logger.warning(f'request to "{request_details.url}" requires turnstile token, but token {state}')
                if self.conf.turnstile_failsafe:
                    logger.warning(f'turnstile_failsafe is enabled, aborting request to {request_details.url}')
                    return NoResponse(logger, Exception(f'turnstile token {state}'), request_details.url)
                headers = {}
            else:
                logger.debug(f'request to {request_details.url} requires token, adding it')
                headers = self.active_token.to_request_headers()

            if request_details.headers is not None:
                request_details.headers.update(headers)
            else:
                request_details.headers = headers

        response = await client.request_endpoint(logger, request_details)

        if self.active_token is not None:
            self.active_token.update_from_response_headers(response.headers)
        else:
            self.active_token = Token.from_request_headers(response.headers)

        entity.update_interval = response.next_update_interval(entity.base_update_interval, entity.update_interval,
                                                               entity.adjust_update_interval)
        return response
