"""Tests de la portée du bandeau « Triche détectée » (bug remonté : lancer un
puzzle faisait apparaître le bandeau à cause d'une alerte globale/sticky).

Règle : le bandeau s'affiche sous l'échiquier d'une partie/d'un puzzle qui
était EN COURS au moment d'un analyze_game (while_in_progress de l'alerte) —
jamais sur les autres échiquiers.
"""

import shutil

import pytest

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


def _web(game_id, your_color=None):
    return server._web_state(game_id, server.GAMES[game_id], your_color)


def test_fresh_puzzle_shows_no_cheat_banner():
    """Lancer un puzzle sans aucune alerte : pas de bandeau."""
    pgid = server.start_puzzle(server.PUZZLES[0]["PuzzleId"])["game_id"]
    assert _web(pgid)["cheat_detected"] is False
    assert _web(pgid)["cheat_alerts"] == []


def test_cheat_banner_scoped_to_the_in_progress_game_not_to_other_boards():
    """Bug remonté : une alerte levée pendant une partie A ne doit PAS
    apparaître sur un puzzle P lancé ensuite (ni sur un échiquier d'analyse)."""
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    # simule un analyze_game pendant la partie A (sans Stockfish : _note_cheat seul)
    server._note_cheat("autre-board")
    assert len(server.CHEAT_ALERTS) == 1
    # la partie A affiche le bandeau
    assert _web(gid)["cheat_detected"] is True
    # un puzzle lancé ensuite : PAS de bandeau
    pgid = server.start_puzzle(server.PUZZLES[0]["PuzzleId"])["game_id"]
    assert _web(pgid)["cheat_detected"] is False
    # un échiquier d'analyse non plus
    aid = server.new_analysis_board()["game_id"]
    assert _web(aid)["cheat_detected"] is False
    # une AUTRE partie en cours pendant l'alerte aurait le bandeau, mais une
    # partie lancée APRES l'alerte non
    gid2 = server.new_game(white="C", black="D")["game_id"]
    assert _web(gid2)["cheat_detected"] is False


def test_banner_shown_on_every_game_in_progress_at_alert_time():
    """Deux parties en cours lors du analyze_game : les deux ont le bandeau."""
    g1 = server.new_game(white="A", black="B")["game_id"]
    g2 = server.new_game(white="C", black="D")["game_id"]
    server._note_cheat("x")
    assert g1 in server.CHEAT_ALERTS[-1]["while_in_progress"]
    assert g2 in server.CHEAT_ALERTS[-1]["while_in_progress"]
    assert _web(g1)["cheat_detected"] is True
    assert _web(g2)["cheat_detected"] is True


def test_banner_stays_on_the_game_board_after_it_ends():
    """La partie terminée conserve le bandeau (trace du debrief)."""
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    server._note_cheat("x")
    server.resign(gid, color="black")
    state = _web(gid)
    assert state["over"] is True
    assert state["cheat_detected"] is True


@needs_stockfish
def test_analyze_during_puzzle_shows_banner_on_the_puzzle_only():
    """End-to-end : analyze_game pendant un puzzle en cours -> bandeau sur le
    puzzle, pas sur l'échiquier d'analyse analysé."""
    pgid = server.start_puzzle(server.PUZZLES[0]["PuzzleId"])["game_id"]
    aid = server.new_analysis_board()["game_id"]
    server.play_move(aid, "e4", color="white")
    r = server.analyze_game(aid, depth=3, publish=False)
    assert r["cheat_detected"] is True
    assert pgid in r["cheat_alert"]["while_in_progress"]
    assert _web(pgid)["cheat_detected"] is True
    assert _web(aid)["cheat_detected"] is False  # l'échiquier analysé n'est pas une partie en cours
