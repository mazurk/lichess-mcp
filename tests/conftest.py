import asyncio

import httpx
import pytest
from httpx import ASGITransport

import server


@pytest.fixture
def client():
    app = server.mcp.http_app()

    class _AsyncClient:
        def __init__(self):
            self._client = httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            )

        def _run(self, coro):
            return asyncio.run(coro)

        def get(self, path, headers=None):
            return self._run(self._client.get(path, headers=headers))

        def post(self, path, json=None, headers=None):
            return self._run(self._client.post(path, json=json, headers=headers))

    return _AsyncClient()
