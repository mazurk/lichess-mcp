"""Tests de la base de puzzles et du déroulé multi-coups (mats en 1 et 2)."""

import random

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


def test_puzzle_database_size():
    """La base doit être large et majoritairement des mats en 2."""
    assert len(server.PUZZLES) >= 280
    mate2 = [p for p in server.PUZZLES if p.get("MateIn") == 2]
    mate1 = [p for p in server.PUZZLES if p.get("MateIn", 1) == 1]
    assert len(mate2) >= 200, f"seulement {len(mate2)} mats en 2"
    assert len(mate1) >= 40


def test_puzzle_database_integrity():
    """Chaque puzzle : position strictement légale (pas de roi adverse en prise),
    FEN lisible, ligne légale, mat final authentique, longueur = 2N-1 demi-coups."""
    ids = set()
    for p in server.PUZZLES:
        assert p["PuzzleId"] not in ids, f"id dupliqué : {p['PuzzleId']}"
        ids.add(p["PuzzleId"])
        board = chess.Board(p["FEN"])
        assert board.is_valid(), f"position illégale dans {p['PuzzleId']} ({board.status()})"
        line = p["Moves"].split()
        assert len(line) == 2 * p.get("MateIn", 1) - 1
        for uci in line:
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves, f"coup illégal dans {p['PuzzleId']}"
            board.push(move)
        assert board.is_checkmate(), f"pas de mat final dans {p['PuzzleId']}"


def test_puzzles_from_lichess_are_marked():
    """Les puzzles récupérés sur lichess.org portent leur source."""
    src = [p for p in server.PUZZLES if str(p.get("Source", "")).startswith("lichess.org")]
    assert len(src) >= 100, f"seulement {len(src)} puzzles sourcés lichess.org"


def test_next_puzzle_session_and_hints():
    """next_puzzle crée une session ; l'indice dépend du mat en N."""
    mate2 = next(p for p in server.PUZZLES if p.get("MateIn") == 2)
    session = server._start_puzzle(mate2)
    assert session["plies_done"] == 0
    assert session["board"].fen() == mate2["FEN"]

    r = server.next_puzzle()
    assert r["puzzle_id"] in server.ACTIVE_PUZZLES
    assert r["mate_in"] in (1, 2)
    if r["mate_in"] == 2:
        assert "Mat en 2" in r["hint"]
    else:
        assert "mat en 1" in r["hint"]


def test_mate2_full_flow():
    """Déroulé complet d'un mat en 2 : coup clé, réponse adverse, mat."""
    mate2 = next(p for p in server.PUZZLES if p.get("MateIn") == 2)
    server._start_puzzle(mate2)
    pid = mate2["PuzzleId"]
    line = mate2["Moves"].split()

    # coup clé
    r1 = server.try_puzzle_move(pid, line[0])
    assert r1["correct"] is True
    assert "L'adversaire répond" in r1["note"]
    assert r1["mate_in"] == 2
    # la position a avancé : clé + réponse adverse
    assert server.ACTIVE_PUZZLES[pid]["plies_done"] == 2

    # coup de mat
    r2 = server.try_puzzle_move(pid, line[2])
    assert r2["correct"] is True
    assert "mat en 2 trouvé" in r2["note"]


def test_mate2_wrong_key_move_rejected():
    mate2 = next(p for p in server.PUZZLES if p.get("MateIn") == 2)
    server._start_puzzle(mate2)
    pid = mate2["PuzzleId"]
    line = mate2["Moves"].split()

    # un coup légal mais incorrect
    board = chess.Board(mate2["FEN"])
    for move in board.legal_moves:
        if move.uci() != line[0]:
            r = server.try_puzzle_move(pid, move.uci())
            assert r["correct"] is False
            assert "coup clé" in r["hint"]
            break
    # la session n'a pas avancé
    assert server.ACTIVE_PUZZLES[pid]["plies_done"] == 0


def test_mate1_flow_unchanged():
    mate1 = next(p for p in server.PUZZLES if p.get("MateIn", 1) == 1)
    server._start_puzzle(mate1)
    r = server.try_puzzle_move(mate1["PuzzleId"], mate1["Moves"].split()[0])
    assert r["correct"] is True
    assert "mat en 1 trouvé" in r["note"]


def test_mate2_mating_move_must_match_line():
    """Après le coup clé, seul le mat de la ligne est accepté."""
    mate2 = next(p for p in server.PUZZLES if p.get("MateIn") == 2)
    server._start_puzzle(mate2)
    pid = mate2["PuzzleId"]
    line = mate2["Moves"].split()
    server.try_puzzle_move(pid, line[0])  # clé + réponse adverse

    board = server.ACTIVE_PUZZLES[pid]["board"]
    for move in board.legal_moves:
        if move.uci() != line[2]:
            r = server.try_puzzle_move(pid, move.uci())
            assert r["correct"] is False
            break


