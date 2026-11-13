"""Tests de l'interface web (plateau navigateur) via l'app ASGI FastMCP."""

import asyncio

import httpx
import pytest
from httpx import ASGITransport

import server


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("LICHESS_MCP_AUTH_TOKEN", raising=False)
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()


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


def _post_json(client, path, payload):
    return client.post(path, json=payload)


def test_web_index_file_exists():
    assert (server.WEB_DIR / "index.html").is_file(), (
        "web/index.html doit être livré avec le serveur (voir Dockerfile)"
    )


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "lichess-mcp" in r.text
    assert "board" in r.text
    import re as _re
    assert _re.search(r'src="util\.js(\?v=\d+)?"', r.text)


def test_web_new_game_engine(client):
    r = _post_json(client, "/web/api/games", {
        "your_name": "Nicolas", "your_color": "white",
        "against_engine": True, "level": "easy",
    })
    assert r.status_code == 200
    state = r.json()
    assert state["white_name"] == "Nicolas"
    assert state["black_name"] == "Moteur"
    assert state["engine_color"] == "black"
    assert state["your_color"] == "white"
    assert state["your_turn"] is True
    assert len(state["legal_moves_uci"]) == 20
    assert state["board"][0][4] == "k"
    assert state["board"][7][4] == "K"
    assert state["board"][6][4] == "P"


def test_web_new_game_free(client):
    r = _post_json(client, "/web/api/games", {
        "your_name": "Nicolas", "your_color": "white",
        "against_engine": False, "opponent_name": "Agent IA",
    })
    assert r.status_code == 200
    state = r.json()
    assert state["white_name"] == "Nicolas"
    assert state["black_name"] == "Agent IA"
    assert state["engine_color"] is None


def test_web_new_game_engine_white_plays_first(client):
    r = _post_json(client, "/web/api/games", {
        "your_name": "Nicolas", "your_color": "black",
        "against_engine": True, "level": "easy",
    })
    state = r.json()
    assert state["engine_color"] == "white"
    assert state["your_turn"] is True
    assert len(state["moves"]) == 1


def test_web_new_game_invalid_params(client):
    r = _post_json(client, "/web/api/games", {"your_color": "rouge"})
    assert r.status_code == 400
    r = _post_json(client, "/web/api/games", {"level": "impossible"})
    assert r.status_code == 400


def test_web_get_game_as_player(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    r = client.get(f"/web/api/games/{gid}?your_color=white")
    assert r.status_code == 200
    state = r.json()
    assert state["your_color"] == "white"
    assert state["your_turn"] is True
    assert len(state["legal_moves_uci"]) == 20


def test_web_get_game_as_spectator(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    r = client.get(f"/web/api/games/{gid}")
    assert r.status_code == 200
    state = r.json()
    assert state["your_color"] is None
    assert state["your_turn"] is False
    assert state["legal_moves_uci"] == []
    assert state["white_name"] == "A"
    assert state["black_name"] == "B"


def test_web_get_unknown_game(client):
    r = client.get("/web/api/games/inconnu")
    assert r.status_code == 404


def test_web_play_move(client):
    gid = server.new_game(white="Nicolas", black="Agent IA")["game_id"]
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "e2e4", "your_color": "white"})
    assert r.status_code == 200
    state = r.json()
    assert state["moves"][0] == "e4"
    assert state["your_turn"] is False
    assert state["last_move"] == "e2e4"


def test_web_play_move_san_accepted(client):
    gid = server.new_game(white="Nicolas", black="Agent IA")["game_id"]
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "Nf3", "your_color": "white"})
    assert r.status_code == 200
    assert r.json()["moves"][0] == "Nf3"


def test_web_play_move_wrong_side_rejected(client):
    gid = server.new_game(white="Nicolas", black="Agent IA")["game_id"]
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "e7e5", "your_color": "black"})
    assert r.status_code == 400


def test_web_play_move_illegal_rejected(client):
    gid = server.new_game(white="Nicolas", black="Agent IA")["game_id"]
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "e2e5", "your_color": "white"})
    assert r.status_code == 400
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "zz9", "your_color": "white"})
    assert r.status_code == 400


def test_web_play_move_engine_side_rejected(client):
    r = _post_json(client, "/web/api/games", {
        "your_name": "Nicolas", "your_color": "white",
        "against_engine": True, "level": "easy",
    })
    gid = r.json()["game_id"]
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "e7e5", "your_color": "black"})
    assert r.status_code == 400


def test_web_move_on_ended_game_rejected(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    server.resign(gid, color="white")
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "e2e4", "your_color": "white"})
    assert r.status_code == 400


def test_web_game_over_state(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
        server.GAMES[gid]["board"].push_uci(uci)
    r = client.get(f"/web/api/games/{gid}?your_color=white")
    state = r.json()
    assert state["over"] is True
    assert "Mat" in state["result"]
    assert state["legal_moves_uci"] == []


def test_web_shared_with_mcp(client):
    gid = server.new_game(white="Nicolas", black="Agent IA")["game_id"]
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "e2e4", "your_color": "white"})
    assert r.status_code == 200
    mcp_state = server.get_board(gid)
    assert mcp_state["fen"] == r.json()["fen"]
    r2 = server.play_move(gid, "e5", color="black")
    web_state = client.get(f"/web/api/games/{gid}?your_color=white").json()
    assert web_state["fen"] == r2["fen"]


def test_web_auth_required_when_token_set(client, monkeypatch):
    monkeypatch.setenv("LICHESS_MCP_AUTH_TOKEN", "secret-token")
    r = _post_json(client, "/web/api/games", {"your_color": "white"})
    assert r.status_code == 401
    gid = server.new_game(white="A", black="B")["game_id"]
    r = client.get(f"/web/api/games/{gid}")
    assert r.status_code == 401
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "e2e4", "your_color": "white"})
    assert r.status_code == 401


def test_web_auth_token_accepted(client, monkeypatch):
    monkeypatch.setenv("LICHESS_MCP_AUTH_TOKEN", "secret-token")
    auth = {"Authorization": "Bearer secret-token"}
    r = client.post("/web/api/games", json={"your_name": "Nicolas", "your_color": "white"}, headers=auth)
    assert r.status_code == 200
    gid = r.json()["game_id"]
    r = client.get(f"/web/api/games/{gid}", headers=auth)
    assert r.status_code == 200


def test_web_index_served_without_auth(client, monkeypatch):
    monkeypatch.setenv("LICHESS_MCP_AUTH_TOKEN", "secret-token")
    r = client.get("/")
    assert r.status_code == 200
