import aiohttp
import pytest
import pytest_asyncio

from harness import ServerConfig, build_server_from_config


@pytest.fixture
def server_cfg(request) -> ServerConfig:
    """Return the ServerConfig supplied via indirect parametrization"""
    return ServerConfig.model_validate(request.param)


@pytest_asyncio.fixture
async def server_instance(server_cfg: ServerConfig):
    """Start a TestServer built from server_cfg and ensure clean shutdown"""
    server = build_server_from_config(server_cfg)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


raw_config = [
    {
        'method': 'GET',
        'path': '/greet',
        'payloads': [
            {
                'body': 'hello',
                'headers': {"X-Id": "1"}
            },
            {
                'body': 'world',
                'headers': {"X-Id": "2"}
            },

        ]
    }
]


@pytest.mark.parametrize("server_cfg", [raw_config], indirect=True)
@pytest.mark.asyncio
async def test_greet_endpoint(server_instance, server_cfg):
    url = f"http://127.0.0.1:{server_instance.port}/greet"

    async with aiohttp.ClientSession() as client:
        async with client.get(url) as r1:
            txt1 = await r1.text()
            assert r1.status == 200
            assert r1.headers["X-Id"] == "1"
            assert txt1 == "hello"

        async with client.get(url) as r2:
            txt2 = await r2.text()
            assert r2.headers["X-Id"] == "2"
            assert txt2 == "world"

        async with client.get(url) as r3:
            txt3 = await r3.text()
            assert r3.headers["X-Id"] == "2"
            assert txt3 == "world"


raw_config = [
    {
        'method': 'POST',
        'path': '/data',
        'payloads': [
            {
                'body': '{"ok": true}',
                'status': 202
            }
        ]
    }
]


@pytest.mark.parametrize("server_cfg", [raw_config], indirect=True)
@pytest.mark.asyncio
async def test_data_endpoint(server_instance, server_cfg):
    url = f"http://127.0.0.1:{server_instance.port}/data"
    async with aiohttp.ClientSession() as client:
        async with client.post(url, json={"dummy": 0}) as resp:
            data = await resp.text()
            assert resp.status == 202
            assert data == '{"ok": true}'
