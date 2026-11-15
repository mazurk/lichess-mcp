"""Tests des échiquiers d'analyse (dry-run), wait_for_turn et analyse Stockfish."""

import shutil

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


@needs_stockfish
def test_analysis_provides_pv():
    """La PV Stockfish (ligne principale) est exposée pour chaque coup."""
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    server.play_move(gid, "d5", color="black")
    r = server.analyze_game(gid, depth=10)
    for e in r["evals"]:
        assert "pv_san" in e
        assert isinstance(e["pv_san"], list)
        assert len(e["pv_san"]) >= 2, "la PV doit contenir une continuation"
    # la PV du premier coup démarre par un coup légal SAN connu
    assert r["evals"][0]["pv_san"][0] in ("e4", "d4", "Nf3", "c4")


def test_get_board_navigation():
    """L'agent peut rejouer les positions passées comme l'interface web."""
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    server.play_move(gid, "e5", color="black")
    server.play_move(gid, "Nf3", color="white")

    # position initiale
    r0 = server.get_board(gid, ply=0)
    assert r0["ply"] == 0 and r0["total_moves"] == 3
    assert "rnbqkbnr/pppppppp" in r0["fen"]
    assert "Prochain coup joué : e4" in r0["note"]

    # après le 2e coup (e4 e5) : trait aux blancs, pas de cavalier en f3
    r2 = server.get_board(gid, ply=2)
    assert r2["ply"] == 2
    assert r2["turn"] == "white"  # trait de la position rejouée
    assert "5N2" not in r2["fen"]  # pas de cavalier blanc en f3
    assert "rnbqkbnr" in r2["fen"]  # pièces noires intactes

    # défaut = position actuelle ; bornes refusées
    r_now = server.get_board(gid)
    assert r_now["ply"] == 3 and "note" not in r_now
    with pytest.raises(ValueError, match="entre 0 et 3"):
        server.get_board(gid, ply=4)
    with pytest.raises(ValueError, match="entre 0 et 3"):
        server.get_board(gid, ply=-1)


def test_analysis_board_get_board_keeps_fen():
    """Régression : get_board doit montrer le FEN de départ d'un échiquier
    d'analyse (bug introduit par la navigation ply, rejouant depuis la
    position initiale)."""
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
    gid = server.new_analysis_board(fen)["game_id"]
    b = server.get_board(gid)
    assert b["fen"] == fen
    assert b["ply"] == 0 and b["total_moves"] == 0

    # navigation après des coups joués sur l'échiquier d'analyse
    server.play_move(gid, "O-O", color="white")
    b0 = server.get_board(gid, ply=0)
    assert b0["fen"] == fen, "ply=0 doit revenir au FEN de départ"
    b1 = server.get_board(gid)
    assert b1["fen"] != fen and b1["ply"] == 1


@needs_stockfish
def test_analyze_game_on_analysis_board_with_fen():
    """Régression : l'analyse d'un échiquier d'analyse créé depuis un FEN
    échouait (coups rejoués depuis la position initiale -> protocole
    moteur cassé, timeout côté client)."""
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
    gid = server.new_analysis_board(fen)["game_id"]
    server.play_move(gid, "O-O", color="white")
    server.play_move(gid, "a6", color="black")
    r = server.analyze_game(gid, depth=10)
    assert r["published"] is True
    assert len(r["evals"]) == 2
    assert r["evals"][0]["san"] == "O-O"
    # le PGN embarque le FEN de départ
    placement = fen.split(" ", 1)[0]
    assert placement in r["pgn"]
    # l'analyse publiée est visible sur l'interface web
    assert server.GAMES[gid]["analysis"]["depth"] == 10


@needs_stockfish
def test_analyze_pgn_localizes_illegal_move():
    """Un coup illégal dans un PGN doit être localisé précisément
    (numéro de coup, camp, position) au lieu d'un rejet générique."""
    bad_pgn = """[Event "Test"]

1. e4 e5 2. Ke2 Ke7 3. O-O 1-0 *"""
    with pytest.raises(ValueError, match="coup 3") as exc_info:
        server.analyze_pgn(bad_pgn, depth=8)
    msg = str(exc_info.value)
    assert "Blancs à jouer" in msg
    assert "4 coups légaux" in msg
    assert "O-O" in msg
    assert "rnbq1bnr" in msg  # FEN au moment du rejet


@needs_stockfish
def test_analyze_pgn_with_fen_header():
    """Un PGN avec en-tête FEN doit être analysé depuis cette position
    (et non depuis la position initiale)."""
    fen_pgn = """[Event "Test"]
[SetUp "1"]
[FEN "6k1/8/6K1/8/8/8/8/R7 w - - 0 1"]

1. Ra8# 1-0 *"""
    r = server.analyze_pgn(fen_pgn, depth=8)
    assert r["summary"]["moves"] == 1
    assert r["evals"][0]["san"] == "Ra8#"
    assert r["evals"][0]["eval_white"] >= 90000  # mat
    assert "6k1" in r["pgn"]  # le FEN de départ est dans le PGN annoté


def test_list_games_tool():
    """L'agent peut lister les parties du serveur (id, statut, camp libres)."""
    server.GAMES.clear()
    gid1 = server.new_game(white="A", black="B")["game_id"]
    gid2 = server.new_game(white="C", engine_color="black", clock="10+0")["game_id"]
    result = server.list_games()
    assert result["count"] == 2
    ids = [g["game_id"] for g in result["games"]]
    assert ids == sorted([gid1, gid2])
    g2 = next(g for g in result["games"] if g["game_id"] == gid2)
    assert g2["status"] == "en cours"
    assert g2["kind"] == "game"
    assert g2["engine_color"] == "black"
    assert g2["plies"] == 0
    assert g2["white_seat"] is None
    assert g2["analysed"] is False
    # une partie terminée est signalée
    server.resign(gid1, "white")
    g1 = next(g for g in server.list_games()["games"] if g["game_id"] == gid1)
    assert g1["status"] == "terminée"
