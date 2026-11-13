"""Tests des échiquiers d'analyse (dry-run), wait_for_turn et analyse Stockfish."""

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


# ---------------------------------------------------------------- échiquier d'analyse

def test_analysis_board_default_position():
    r = server.new_analysis_board()
    assert r["kind"] == "analysis"
    assert r["turn"] == "white"
    assert r["engine_color"] is None
    assert len(server.legal_moves(r["game_id"])["moves"]) == 20


def test_analysis_board_from_fen():
    fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    r = server.new_analysis_board(fen)
    assert r["fen"] == fen
    assert r["turn"] == "black"


def test_analysis_board_invalid_fen():
    with pytest.raises(ValueError, match="FEN"):
        server.new_analysis_board("ceci n'est pas un fen")


def test_analysis_board_play_both_sides():
    gid = server.new_analysis_board()["game_id"]
    r1 = server.play_move(gid, "e4", color="white")
    assert r1["move_played"] == "e4"
    r2 = server.play_move(gid, "e5", color="black")
    assert r2["move_played"] == "e5"
    assert r2["engine_move"] is None
    r3 = server.play_move(gid, "Nf3", color="white")
    assert r3["move_played"] == "Nf3"


def test_analysis_board_default_color_is_turn():
    gid = server.new_analysis_board()["game_id"]
    assert server.play_move(gid, "e4")["color_played"] == "white"
    assert server.play_move(gid, "e5")["color_played"] == "black"


def test_analysis_board_undo_pops_one():
    gid = server.new_analysis_board()["game_id"]
    server.play_move(gid, "e4", color="white")
    server.undo(gid)
    assert len(server.GAMES[gid]["board"].move_stack) == 0


def test_analysis_board_get_board_and_legal_moves():
    fen = "6k1/5r2/4Q3/8/8/8/8/4K3 b - - 0 1"
    gid = server.new_analysis_board(fen)["game_id"]
    board = server.get_board(gid)
    assert board["in_check"] is False
    moves = server.legal_moves(gid)["moves"]
    assert moves == ["Kh8", "Kf8", "Kh7", "Kg7"]


# ---------------------------------------------------------------- wait_for_turn

def test_wait_for_turn_ready_immediately():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    r = server.wait_for_turn(gid, color="black", timeout_seconds=2)
    assert r["ready"] is True
    assert r["turn"] == "black"


def test_wait_for_turn_times_out():
    gid = server.new_game(white="A", black="B")["game_id"]
    r = server.wait_for_turn(gid, color="black", timeout_seconds=1)
    assert r["ready"] is False
    assert r["timed_out"] is True


def test_wait_for_turn_game_over():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.resign(gid, color="white")
    r = server.wait_for_turn(gid, color="black", timeout_seconds=1)
    assert r["ready"] is False
    assert "game_over" in r


def test_wait_for_turn_color_required():
    gid = server.new_game(white="A", black="B")["game_id"]
    with pytest.raises(ValueError):
        server.wait_for_turn(gid, color="rouge")
    with pytest.raises(ValueError):
        server.wait_for_turn(gid)


# ---------------------------------------------------------------- analyse Stockfish

@needs_stockfish
def test_analyze_game():
    gid = server.new_game(white="A", black="B")["game_id"]
    for uci, color in (("f2f3", "white"), ("e7e5", "black"), ("g2g4", "white"), ("d8h4", "black")):
        server.play_move(gid, uci, color=color)
    r = server.analyze_game(gid, depth=10)
    assert r["summary"]["moves"] == 4
    assert "[%eval" in r["pgn"]
    assert r["critical"], "le mat du berger devrait générer des coups critiques"


@needs_stockfish
def test_analyze_game_empty_rejected():
    gid = server.new_game(white="A", black="B")["game_id"]
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
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    with pytest.raises(ValueError, match="Stockfish"):
        server.analyze_game(gid)


# ---------------------------------------------------------------- détection Stockfish

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


@needs_stockfish
def test_analyze_game_publishes_to_web():
    gid = server.new_game(white="A", black="B")["game_id"]
    for uci, color in (("f2f3", "white"), ("e7e5", "black"), ("g2g4", "white"), ("d8h4", "black")):
        server.play_move(gid, uci, color=color)
    r = server.analyze_game(gid, depth=10)
    assert r["published"] is True
    assert len(r["evals"]) == 4
    assert all("eval_white" in e for e in r["evals"])
    # perspective blancs : après le mat du berger, les blancs sont mats
    assert r["evals"][-1]["eval_white"] <= -90000
    # l'état web expose l'analyse
    web = server._web_state(gid, server.GAMES[gid], None)
    assert web["analysis"]["depth"] == 10
    assert len(web["analysis"]["evals"]) == 4


@needs_stockfish
def test_analyze_game_no_publish():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    r = server.analyze_game(gid, depth=8, publish=False)
    assert "published" not in r
    assert server.GAMES[gid].get("analysis") is None