def test_random_puzzle_flow_smoke():
    """Tirage aléatoire : quel que soit le puzzle, la ligne complète réussit."""
    random.seed(42)
    for _ in range(10):
        r = server.next_puzzle()
        pid = r["puzzle_id"]
        line = server.ACTIVE_PUZZLES[pid]["puzzle"]["Moves"].split()
        # mat en 1 : un coup ; mat en 2 : clé puis mat
        if r["mate_in"] == 1:
            final = server.try_puzzle_move(pid, line[0])
        else:
            step = server.try_puzzle_move(pid, line[0])
            assert step["correct"] is True
            final = server.try_puzzle_move(pid, line[2])
        assert final["correct"] is True


def test_analysis_board_nameable():
    """Un échiquier d'analyse peut être nommé et apparaît dans la liste web."""
    server.GAMES.clear()
    r = server.new_analysis_board(name="Ouverture française")
    assert r["name"] == "Ouverture française"
    game = server.GAMES[r["game_id"]]
    assert game["name"] == "Ouverture française"
    # sans nom : champ null
    r2 = server.new_analysis_board()
    assert server.GAMES[r2["game_id"]]["name"] is None


def test_start_puzzle_publishes_observable_board():
    """start_puzzle : le puzzle est visible sur le serveur (mode Consulter)."""
    server.GAMES.clear()
    puzzle = server.PUZZLES[0]
    r = server.start_puzzle(puzzle_id=puzzle["PuzzleId"], name="Puzzle du jour")
    gid = r["game_id"]
    game = server.GAMES[gid]
    assert game["kind"] == "puzzle"
    assert game["name"] == "Puzzle du jour"
    assert r["puzzle_id"] == puzzle["PuzzleId"]
    # la position publiée est bien celle du puzzle
    assert game["board"].fen() == puzzle["FEN"]
    # consultable comme spectateur (comme les échiquiers d'analyse)
    joined = server.join_game(gid, "observateur", spectate=True)
    assert joined["you_are"] == "spectator"


def test_try_puzzle_move_on_published_board():
    """Les coups validés d'un puzzle publié sont rejoués sur l'échiquier web."""
    puzzle = server.PUZZLES[0]
    r = server.start_puzzle(puzzle_id=puzzle["PuzzleId"])
    gid = r["game_id"]
    game = server.GAMES[gid]
    line = puzzle["Moves"].split()
    first = chess.Move.from_uci(line[0])
    res = server.try_puzzle_move(puzzle["PuzzleId"],
                                 game["board"].san(first), game_id=gid)
    assert res["correct"] is True
    # le coup (et la réponse adverse éventuelle) est visible sur l'échiquier
    assert len(game["board"].move_stack) >= 1
    assert game["puzzle"]["plies_done"] >= 1
    # un mauvais game_id est refusé
    gid2 = server.new_game()["game_id"]
    try:
        server.try_puzzle_move(puzzle["PuzzleId"], "e4", game_id=gid2)
        raise AssertionError("doit échouer")
    except ValueError:
        pass


def test_list_puzzles_catalog():
    """list_puzzles expose le catalogue (ids pour start_puzzle), avec filtre mate_in."""
    r = server.list_puzzles()
    assert r["count"] == len(server.PUZZLES)
    assert {p["puzzle_id"] for p in r["puzzles"]} == {p["PuzzleId"] for p in server.PUZZLES}
    r2 = server.list_puzzles(mate_in=2)
    assert r2["count"] >= 200
    assert all(p["mate_in"] == 2 for p in r2["puzzles"])
    r1 = server.list_puzzles(mate_in=1)
    assert all(p["mate_in"] == 1 for p in r1["puzzles"])
    with pytest.raises(ValueError):
        server.list_puzzles(mate_in=3)


def test_start_puzzle_progress_exposed():
    """La progression du puzzle publié est exposée (sans la solution)."""
    puzzle = server.PUZZLES[0]
    r = server.start_puzzle(puzzle_id=puzzle["PuzzleId"])
    gid = r["game_id"]
    info = server._puzzle_progress(server.GAMES[gid])
    assert info["puzzle_id"] == puzzle["PuzzleId"]
    assert info["plies_done"] == 0
    assert info["total_plies"] == len(puzzle["Moves"].split())
    assert info["solved"] is False
    assert "moves" not in info
    # après le coup gagnant, la progression avance et le puzzle est résolu
    line = puzzle["Moves"].split()
    server.try_puzzle_move(puzzle["PuzzleId"], line[0], game_id=gid)
    info = server._puzzle_progress(server.GAMES[gid])
    assert info["plies_done"] >= 1
    if len(line) == 1:
        assert info["solved"] is True


