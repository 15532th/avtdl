from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Tuple, Union

from aiohttp import web
from pydantic import BaseModel, Field

from avtdl.core.utils import ListRootModel


class ExpectedRequest(BaseModel):
    headers: Dict[str, str] = {}
    params: Dict[str, str] = {}
    data: str | None = None
    cookies: Dict[str, str] = {}


class Payload(BaseModel):
    '''Representation of a single response payload'''
    status: int = 200
    headers: Dict[str, str] = {}
    body: Union[str, bytes] = ''
    expected_request: ExpectedRequest = ExpectedRequest()


def _compare_dict(actual: Mapping[str, Any], expected: Mapping[str, Any], kind: str) -> None:
    """Raise a 400 response if any key/value pair in expected is missing or differs in actual"""
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if actual_value != expected_value:
            msg = f'unexpected {kind} "{key}", expected "{expected_value}" got "{actual_value}"'
            raise web.HTTPBadRequest(reason=msg)


async def validate_request(request: web.Request, expected: ExpectedRequest) -> None:
    """Raise status 400 if request doesn't match expected_request"""

    # normalise header names to lower‑case because aiohttp stores themthat way
    actual_headers = {k.lower(): v for k, v in request.headers.items()}
    expected_headers = {k.lower(): v for k, v in expected.headers.items()}
    _compare_dict(actual_headers, expected_headers, "header")

    # ``request.rel_url.query`` is a multidict; convert to a plain dict
    actual_params = dict(request.rel_url.query)
    _compare_dict(actual_params, expected.params, "query parameter")

    if expected.data is not None:
        try:
            body = await request.text()
        except Exception as exc:
            raise web.HTTPBadRequest(reason=f"Unable to read request body: {exc}")
        if body != expected.data:
            raise web.HTTPBadRequest(reason=f"unexpected body, expected '{expected.data}' got '{body}'")

    if expected.cookies:
        # ``request.cookies`` returns a dict of name → value.
        actual_cookies = request.cookies
        _compare_dict(actual_cookies, expected.cookies, "cookie")


class Route(BaseModel):
    '''Definition of a route and the sequence of payloads it should return'''
    method: str = 'GET'
    path: str
    payloads: List[Payload] = []


class ServerConfig(ListRootModel):
    '''List of all routes of specific TestServer instance'''
    root: List[Route] = Field(default_factory=list)


class TestServer:
    def __init__(self) -> None:
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._payloads: Dict[Tuple[str, str], deque[Payload]] = defaultdict(deque)

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host='127.0.0.1', port=0)
        await self._site.start()

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()

    @property
    def port(self) -> int:
        if self._site is None:
            raise RuntimeError('Server not started yet')
        return self._site._server.sockets[0].getsockname()[1]  # type: ignore

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.port}'

    def get_payload_queue(self, method: str, path: str) -> deque[Payload]:
        return self._payloads[(method.upper(), path)]

    def _make_handler(self, method: str, path: str) -> Callable[[web.Request], Awaitable[web.Response]]:
        async def handler(request: web.Request) -> web.Response:
            queue = self.get_payload_queue(method, path)

            if len(queue) == 0:
                raise web.HTTPNotFound(text=f'No payload for {method} {path}')
            elif len(queue) == 1:  # single (or last) payload is served indefinitely
                payload = queue[0]
            else:
                payload = queue.popleft()
            await validate_request(request, payload.expected_request)
            if isinstance(payload.body, str):
                return web.Response(text=payload.body, status=payload.status, headers=payload.headers)
            elif isinstance(payload.body, bytes):
                return web.Response(body=payload.body, status=payload.status, headers=payload.headers)
            else:
                raise web.HTTPInternalServerError(reason=f'unexpected payload type: {type(payload.body)}')

        return handler

    def route_registered(self, method, path) -> bool:
        for r in self._app.router.routes():
            if r.method == method:
                if getattr(r, 'path', None) == path:
                    return True
        return False

    def register_route(self, route: Route) -> None:
        '''Register a route with given path and method to serve specific payload(s)'''

        if not self.route_registered(route.method, route.path):
            self._app.router.add_route(route.method, route.path, self._make_handler(route.method, route.path))

        queue = self.get_payload_queue(route.method, route.path)
        for payload in route.payloads:
            queue.append(payload)


def build_server_from_config(config: ServerConfig) -> TestServer:
    '''Create a TestServer instance and register every route defined in config'''
    server = TestServer()
    for route in config:
        server.register_route(route)
    return server


def load_config_from_json(path: Union[str, Path]) -> ServerConfig:
    '''Load a JSON file and parse it into a ServerConfig model'''
    p = Path(path)
    data = p.read_text(encoding='utf8')
    return ServerConfig.model_validate_json(data)
