"""Tests du mode « partie libre » (adversaire humain/agent) et de l'analyse."""

import shutil

import chess
import pytest

import server

STOCKFISH = shutil.which("stockfish") or shutil.which("/tmp/sf/stockfish")

needs_stockfish = pytest.mark.skipif(
    not STOCKFISH, reason="Stockfish non installé"
)


@pytest.fixture(autouse=True)
def _clean_state():
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()


def test_new_game_opponent_human():
    r = server.new_game("white", "easy", opponent="human")
    assert r["opponent"] == "human"
    assert r["turn"] == "white"
    assert r["agent_turn"] is False


def test_new_game_invalid_opponent():
    with pytest.raises(ValueError):
        server.new_game("white", "easy", opponent="robot")


def test_human_mode_no_engine_reply():
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    r = server.play_move(gid, "e4", color="white")
    assert r["engine_move"] is None
    assert len(server.GAMES[gid]["board"].move_stack) == 1
    assert r["turn"] == "black"
    assert r["agent_turn"] is True


def test_human_mode_black_side_no_engine_first_move():
    r = server.new_game("black", "easy", opponent="human")
    assert len(server.GAMES[r["game_id"]]["board"].move_stack) == 0
    assert r["agent_color"] == "white"
    assert r["agent_turn"] is True


def test_play_move_with_color_param():
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    r1 = server.play_move(gid, "e4", color="white")
    assert r1["move_played"] == "e4"
    r2 = server.play_move(gid, "e5", color="black")
    assert r2["move_played"] == "e5"
    r3 = server.play_move(gid, "Nf3", color="white")
    assert r3["move_played"] == "Nf3"


def test_play_move_color_turn_mismatch():
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    with pytest.raises(ValueError):
        server.play_move(gid, "e5", color="black")
    with pytest.raises(ValueError):
        server.play_move(gid, "e4", color="rouge")


def test_engine_mode_still_auto_replies():
    gid = server.new_game("white", "easy")["game_id"]
    r = server.play_move(gid, "e4")
    assert r["engine_move"] is not None
    assert len(server.GAMES[gid]["board"].move_stack) == 2


def test_game_status_reports_opponent():
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    assert server.game_status(gid)["opponent"] == "human"


@needs_stockfish
def test_analyze_game():
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    for mv in ("f3", "e5", "g4", "Qh4"):
        color = "white" if len(server.GAMES[gid]["board"].move_stack) % 2 == 0 else "black"
        server.play_move(gid, mv, color=color)
    r = server.analyze_game(gid, depth=10)
    assert r["summary"]["moves"] == 4
    assert "[%eval" in r["pgn"]
    assert r["critical"], "le mat du berger devrait générer des coups critiques"


@needs_stockfish
def test_analyze_game_empty_rejected():
    gid = server.new_game("white", "easy")["game_id"]
    with pytest.raises(ValueError):
        server.analyze_game(gid)


@needs_stockfish
def test_analyze_pgn():
    pgn = '1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7#'
    r = server.analyze_pgn(pgn, depth=8)
    assert r["summary"]["moves"] == 7
    assert "1." in r["pgn"]


@needs_stockfish
def test_analyze_pgn_invalid():
    with pytest.raises(ValueError):
        server.analyze_pgn("ceci n'est pas un pgn")


def test_analyze_without_stockfish(monkeypatch):
    if STOCKFISH:
        monkeypatch.setattr("analysis.stockfish_path", lambda: None)
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    server.play_move(gid, "e4", color="white")
    with pytest.raises(ValueError, match="Stockfish"):
        server.analyze_game(gid)


def test_web_new_game_human_opponent(client):
    r = client.post("/web/api/games", json={"color": "white", "level": "easy", "opponent": "human"})
    assert r.status_code == 200
    state = r.json()
    assert state["opponent"] == "human"
    r = client.post(f"/web/api/games/{state['game_id']}/move", json={"move": "e2e4"})
    assert r.status_code == 200
    state = r.json()
    assert len(state["moves"]) == 1
    assert state["your_turn"] is False


def test_web_new_game_invalid_opponent(client):
    r = client.post("/web/api/games", json={"opponent": "robot"})
    assert r.status_code == 400


def test_human_mode_play_move_defaults_to_opposite_color():
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    # L'agent (MCP, sans color explicite) joue le camp OPPOSÉ au joueur.
    # Ici le joueur a les blancs : l'agent ne peut pas jouer le 1er coup.
    with pytest.raises(ValueError, match="tour"):
        server.play_move(gid, "e4")
    # Le joueur (blancs) joue son coup, puis l'agent (noirs) répond par défaut.
    server.play_move(gid, "e4", color="white")
    r = server.play_move(gid, "e5")
    assert r["move_played"] == "e5"
    assert server.GAMES[gid]["board"].turn == chess.WHITE
    assert r["turn"] == "white"
    assert r["agent_turn"] is False


def test_human_mode_agent_never_shares_player_color():
    gid = server.new_game("black", "easy", opponent="human")["game_id"]
    r = server.play_move(gid, "e4")
    assert r["move_played"] == "e4"
    assert server.GAMES[gid]["player_color"] == "black"
    assert server.GAMES[gid]["board"].turn == chess.BLACK


def test_engine_mode_play_move_defaults_to_player_color():
    gid = server.new_game("white", "easy")["game_id"]
    r = server.play_move(gid, "e4")
    assert r["move_played"] == "e4"
    assert r["engine_move"] is not None


def test_wait_for_turn_ready_immediately():
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    server.play_move(gid, "e4", color="white")
    r = server.wait_for_turn(gid, timeout_seconds=2)
    assert r["ready"] is True
    assert r["turn"] == "black"
    assert r["agent_turn"] is True


def test_wait_for_turn_times_out():
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    r = server.wait_for_turn(gid, timeout_seconds=1)
    assert r["ready"] is False
    assert r["timed_out"] is True


def test_wait_for_turn_game_over():
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    server.resign(gid)
    r = server.wait_for_turn(gid, timeout_seconds=1)
    assert r["ready"] is False
    assert "game_over" in r


def test_wait_for_turn_invalid_color():
    gid = server.new_game("white", "easy", opponent="human")["game_id"]
    with pytest.raises(ValueError):
        server.wait_for_turn(gid, color="rouge")


def test_stockfish_path_env_priority(monkeypatch, tmp_path):
    import analysis
    binary = tmp_path / "sf"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("LICHESS_MCP_STOCKFISH", str(binary))
    monkeypatch.setattr(analysis.shutil, "which", lambda _: None)
    assert analysis.stockfish_path() == str(binary)


def test_stockfish_path_env_missing_file(monkeypatch):
    import analysis
    monkeypatch.setenv("LICHESS_MCP_STOCKFISH", "/inexistant/stockfish")
    monkeypatch.setattr(analysis.shutil, "which", lambda _: None)
    monkeypatch.setattr(analysis.os.path, "isfile", lambda p: False)
    assert analysis.stockfish_path() is None


def test_stockfish_path_fallback_usrgames(monkeypatch):
    import analysis
    monkeypatch.delenv("LICHESS_MCP_STOCKFISH", raising=False)
    monkeypatch.setattr(analysis.shutil, "which", lambda _: None)

    def fake_isfile(p):
        return p == "/usr/games/stockfish"

    monkeypatch.setattr(analysis.os.path, "isfile", fake_isfile)
    assert analysis.stockfish_path() == "/usr/games/stockfish"
