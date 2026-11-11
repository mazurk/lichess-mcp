"""Tests fonctionnels du serveur MCP d'échecs hors ligne."""

import chess
import pytest

import server


@pytest.fixture(autouse=True)
def _clean_state():
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()


def test_new_game_white():
    result = server.new_game("white", "easy")
    assert result["player_color"] == "white"
    assert result["turn"] == "white"
    assert result["agent_turn"] is True
    assert "game_id" in result
    assert "fen" in result


def test_new_game_black_engine_plays_first():
    result = server.new_game("black", "easy")
    assert result["engine_move"] is not None
    assert result["turn"] == "black"
    assert result["agent_turn"] is True


def test_new_game_invalid_params():
    with pytest.raises(ValueError):
        server.new_game("rouge", "easy")
    with pytest.raises(ValueError):
        server.new_game("white", "impossible")


def test_play_move_san_and_uci():
    game = server.new_game("white", "easy")
    gid = game["game_id"]
    r1 = server.play_move(gid, "e4")
    assert r1["move_played"] == "e4"
    r2 = server.play_move(gid, "g1f3")
    assert r2["move_played"] == "Nf3"


def test_play_move_uci_format():
    game = server.new_game("white", "easy")
    gid = game["game_id"]
    r = server.play_move(gid, "d2d4")
    assert r["move_played"] == "d4"


def test_play_move_illegal_rejected():
    game = server.new_game("white", "easy")
    gid = game["game_id"]
    with pytest.raises(ValueError):
        server.play_move(gid, "e5")
    with pytest.raises(ValueError):
        server.play_move(gid, "Ke2")


def test_play_move_not_your_turn():
    game = server.new_game("white", "easy")
    gid = game["game_id"]
    server.GAMES[gid]["board"].push_uci("e2e4")
    with pytest.raises(ValueError):
        server.play_move(gid, "d4")


def test_fools_mate_detected():
    game = server.new_game("white", "medium")
    gid = game["game_id"]
    board = server.GAMES[gid]["board"]
    for uci in ("f2f3", "e7e5", "g2g4"):
        board.push_uci(uci)
    board.push_uci("d8h4")
    assert board.is_checkmate()
    info = server._game_over_info(board, server.GAMES[gid]["player_color"])
    assert "Mat" in info
    assert "black" in info


def test_get_board_and_legal_moves():
    game = server.new_game("white", "easy")
    gid = game["game_id"]
    board = server.get_board(gid)
    assert board["turn"] == "white"
    assert board["agent_turn"] is True
    assert board["in_check"] is False
    moves = server.legal_moves(gid)["moves"]
    assert len(moves) == 20
    assert "e4" in moves


def test_undo():
    game = server.new_game("white", "easy")
    gid = game["game_id"]
    before = server.GAMES[gid]["board"].fen()
    server.play_move(gid, "e4")
    after = server.undo(gid)
    assert after["fen"].startswith(before.split()[0])


def test_resign():
    game = server.new_game("white", "easy")
    gid = game["game_id"]
    r = server.resign(gid)
    assert "abandonné" in r["game_over"].lower()
    with pytest.raises(ValueError):
        server.play_move(gid, "e4")


def test_game_status():
    game = server.new_game("white", "easy")
    gid = game["game_id"]
    status = server.game_status(gid)
    assert status["over"] is False
    assert status["player_color"] == "white"


def test_unknown_game_rejected():
    with pytest.raises(ValueError):
        server.get_board("inexistant")
    with pytest.raises(ValueError):
        server.play_move("inexistant", "e4")


def test_puzzles_all_solvable():
    for puzzle in server.PUZZLES:
        drawn = server.next_puzzle()
        pid = drawn["puzzle_id"]
        solution = server.ACTIVE_PUZZLES[pid]["Moves"]
        board = chess.Board(server.ACTIVE_PUZZLES[pid]["FEN"])
        assert board.turn is not None
        correct = server.try_puzzle_move(pid, solution)
        assert correct["correct"] is True
        wrong = server.try_puzzle_move(pid, "a1a3")
        assert wrong["correct"] is False


def test_puzzle_unknown_id():
    with pytest.raises(ValueError):
        server.try_puzzle_move("inconnu", "e4")


def test_puzzle_invalid_move_not_crash():
    drawn = server.next_puzzle()
    r = server.try_puzzle_move(drawn["puzzle_id"], "zz9")
    assert r["correct"] is False


def test_board_str_rendering():
    game = server.new_game("white", "easy")
    gid = game["game_id"]
    rendered = server.get_board(gid)["board"]
    assert "a b c d e f g h" in rendered
    assert rendered.count("\n") == 10
