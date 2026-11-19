import asyncio
import os
import tempfile

import httpx
import pytest
from httpx import ASGITransport

# Isole la persistance des tests : jamais d'écriture dans le data/ du dépôt.
os.environ.setdefault(
    "LICHESS_MCP_STATE_FILE",
    os.path.join(tempfile.mkdtemp(prefix="lichess-mcp-test-"), "server_state.json"),
)

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
