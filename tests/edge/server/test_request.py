import pytest

from avtdl.core.request import ClientPool, Transport
from harness import ServerConfig, TestServer
from test_harness import server_cfg, server_instance

test_cases = [
    # Simple GET
    {
        "method": "GET",
        "path": "/simple-get",
        "payloads": [
            {}  # all defaults
        ]
    },

    # GET with query parameters
    {
        "method": "GET",
        "path": "/get-with-params",
        "payloads": [
            {
                "expected_request": {
                    "params": {"a": "1", "b": "two"}
                }
            }
        ]
    },

    # GET with a custom request header
    {
        "method": "GET",
        "path": "/get-with-header",
        "payloads": [
            {
                "expected_request": {
                    "headers": {"X-Test": "true"}
                }
            }
        ]
    },

    # POST with JSON body
    {
        "method": "POST",
        "path": "/post-json",
        "payloads": [
            {
                "headers": {"Content-Type": "application/json"},
                "body": '{"msg": "hello", "id": 123}',
                "expected_request": {
                    "headers": {"Content-Type": "application/json"},
                    "data": '{"msg":"hello","id":123}'
                }
            }
        ]
    },

    # POST with form‑encoded data
    {
        "method": "POST",
        "path": "/post-form",
        "payloads": [
            {
                "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                "body": "name=alice&age=30",
                "expected_request": {
                    "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                    "data": "name=alice&age=30"
                }
            }
        ]
    },

    # POST with cookies
    {
        "method": "POST",
        "path": "/post-with-cookies",
        "payloads": [
            {
                "headers": {"Content-Type": "application/json"},
                "body": '{"action": "login"}',
                "expected_request": {
                    "headers": {"Content-Type": "application/json"},
                    "data": '{"action": "login"}',
                    "cookies": {"sessionid": "abc123", "pref": "dark"}
                }
            }
        ]
    },
]


@pytest.mark.parametrize("server_cfg", [test_cases], indirect=True)
@pytest.mark.parametrize("transport", [Transport.AIOHTTP, Transport.CURL_CFFI])
@pytest.mark.asyncio
async def test_http_client(server_instance: TestServer, server_cfg: ServerConfig, transport: Transport):
    client_pool = ClientPool()
    try:
        for route in server_cfg:
            for payload in route.payloads:
                expected = payload.expected_request
                client = client_pool.get_client(name='test_http_client', transport=transport)
                if expected.cookies:
                    client.cookie_jar.update_cookies(expected.cookies)
                response = await client.request_once(server_instance.url + route.path,
                                                     expected.params,
                                                     expected.data,
                                                     headers=expected.headers,
                                                     method=route.method)
                if not response.status == payload.status:
                    assert False, f'endpoint {route.method} {route.path} failed with {response.text}'
    finally:
        await client_pool.close()


TEXT_FILE = b'\x00\x01\x02\xFF'

file_server_config = [
    {
        "method": "GET",
        "path": "/file.txt",
        "payloads": [
            {
                "headers": {"Content-Type": "application/octet-stream"},
                "body": TEXT_FILE,
                "expected_request": {}
            }
        ]
    },

]


@pytest.mark.parametrize("server_cfg", [file_server_config], indirect=True)
@pytest.mark.parametrize("transport", [Transport.AIOHTTP, Transport.CURL_CFFI])
@pytest.mark.asyncio
async def test_download(server_instance: TestServer, server_cfg: ServerConfig, transport: Transport, tmp_path):
    client_pool = ClientPool()
    try:
        for route in server_cfg:
            for payload in route.payloads:
                expected = payload.expected_request
                client = client_pool.get_client(name='test_http_client', transport=transport)
                if expected.cookies:
                    client.cookie_jar.update_cookies(expected.cookies)
                download_path = tmp_path / route.path.lstrip('/')
                file_info = await client.download_file(download_path,
                                                       server_instance.url + route.path,
                                                       expected.params,
                                                       expected.data,
                                                       headers=expected.headers,
                                                       method=route.method)
                assert file_info is not None
                assert download_path.exists()
                assert download_path.read_bytes() == payload.body
    finally:
        await client_pool.close()
