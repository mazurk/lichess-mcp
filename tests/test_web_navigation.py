"""Tests de la navigation web dans l'historique (position initiale, SAN sur FEN perso)."""

import asyncio

import chess
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

    return _AsyncClient()


def test_web_state_start_fen_and_custom_fen_history(client):
    """Régression : échiquier d'analyse créé avec un FEN personnalisé — les SAN
    de l'historique web doivent être calculés depuis le FEN de départ (et non
    depuis la position initiale standard), et start_fen expose ce FEN de départ
    pour la navigation « position initiale » de l'interface."""
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
    gid = server.new_analysis_board(fen)["game_id"]
    server.play_move(gid, "O-O", color="white")
    server.play_move(gid, "a6", color="black")
    state = client.get(f"/web/api/games/{gid}").json()
    assert state["kind"] == "analysis"
    assert state["start_fen"] == fen
    # SAN corrects (rejoués depuis le FEN de départ, pas depuis la position initiale)
    assert state["moves"] == ["O-O", "a6"]
    assert state["history"][0]["san"] == "O-O"
    assert state["history"][1]["san"] == "a6"
    replay = chess.Board(fen)
    replay.push_uci("e1g1")
    assert state["history"][0]["fen"] == replay.fen()
    replay.push_uci("a7a6")
    assert state["history"][1]["fen"] == replay.fen()


def test_web_state_start_fen_default_game(client):
    """Sur une partie classique, start_fen est la position initiale standard."""
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    state = client.get(f"/web/api/games/{gid}").json()
    assert state["start_fen"] == chess.Board().fen()
    assert state["moves"] == ["e4"]
