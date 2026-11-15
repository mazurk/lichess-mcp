"""Tests des évolutions :

- `delete_game(game_id)` : suppression définitive d'une partie, d'un
  échiquier d'analyse ou d'un puzzle publié ;
- alerte « triche détectée » : tout appel à `analyze_game` lancé alors qu'une
  partie ou un puzzle est en cours est enregistré (même si l'échiquier
  analysé est un autre id) et remonté dans l'état web + l'UI (cheatAlert).
"""

import asyncio
import shutil

import httpx
import pytest
from httpx import ASGITransport

import server

STOCKFISH = shutil.which("stockfish") or shutil.which("/tmp/sf/stockfish")
needs_stockfish = pytest.mark.skipif(not STOCKFISH, reason="Stockfish non installé")


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("LICHESS_MCP_AUTH_TOKEN", raising=False)
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    server.CHEAT_ALERTS.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    server.CHEAT_ALERTS.clear()


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


# ---------------------------------------------------------------- delete_game

def test_delete_analysis_board():
    gid = server.new_analysis_board()["game_id"]
    server.play_move(gid, "e4", color="white")
    r = server.delete_game(gid)
    assert r["deleted"] == gid
    assert r["kind"] == "analysis"
    assert r["remaining"] == 0
    assert gid not in server.GAMES
    with pytest.raises(ValueError, match="Partie inconnue"):
        server.get_board(gid)
    with pytest.raises(ValueError, match="Partie inconnue"):
        server.delete_game(gid)  # double suppression refusée
    assert all(g["game_id"] != gid for g in server.list_games()["games"])


def test_delete_game_and_puzzle():
    gid = server.new_game(white="A", black="B")["game_id"]
    assert server.delete_game(gid)["kind"] == "game"
    pid = server.PUZZLES[0]["PuzzleId"]
    pgid = server.start_puzzle(pid)["game_id"]
    assert server.GAMES[pgid]["kind"] == "puzzle"
    r = server.delete_game(pgid)
    assert r["kind"] == "puzzle"
    assert pgid not in server.GAMES
    assert pid not in server.ACTIVE_PUZZLES


def test_delete_unknown_game_rejected():
    with pytest.raises(ValueError, match="Partie inconnue"):
        server.delete_game("nope1234")


# ------------------------------------------------- alerte « triche détectée »

@needs_stockfish
def test_analyze_in_progress_game_flags_cheat():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    r = server.analyze_game(gid, depth=3, publish=False)
    assert r["cheat_detected"] is True
    assert r["cheat_alert"]["analyzed"] == gid
    assert gid in r["cheat_alert"]["while_in_progress"]
    assert len(server.CHEAT_ALERTS) == 1
    # tout appel tant que la partie est en cours : nouvelle alerte à chaque fois
    r2 = server.analyze_game(gid, depth=3, publish=False)
    assert r2["cheat_detected"] is True
    assert len(server.CHEAT_ALERTS) == 2


@needs_stockfish
def test_analyze_other_board_while_game_in_progress_still_flags_cheat():
    """L'alerte est globale : même si l'id analysé n'est PAS la partie en cours."""
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    aid = server.new_analysis_board()["game_id"]
    server.play_move(aid, "e4", color="white")
    r = server.analyze_game(aid, depth=3, publish=False)
    assert r["cheat_detected"] is True
    alert = server.CHEAT_ALERTS[-1]
    assert alert["analyzed"] == aid          # échiquier analysé...
    assert gid in alert["while_in_progress"]  # ...mais la partie en cours est signalée
    assert aid not in alert["while_in_progress"]


@needs_stockfish
def test_analyze_while_puzzle_in_progress_flags_cheat():
    pid = server.PUZZLES[0]["PuzzleId"]
    pgid = server.start_puzzle(pid)["game_id"]
    aid = server.new_analysis_board()["game_id"]
    server.play_move(aid, "d4", color="white")
    r = server.analyze_game(aid, depth=3, publish=False)
    assert r["cheat_detected"] is True
    assert pgid in server.CHEAT_ALERTS[-1]["while_in_progress"]


@needs_stockfish
def test_analysis_done_before_game_start_is_not_cheating():
    """Un board déjà analysé AVANT le début de la partie en cours : ré-analyser
    pendant la partie ne lève PAS d'alerte (l'analyse préexistait)."""
    aid = server.new_analysis_board()["game_id"]
    server.play_move(aid, "e4", color="white")
    r1 = server.analyze_game(aid, depth=3, publish=False)  # avant toute partie
    assert r1["cheat_detected"] is False
    assert server.CHEAT_ALERTS == []
    # une partie démarre ensuite
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    # ré-analyser le même board pendant la partie : analyse préexistante -> pas d'alerte
    r2 = server.analyze_game(aid, depth=3, publish=False)
    assert r2["cheat_detected"] is False
    assert server.CHEAT_ALERTS == []
    # mais analyser un board SANS analyse préalable pendant la partie -> alerte
    aid2 = server.new_analysis_board()["game_id"]
    server.play_move(aid2, "d4", color="white")
    r3 = server.analyze_game(aid2, depth=3, publish=False)
    assert r3["cheat_detected"] is True
    assert gid in server.CHEAT_ALERTS[-1]["while_in_progress"]


@needs_stockfish
def test_no_cheat_alert_without_game_or_puzzle_in_progress():
    aid = server.new_analysis_board()["game_id"]
    server.play_move(aid, "e4", color="white")
    r = server.analyze_game(aid, depth=3, publish=False)
    assert r["cheat_detected"] is False
    assert "cheat_alert" not in r
    assert server.CHEAT_ALERTS == []


@needs_stockfish
def test_no_new_cheat_alert_once_game_is_over():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    server.resign(gid, color="black")  # partie terminée : plus « en cours »
    r = server.analyze_game(gid, depth=3, publish=False)
    assert r["cheat_detected"] is False
    assert server.CHEAT_ALERTS == []


@needs_stockfish
def test_web_state_and_ui_expose_cheat_alert(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    server.analyze_game(gid, depth=3, publish=False)
    r = client.get(f"/web/api/games/{gid}?your_color=white")
    assert r.status_code == 200
    state = r.json()
    assert state["cheat_detected"] is True
    assert len(state["cheat_alerts"]) == 1
    assert state["cheat_alerts"][0]["analyzed"] == gid
    # l'alerte est affichée sous l'échiquier dans l'UI, label seul « Triche détectée »
    html = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (server.WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert 'id="cheatAlert"' in html
    assert "cheatAlert" in js and "cheat_detected" in js
    assert 'cheatEl.textContent = "Triche détectée"' in js
