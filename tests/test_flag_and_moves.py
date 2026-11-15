"""Tests des évolutions :

- détection de drapeau centralisée dans `_is_over` : un drapeau tombé est
  visible de TOUTE lecture d'état (game_status, list_games, état web, resign),
  sans qu'aucun appel précédent n'ait déclenché la détection ;
- outil `get_moves(game_id, from_ply)` : liste des coups en SAN (+ UCI),
  complète ou tronquée depuis `from_ply`.
"""

import asyncio
import time

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


def _clock_game_black_to_move() -> str:
    """Partie 1+0, deux joueurs ayant rejoint, 1. e4 joué : noirs au trait."""
    gid = server.new_game(white="A", black="B", clock="1+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    server.play_move(gid, "e4", color="white", player="A")
    return gid


def _black_flag_falls(gid: str) -> None:
    """Il ne reste que 0,2 s aux noirs (au trait) : le drapeau tombe."""
    server.GAMES[gid]["clock_ms"]["black"] = 200
    time.sleep(0.5)


# ------------------------------------------------- drapeau visible partout

def test_game_status_flips_after_flag_caught_by_wait_for_turn_on_other_side():
    """Détection live : long-poller le camp qui N'a PAS le trait attrape le
    drapeau (poller son propre camp rendrait immédiatement « ready »).
    Ensuite, game_status bascule — appel après appel."""
    gid = _clock_game_black_to_move()
    _black_flag_falls(gid)
    r = server.wait_for_turn(gid, color="white", timeout_seconds=5)
    assert r["ready"] is False
    assert "A (white) gagne au drapeau" in r["game_over"]
    for _ in range(3):
        s = server.game_status(gid)
        assert s["over"] is True
        assert s["turn"] == "black"
        assert s["move_number"] == 1
        assert "A (white) gagne au drapeau" in s["result"]
        assert s["winner"] == {"name": "A", "color": "white"}


def test_list_games_and_resign_see_flag_without_prior_detection():
    """list_games et resign ne déclenchent aucune détection eux-mêmes : la
    lecture d'état doit détecter le drapeau elle-même."""
    gid = _clock_game_black_to_move()
    _black_flag_falls(gid)
    g = next(g for g in server.list_games()["games"] if g["game_id"] == gid)
    assert g["status"] == "terminée"
    with pytest.raises(ValueError, match="déjà terminée"):
        server.resign(gid, color="white")
    assert server.game_status(gid)["over"] is True
    assert server.get_board(gid)["over"] is True


def test_web_state_over_on_first_poll_after_flag(client):
    """Le premier poll web après la chute du drapeau voit déjà over=True."""
    gid = _clock_game_black_to_move()
    _black_flag_falls(gid)
    r = client.get(f"/web/api/games/{gid}?your_color=white")
    assert r.status_code == 200
    state = r.json()
    assert state["over"] is True
    assert state["status"] == "terminée"
    assert "A (white) gagne au drapeau" in state["result"]
    assert state["winner"] == {"name": "A", "color": "white"}


def test_no_false_flag_while_time_remains():
    """Tant que du temps reste, aucune lecture ne voit la partie terminée."""
    gid = _clock_game_black_to_move()
    assert server.game_status(gid)["over"] is False
    assert server.get_board(gid)["over"] is False
    g = next(g for g in server.list_games()["games"] if g["game_id"] == gid)
    assert g["status"] == "en cours"


# ------------------------------------------------- get_moves

def test_get_moves_full_san_and_uci():
    gid = server.new_game(white="A", black="B")["game_id"]
    for uci, color in (("e2e4", "white"), ("e7e5", "black"),
                       ("g1f3", "white"), ("b8c6", "black"),
                       ("f1c4", "white"), ("f8c5", "black")):
        server.play_move(gid, uci, color=color)
    server.play_move(gid, "e1g1", color="white")  # petit roque
    r = server.get_moves(gid)
    assert r["game_id"] == gid
    assert r["moves"] == ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "O-O"]
    assert r["uci"] == ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "e1g1"]
    assert r["plies"] == 7 and r["from_ply"] == 0 and r["count"] == 7


def test_get_moves_from_ply_returns_only_the_tail():
    gid = server.new_game(white="A", black="B")["game_id"]
    for uci, color in (("e2e4", "white"), ("e7e5", "black"),
                       ("g1f3", "white"), ("b8c6", "black")):
        server.play_move(gid, uci, color=color)
    r = server.get_moves(gid, from_ply=2)
    assert r["moves"] == ["Nf3", "Nc6"]
    assert r["uci"] == ["g1f3", "b8c6"]
    assert r["from_ply"] == 2 and r["count"] == 2 and r["plies"] == 4
    assert server.get_moves(gid, from_ply=4)["moves"] == []
    with pytest.raises(ValueError, match="from_ply"):
        server.get_moves(gid, from_ply=5)
    with pytest.raises(ValueError, match="from_ply"):
        server.get_moves(gid, from_ply=-1)


def test_get_moves_empty_game():
    gid = server.new_game()["game_id"]
    r = server.get_moves(gid)
    assert r["moves"] == [] and r["uci"] == []
    assert r["plies"] == 0 and r["count"] == 0


def test_get_moves_analysis_board_custom_fen_san_correct():
    """Depuis un FEN personnalisé, les SAN sont calculés depuis la position
    racine (pas de garbage) — ex. prise de dame par le cavalier de c7."""
    gid = server.new_analysis_board("7k/2N5/5N2/3q4/8/8/8/4K3 w - - 0 1")["game_id"]
    server.play_move(gid, "Ncd5", color="white")
    r = server.get_moves(gid)
    assert r["moves"] == ["Ncxd5"]
    assert r["uci"] == ["c7d5"]
