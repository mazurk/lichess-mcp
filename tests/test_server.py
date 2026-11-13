"""Tests fonctionnels du serveur MCP d'échecs hors ligne (interface neutre)."""

import pytest

import server


@pytest.fixture(autouse=True)
def _clean_state():
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()


def test_new_game_defaults_free():
    r = server.new_game()
    assert r["white_name"] == "Blancs"
    assert r["black_name"] == "Noirs"
    assert r["engine_color"] is None
    assert r["turn"] == "white"
    assert "game_id" in r and "fen" in r


def test_new_game_with_names():
    r = server.new_game(white="Nicolas", black="Agent IA")
    assert r["white_name"] == "Nicolas"
    assert r["black_name"] == "Agent IA"


def test_new_game_engine_black():
    r = server.new_game(white="Nicolas", black="Moteur", engine_color="black")
    assert r["engine_color"] == "black"
    assert r["turn"] == "white"


def test_new_game_engine_white_plays_first():
    r = server.new_game(white="Moteur", black="Nicolas", engine_color="white")
    assert r["engine_move"] is not None
    assert r["turn"] == "black"
    assert r["white_name"] == "Moteur"


def test_new_game_invalid_params():
    with pytest.raises(ValueError):
        server.new_game(engine_color="rouge")
    with pytest.raises(ValueError):
        server.new_game(level="impossible")


def test_play_move_default_color_is_turn():
    gid = server.new_game(white="A", black="B")["game_id"]
    r1 = server.play_move(gid, "e4")
    assert r1["move_played"] == "e4"
    assert r1["color_played"] == "white"
    r2 = server.play_move(gid, "e5")
    assert r2["move_played"] == "e5"
    assert r2["color_played"] == "black"


def test_play_move_explicit_color():
    gid = server.new_game(white="A", black="B")["game_id"]
    assert server.play_move(gid, "e4", color="white")["move_played"] == "e4"
    assert server.play_move(gid, "g8f6", color="black")["move_played"] == "Nf6"


def test_play_move_wrong_turn_rejected():
    gid = server.new_game(white="A", black="B")["game_id"]
    with pytest.raises(ValueError):
        server.play_move(gid, "e5", color="black")
    server.play_move(gid, "e4", color="white")
    with pytest.raises(ValueError):
        server.play_move(gid, "d4", color="white")


def test_play_move_engine_side_rejected():
    gid = server.new_game(white="A", black="Moteur", engine_color="black")["game_id"]
    with pytest.raises(ValueError, match="moteur"):
        server.play_move(gid, "e5", color="black")


def test_play_move_engine_auto_reply():
    gid = server.new_game(white="A", black="Moteur", engine_color="black")["game_id"]
    r = server.play_move(gid, "e4", color="white")
    assert r["engine_move"] is not None
    assert r["turn"] == "white"
    assert len(server.GAMES[gid]["board"].move_stack) == 2


def test_play_move_illegal_rejected():
    gid = server.new_game(white="A", black="B")["game_id"]
    with pytest.raises(ValueError):
        server.play_move(gid, "e5", color="white")
    with pytest.raises(ValueError):
        server.play_move(gid, "zz9", color="white")


def test_fools_mate_detected():
    gid = server.new_game(white="A", black="B")["game_id"]
    for uci, color in (("f2f3", "white"), ("e7e5", "black"), ("g2g4", "white"), ("d8h4", "black")):
        r = server.play_move(gid, uci, color=color)
    assert r.get("game_over")
    assert "B" in r["game_over"]


def test_get_board_and_legal_moves():
    gid = server.new_game(white="A", black="B")["game_id"]
    board = server.get_board(gid)
    assert board["turn"] == "white"
    assert board["white_name"] == "A"
    assert board["in_check"] is False
    moves = server.legal_moves(gid)["moves"]
    assert len(moves) == 20
    assert "e4" in moves


def test_undo_free_pops_one():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    r = server.undo(gid)
    assert len(server.GAMES[gid]["board"].move_stack) == 0
    assert r["turn"] == "white"


def test_undo_engine_pops_two():
    gid = server.new_game(white="A", black="Moteur", engine_color="black")["game_id"]
    server.play_move(gid, "e4", color="white")
    server.undo(gid)
    assert len(server.GAMES[gid]["board"].move_stack) == 0


def test_resign():
    gid = server.new_game(white="A", black="B")["game_id"]
    r = server.resign(gid, color="black")
    assert "B" in r["game_over"]
    assert "A" in r["game_over"]
    with pytest.raises(ValueError):
        server.play_move(gid, "e4", color="white")


def test_game_status():
    gid = server.new_game(white="A", black="B")["game_id"]
    status = server.game_status(gid)
    assert status["over"] is False
    assert status["white_name"] == "A"
    assert status["black_name"] == "B"
    assert status["engine_color"] is None


def test_unknown_game_rejected():
    with pytest.raises(ValueError):
        server.get_board("inexistant")
    with pytest.raises(ValueError):
        server.play_move("inexistant", "e4")


def test_anyone_can_play_any_side_by_id():
    gid = server.new_game(white="Nicolas", black="Agent")["game_id"]
    assert server.play_move(gid, "e4", color="white")["move_played"] == "e4"
    assert server.play_move(gid, "e6", color="black")["move_played"] == "e6"
    assert server.play_move(gid, "d4", color="white")["move_played"] == "d4"
