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
    assert _re.search(r'src="util\.js(\?v=[\w]+)?"', r.text)


def test_web_new_game_engine(client):
    r = _post_json(client, "/web/api/games", {
        "your_name": "Alice", "your_color": "white",
        "against_engine": True, "level": "easy",
    })
    assert r.status_code == 200
    state = r.json()
    assert state["white_name"] == "Alice"
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
        "your_name": "Alice", "your_color": "white",
        "against_engine": False, "opponent_name": "Agent IA",
    })
    assert r.status_code == 200
    state = r.json()
    assert state["white_name"] == "Alice"
    assert state["black_name"] == "Agent IA"
    assert state["engine_color"] is None


def test_web_new_game_engine_white_plays_first(client):
    r = _post_json(client, "/web/api/games", {
        "your_name": "Alice", "your_color": "black",
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
    gid = server.new_game(white="Alice", black="Agent IA")["game_id"]
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "e2e4", "your_color": "white"})
    assert r.status_code == 200
    state = r.json()
    assert state["moves"][0] == "e4"
    assert state["your_turn"] is False
    assert state["last_move"] == "e2e4"


def test_web_play_move_san_accepted(client):
    gid = server.new_game(white="Alice", black="Agent IA")["game_id"]
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "Nf3", "your_color": "white"})
    assert r.status_code == 200
    assert r.json()["moves"][0] == "Nf3"


def test_web_play_move_wrong_side_rejected(client):
    gid = server.new_game(white="Alice", black="Agent IA")["game_id"]
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "e7e5", "your_color": "black"})
    assert r.status_code == 400


def test_web_play_move_illegal_rejected(client):
    gid = server.new_game(white="Alice", black="Agent IA")["game_id"]
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "e2e5", "your_color": "white"})
    assert r.status_code == 400
    r = _post_json(client, f"/web/api/games/{gid}/move", {"move": "zz9", "your_color": "white"})
    assert r.status_code == 400


def test_web_play_move_engine_side_rejected(client):
    r = _post_json(client, "/web/api/games", {
        "your_name": "Alice", "your_color": "white",
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
    gid = server.new_game(white="Alice", black="Agent IA")["game_id"]
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
    r = client.post("/web/api/games", json={"your_name": "Alice", "your_color": "white"}, headers=auth)
    assert r.status_code == 200
    gid = r.json()["game_id"]
    r = client.get(f"/web/api/games/{gid}", headers=auth)
    assert r.status_code == 200


def test_web_index_served_without_auth(client, monkeypatch):
    monkeypatch.setenv("LICHESS_MCP_AUTH_TOKEN", "secret-token")
    r = client.get("/")
    assert r.status_code == 200


def test_web_state_exposes_turn(client):
    """L'indicateur de trait (page web) dépend du champ turn."""
    gid = server.new_game(white="A", black="B")["game_id"]
    r = client.get(f"/web/api/games/{gid}?your_color=white")
    assert r.json()["turn"] == "white"
    server.GAMES[gid]["board"].push_uci("e2e4")
    r = client.get(f"/web/api/games/{gid}?your_color=white")
    assert r.json()["turn"] == "black"


def test_web_state_history_for_navigation(client):
    """Navigation web : chaque coup expose san, uci et fen de la position résultante."""
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    server.play_move(gid, "e5", color="black")
    r = client.get(f"/web/api/games/{gid}")
    state = r.json()
    assert state["history"][0]["san"] == "e4"
    assert state["history"][0]["uci"] == "e2e4"
    assert "4P3" in state["history"][0]["fen"]  # pawn blanc en e4
    assert state["history"][1]["san"] == "e5"


def test_web_state_status_fields(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    state = client.get(f"/web/api/games/{gid}").json()
    assert state["status"] == "en cours"
    assert state["analysed"] is False
    for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
        server.GAMES[gid]["board"].push_uci(uci)
    state = client.get(f"/web/api/games/{gid}").json()
    assert state["status"] == "terminée"


def test_web_games_list_endpoint(client):
    gid1 = server.new_game(white="Alice", black="Bob")["game_id"]
    gid2 = server.new_game(engine_color="black")["game_id"]
    r = client.get("/web/api/games")
    assert r.status_code == 200
    games = {g["game_id"]: g for g in r.json()["games"]}
    assert gid1 in games and gid2 in games
    g1 = games[gid1]
    assert g1["white_name"] == "Alice"
    assert g1["status"] == "en cours"
    assert g1["analysed"] is False
    assert g1["plies"] == 0
    assert g1["puzzle"] is None


def test_web_puzzle_fields_in_list_and_state(client):
    """Un puzzle publié (kind="puzzle") est listé et consultable sur le web,
    avec sa progression (sans la solution)."""
    puzzle = server.PUZZLES[0]
    gid = server.start_puzzle(puzzle_id=puzzle["PuzzleId"], name="Puzzle du jour")["game_id"]
    games = client.get("/web/api/games").json()["games"]
    g = next(g for g in games if g["game_id"] == gid)
    assert g["kind"] == "puzzle"
    assert g["name"] == "Puzzle du jour"
    assert g["puzzle"]["puzzle_id"] == puzzle["PuzzleId"]
    assert g["puzzle"]["solved"] is False
    state = client.get(f"/web/api/games/{gid}").json()
    assert state["kind"] == "puzzle"
    assert state["name"] == "Puzzle du jour"
    assert state["puzzle"]["plies_done"] == 0
    assert state["puzzle"]["total_plies"] == len(puzzle["Moves"].split())
    # après un coup gagnant, la progression et les coups sont visibles côté web
    line = puzzle["Moves"].split()
    server.try_puzzle_move(puzzle["PuzzleId"], line[0], game_id=gid)
    state = client.get(f"/web/api/games/{gid}").json()
    assert state["puzzle"]["plies_done"] >= 1
    assert len(state["moves"]) >= 1


def test_server_version_is_0xx_and_exposed_on_mcp():
    """Numérotation 0.XX (XX sur 2 chiffres, incrémenté avant chaque livraison
    sur main — voir AGENTS.md §5), propagée au serveur MCP (serverInfo)."""
    import json
    import re
    from pathlib import Path
    assert re.fullmatch(r"0\.\d{2}", server.__version__), server.__version__
    assert server.mcp.version == server.__version__
    # package.json reste synchronisé avec __version__
    pkg = json.loads((Path(server.__file__).resolve().parent / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == server.__version__


def test_web_api_version(client):
    """GET /web/api/version -> {version, commit} (badge coin haut gauche, issue #67)."""
    r = client.get("/web/api/version")
    assert r.status_code == 200
    data = r.json()
    assert data["version"] == server.__version__
    assert "commit" in data  # None en test (pas de build-arg), renseigné en prod


def test_web_api_version_requires_token_when_configured(client, monkeypatch):
    """Avec LICHESS_MCP_AUTH_TOKEN configuré, /web/api/version exige le bearer."""
    monkeypatch.setenv("LICHESS_MCP_AUTH_TOKEN", "secret-test")
    r = client.get("/web/api/version")
    assert r.status_code == 401
    r = client.get("/web/api/version", headers={"Authorization": "Bearer secret-test"})
    assert r.status_code == 200
    assert r.json()["version"] == server.__version__
