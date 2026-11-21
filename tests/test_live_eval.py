"""Issue #94 : jauge d'évaluation temps réel pour les spectateurs (web) +
outil MCP spectator_analysis pour les agents spectateurs.

Le calcul Stockfish est monkeypatché (pas de Stockfish en local) : on teste le
déclenchement (spectateur présent), le recalcul à chaque coup, l'arrêt sans
spectateur, le filtrage fair-play côté web (live_eval jamais renvoyée aux
joueurs) et l'outil MCP.
"""

import asyncio
import time

import chess
import httpx
import pytest
from httpx import ASGITransport

import server


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("LICHESS_MCP_AUTH_TOKEN", raising=False)
    server.GAMES.clear()
    yield
    server.GAMES.clear()


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


def _fake_compute(game):
    """Remplace le calcul Stockfish : eval fixe dépendant du nombre de coups."""
    ply = len(game["board"].move_stack)
    return {
        "eval_white": 100 + ply,
        "eval": f"+{100 + ply:.0f}.00",
        "pv_uci": ["e2e4"],
        "pv_san": ["e4"],
        "depth": server._LIVE_EVAL_DEPTH,
        "fen": game["board"].fen(),
        "at_ply": ply,
        "at": "now",
    }


def _mk_game():
    return server._create_game("Blancs", "Noirs", "medium", None, kind="game")


def _wait_live(gid, timeout=5.0):
    """Attend la fin du thread live éventuel."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with server._LIVE_LOCK:
            if gid not in server._LIVE_RUNNING:
                return True
        time.sleep(0.05)
    return False


def test_no_live_eval_without_spectator(client, monkeypatch):
    """Sans spectateur : aucune analyse live déclenchée, live_eval absent du state."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    r = client.get(f"/web/api/games/{gid}")
    assert r.status_code == 200
    assert r.json()["live_eval"] is None
    assert server.GAMES[gid]["live_eval"] is None
    # un joueur (non spectateur) qui rejoint ne déclenche rien non plus
    client.post(f"/web/api/games/{gid}/join", json={"name": "Alice", "color": "white"})
    _wait_live(gid)
    assert server.GAMES[gid]["live_eval"] is None
    assert client.get(f"/web/api/games/{gid}").json()["live_eval"] is None


def test_spectator_join_triggers_live_eval_and_web_filter(client, monkeypatch):
    """Un spectateur déclenche l'eval live ; seul le state demandé avec
    spectator=1 la contient (fair-play : jamais renvoyée aux joueurs)."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    client.post(f"/web/api/games/{gid}/join", json={"name": "Alice", "color": "white"})
    r = client.post(f"/web/api/games/{gid}/join", json={"name": "Bob", "spectate": True})
    assert r.status_code == 200
    assert r.json()["live_eval"] is not None  # le join spectateur la renvoie
    assert _wait_live(gid)
    game = server.GAMES[gid]
    assert game["live_eval"]["eval_white"] == 100  # 0 coup joué
    assert game["live_eval"]["at_ply"] == 0
    # spectateur web (spectator=1) -> reçoit live_eval
    r = client.get(f"/web/api/games/{gid}?spectator=1")
    assert r.json()["live_eval"]["eval_white"] == 100
    # joueur (your_color, sans spectator=1) -> JAMAIS live_eval (fair-play)
    r = client.get(f"/web/api/games/{gid}?your_color=white")
    assert r.json()["live_eval"] is None
    r = client.get(f"/web/api/games/{gid}")
    assert r.json()["live_eval"] is None


def test_live_eval_recomputed_on_move(client, monkeypatch):
    """À chaque coup joué, l'eval live est recalculée (at_ply suit)."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    client.post(f"/web/api/games/{gid}/join", json={"name": "Alice", "color": "white"})
    client.post(f"/web/api/games/{gid}/join", json={"name": "Bob", "spectate": True})
    assert _wait_live(gid)
    assert server.GAMES[gid]["live_eval"]["at_ply"] == 0
    # Alice joue e2e4
    r = client.post(f"/web/api/games/{gid}/move",
                    json={"move": "e2e4", "your_color": "white", "your_name": "Alice"})
    assert r.status_code == 200
    assert _wait_live(gid)
    live = server.GAMES[gid]["live_eval"]
    assert live["at_ply"] == 1
    assert live["eval_white"] == 101  # recalculé (fake dépend du pli)
    # le state du move (joueur) ne contient pas live_eval (fair-play)
    assert r.json()["live_eval"] is None


def test_live_eval_stops_when_no_spectator(client, monkeypatch):
    """Plus aucun spectateur -> le cache live_eval est vidé (économie Pi)."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    client.post(f"/web/api/games/{gid}/join", json={"name": "Bob", "spectate": True})
    assert _wait_live(gid)
    assert server.GAMES[gid]["live_eval"] is not None
    # Bob "part" (plus de spectateur enregistré)
    server.GAMES[gid]["spectators"].clear()
    r = client.get(f"/web/api/games/{gid}?spectator=1")
    assert r.json()["live_eval"] is None
    assert server.GAMES[gid]["live_eval"] is None


def test_spectator_analysis_tool(monkeypatch):
    """Outil MCP spectator_analysis : eval + PV de la position courante."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    out = server.spectator_analysis(gid)
    assert out["game_id"] == gid
    assert out["eval_white"] == 100
    assert out["eval"] == "+100.00"
    assert out["pv_san"] == ["e4"] and out["pv_uci"] == ["e2e4"]
    assert out["depth"] == server._LIVE_EVAL_DEPTH
    assert out["turn"] == "white" and out["plies"] == 0 and out["over"] is False
    # cache : second appel sans recalcul (même at_ply)
    out2 = server.spectator_analysis(gid)
    assert out2["eval_white"] == 100
    # partie inconnue -> erreur
    with pytest.raises(ValueError):
        server.spectator_analysis("inconnu00")
    # kind != game -> refusé
    gid2 = server._create_game("Blancs", "Noirs", "medium", None, kind="analysis")
    with pytest.raises(ValueError):
        server.spectator_analysis(gid2)


def test_evaluate_position_requires_stockfish(monkeypatch):
    """analysis.evaluate_position lève une erreur claire sans Stockfish."""
    import analysis
    monkeypatch.setattr(analysis, "stockfish_path", lambda: None)
    with pytest.raises(ValueError, match="Stockfish"):
        analysis.evaluate_position(chess.Board(), depth=10)