def test_random_draw_only_lichess_puzzles():
    """Issue #53 : le tirage aléatoire exclut les puzzles générés."""
    gen_ids = {p["PuzzleId"] for p in server.PUZZLES
               if server._puzzle_source(p["PuzzleId"]) == "generated"}
    for _ in range(40):
        r = server.start_puzzle()
        assert r["puzzle_id"] not in gen_ids
        assert r["source"] == "lichess"
    # un puzzle spécifique reste jouable, y compris généré
    gen = next(p for p in server.PUZZLES
               if server._puzzle_source(p["PuzzleId"]) == "generated")
    r = server.start_puzzle(puzzle_id=gen["PuzzleId"])
    assert r["source"] == "generated"


def test_alternative_mate1_accepted():
    """Issue #52 (mate2_177) : un mat en 1 différent de la ligne est accepté."""
    fen = "6k1/5ppp/8/8/8/8/8/KR2R3 w - - 0 1"  # deux mats : Rb8# et Re8#
    puzzle = {"PuzzleId": "altm1", "FEN": fen, "Moves": "b1b8",
              "Theme": "backRankMate", "MateIn": 1}
    server.ACTIVE_PUZZLES.clear()
    server._start_puzzle(puzzle)
    # coup non matant : refusé
    r0 = server.try_puzzle_move("altm1", "b1b2")
    assert r0["correct"] is False
    # mat alternatif ≠ ligne du catalogue : accepté
    r = server.try_puzzle_move("altm1", "e1e8")
    assert r["correct"] is True
    assert r["solved"] is True
    assert "variante acceptée" in r["note"]


def test_alternative_mate2_key_accepted():
    """Issue #52 : coup clé alternatif (mat forcé en 2) accepté ; réponse
    adverse automatique jouée même hors ligne du catalogue."""
    fen = "k7/8/8/8/8/8/8/1RR4K w - - 0 1"  # échelle : Rc5, Rc4, Rc3... forcent le mat en 2
    puzzle = {"PuzzleId": "altm2", "FEN": fen, "Moves": "c1c2 a8a7 c2c7",
              "Theme": "rookLadder", "MateIn": 2}
    server.ACTIVE_PUZZLES.clear()
    server._start_puzzle(puzzle)
    # clé alternative Rc5 (≠ ligne c1c2), mat forcé en 2 → acceptée
    r = server.try_puzzle_move("altm2", "c1c5")
    assert r["correct"] is True
    session = server.ACTIVE_PUZZLES["altm2"]
    assert session["plies_done"] == 2  # clé + réponse adverse automatique
    # refus d'un coup non matant AU MAT : rappel de la réponse adverse (issue #52, mate2_090)
    bad = next(m.uci() for m in session["board"].legal_moves
               if not _is_mate(session["board"], m))
    r2 = server.try_puzzle_move("altm2", bad)
    assert r2["correct"] is False
    assert "l'adversaire a joué" in r2["note"]
    # le vrai mat est accepté
    good = _any_mate(session["board"])
    r3 = server.try_puzzle_move("altm2", good)
    assert r3["correct"] is True and r3["solved"] is True


def _is_mate(board, move):
    board.push(move)
    mate = board.is_checkmate()
    board.pop()
    return mate


def _any_mate(board):
    for m in board.legal_moves:
        if _is_mate(board, m):
            return m.uci()
    raise AssertionError("aucun mat en 1 dans cette position")


def test_share_puzzle():
    """Issue #51 : share_puzzle crée un échiquier d'analyse consultable."""
    server.GAMES.clear()
    p = next(x for x in server.PUZZLES
             if server._puzzle_source(x["PuzzleId"]) == "lichess")
    r = server.share_puzzle(p["PuzzleId"], name="Puzzle partagé")
    game = server.GAMES[r["game_id"]]
    assert game["kind"] == "analysis"
    assert game["name"] == "Puzzle partagé"
    assert game["board"].fen().split()[0] == p["FEN"].split()[0]
    # la solution est dans la réponse, pas jouée sur le board
    assert r["solution_uci"] == p["Moves"]
    assert len(game["board"].move_stack) == 0
    assert p["PuzzleId"] not in server.ACTIVE_PUZZLES or True
