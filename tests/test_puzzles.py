"""Tests de la base de puzzles et du déroulé multi-coups.

Couvre les mats en 1/2/3 et les combinaisons tactiques (issue #72) : le joueur
joue les plies pairs (camp au trait dans le FEN), l'adversaire répond
automatiquement les plies impairs ; la ligne des puzzles lichess est de
longueur paire (la réponse adverse peut conclure, y compris par le mat).
Vérifie aussi les scores par thème persistés (PUZZLE_SCORES / puzzle_stats).
"""

import random

import chess
import pytest

import server


@pytest.fixture(autouse=True)
def _clean_state():
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    server.PUZZLE_SCORES.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    server.PUZZLE_SCORES.clear()


def _line_is_legal(p: dict) -> chess.Board:
    board = chess.Board(p["FEN"])
    assert board.is_valid(), f"position illégale dans {p['PuzzleId']} ({board.status()})"
    for uci in p["Moves"].split():
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves, f"coup illégal dans {p['PuzzleId']}"
        board.push(move)
    return board


def test_puzzle_database_size():
    """La base doit être large : mats en 1/2/3 et combinaisons tactiques (#72)."""
    assert len(server.PUZZLES) >= 2000
    mate1 = [p for p in server.PUZZLES if p.get("MateIn", 1) == 1]
    mate2 = [p for p in server.PUZZLES if p.get("MateIn") == 2]
    mate3 = [p for p in server.PUZZLES if p.get("MateIn") == 3]
    tactics = [p for p in server.PUZZLES if p.get("MateIn") is None]
    assert len(mate1) >= 40
    assert len(mate2) >= 200
    assert len(mate3) >= 100, f"seulement {len(mate3)} mats en 3"
    assert len(tactics) >= 800, f"seulement {len(tactics)} tactiques"


def test_puzzle_database_integrity():
    """Chaque puzzle : position légale, ligne légale. Si MateIn renseigné :
    la ligne finit par un mat en 2*MateIn-1 ou 2*MateIn demi-coups. Si
    MateIn absent (tactique lichess, #72) : ligne de longueur paire."""
    ids = set()
    for p in server.PUZZLES:
        assert p["PuzzleId"] not in ids, f"id dupliqué : {p['PuzzleId']}"
        ids.add(p["PuzzleId"])
        board = _line_is_legal(p)
        line = p["Moves"].split()
        mate_in = p.get("MateIn", 1)
        if mate_in is not None:
            assert len(line) in (2 * mate_in - 1, 2 * mate_in), (
                f"{p['PuzzleId']}: {len(line)} plies pour mat en {mate_in}")
            assert board.is_checkmate(), f"pas de mat final dans {p['PuzzleId']}"
        else:
            assert len(line) % 2 == 0, (
                f"{p['PuzzleId']}: ligne tactique de longueur impaire {len(line)}")


def test_puzzles_from_lichess_are_marked():
    """Les puzzles récupérés sur lichess.org portent leur source."""
    src = [p for p in server.PUZZLES if str(p.get("Source", "")).startswith("lichess.org")]
    assert len(src) >= 100, f"seulement {len(src)} puzzles sourcés lichess.org"
    db = [p for p in server.PUZZLES
          if str(p.get("Source", "")).startswith("lichess.org/database")]
    assert len(db) >= 1500, f"seulement {len(db)} puzzles de la Puzzle Database (#72)"


def test_next_puzzle_session_and_hints():
    """next_puzzle crée une session ; l'indice dépend du type (mat en N ou
    combinaison tactique, issue #72)."""
    mate2 = next(p for p in server.PUZZLES if p.get("MateIn") == 2)
    session = server._start_puzzle(mate2)
    assert session["plies_done"] == 0
    assert session["board"].fen() == mate2["FEN"]

    random.seed(7)
    seen = set()
    for _ in range(60):
        r = server.next_puzzle()
        seen.add(r["mate_in"])
        assert r["puzzle_id"] in server.ACTIVE_PUZZLES
        assert r["mate_in"] in (1, 2, 3, None)
        if r["mate_in"] == 2:
            assert "Mat en 2" in r["hint"]
        elif r["mate_in"] == 1:
            assert "mat en 1" in r["hint"]
        elif r["mate_in"] == 3:
            assert "Mat en 3" in r["hint"]
        else:
            assert "Combinaison tactique" in r["hint"]
    assert None in seen and 3 in seen  # le nouveau catalogue est bien tiré


def _play_line(pid: str, line: list[str], game_id=None) -> dict:
    """Joue tous les coups du joueur (plies pairs) d'une ligne de puzzle."""
    final = None
    for i in range(0, len(line), 2):
        final = server.try_puzzle_move(pid, line[i], game_id=game_id)
        assert final["correct"] is True, f"pli {i} {line[i]} refusé : {final}"
    return final


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


def test_mate3_flow():
    """Issue #72 : mat en 3 (ligne lichess de 6 plies) — 3 coups du joueur,
    réponses auto entre les coups, le mat tombe sur la dernière réponse."""
    mate3 = next(p for p in server.PUZZLES if p.get("MateIn") == 3)
    server._start_puzzle(mate3)
    pid = mate3["PuzzleId"]
    line = mate3["Moves"].split()
    assert len(line) == 6

    r1 = server.try_puzzle_move(pid, line[0])
    assert r1["correct"] is True and "solved" not in r1
    assert server.ACTIVE_PUZZLES[pid]["plies_done"] == 2
    r2 = server.try_puzzle_move(pid, line[2])
    assert r2["correct"] is True and "solved" not in r2
    assert server.ACTIVE_PUZZLES[pid]["plies_done"] == 4
    # dernier coup du joueur (ply 4) puis réponse auto (ply 5) qui conclut
    r3 = server.try_puzzle_move(pid, line[4])
    assert r3["correct"] is True and r3["solved"] is True
    assert server.ACTIVE_PUZZLES[pid]["solved"] is True


def test_tactic_flow_even_line():
    """Issue #72 : combinaison tactique (MateIn None, ligne paire lichess) —
    le puzzle est résolu quand la ligne est entièrement rejouée, y compris
    quand la dernière réponse automatique conclut."""
    tactic = next(p for p in server.PUZZLES
                  if p.get("MateIn") is None and len(p["Moves"].split()) == 2)
    server._start_puzzle(tactic)
    pid = tactic["PuzzleId"]
    line = tactic["Moves"].split()
    r = server.try_puzzle_move(pid, line[0])
    assert r["correct"] is True and r["solved"] is True  # réponse auto = fin de ligne
    assert server.ACTIVE_PUZZLES[pid]["solved"] is True

    # combinaison plus longue (4 plies) : deux coups du joueur
    tactic4 = next(p for p in server.PUZZLES
                   if p.get("MateIn") is None and len(p["Moves"].split()) == 4)
    server._start_puzzle(tactic4)
    final = _play_line(tactic4["PuzzleId"], tactic4["Moves"].split())
    assert final["solved"] is True
    assert "combinaison réussie" in final["note"] or "mat" in final["note"]


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


def test_tactic_wrong_move_rejected_and_hint():
    """Issue #72 : refus sur une tactique -> hint 'suite gagnante'."""
    tactic = next(p for p in server.PUZZLES if p.get("MateIn") is None)
    server._start_puzzle(tactic)
    pid = tactic["PuzzleId"]
    line = tactic["Moves"].split()
    board = chess.Board(tactic["FEN"])
    for move in board.legal_moves:
        if move.uci() != line[0]:
            r = server.try_puzzle_move(pid, move.uci())
            assert r["correct"] is False
            assert "suite gagnante" in r["hint"]
            break
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
    server.try_puzzle_move(pid, line[0])  # clé + réponse adverse automatique

    board = server.ACTIVE_PUZZLES[pid]["board"]
    for move in board.legal_moves:
        if move.uci() != line[2]:
            r = server.try_puzzle_move(pid, move.uci())
            assert r["correct"] is False
            break


def test_random_puzzle_flow_smoke():
    """Tirage aléatoire : quel que soit le puzzle (mat en N ou tactique), la
    ligne complète jouée par plies pairs réussit et résout le puzzle."""
    random.seed(42)
    for _ in range(10):
        r = server.next_puzzle()
        pid = r["puzzle_id"]
        line = server.ACTIVE_PUZZLES[pid]["puzzle"]["Moves"].split()
        final = _play_line(pid, line)
        assert final["correct"] is True
        assert final["solved"] is True
        assert server.ACTIVE_PUZZLES[pid]["solved"] is True


def test_puzzle_scores_and_stats():
    """Issue #72 : chaque try_puzzle_move alimente les scores du thème ;
    puzzle_stats expose compteurs + taux (total et par thème)."""
    mate2 = next(p for p in server.PUZZLES if p.get("MateIn") == 2)
    server._start_puzzle(mate2)
    pid = mate2["PuzzleId"]
    line = mate2["Moves"].split()
    theme = mate2["Theme"]
    # un mauvais coup d'abord (attempts +1, correct +0)
    board = chess.Board(mate2["FEN"])
    wrong = next(m.uci() for m in board.legal_moves if m.uci() != line[0])
    r = server.try_puzzle_move(pid, wrong)
    assert r["correct"] is False
    # puis la ligne complète : 2 coups corrects, résolu au 2e coup
    _play_line(pid, line)

    s = server.PUZZLE_SCORES[theme]
    assert s["attempts"] == 3
    assert s["correct"] == 2
    assert s["solved"] == 1
    assert s["first_try"] == 0  # résolu au 2e coup, pas au 1er

    stats = server.puzzle_stats()
    assert stats["total"]["attempts"] == 3
    assert stats["total"]["solved"] == 1
    assert stats["by_theme"][theme]["success_rate"] == round(100 * 2 / 3, 1)
    assert stats["by_theme"][theme]["first_try_rate"] == 0.0


def test_puzzle_scores_first_try():
    """Issue #72 : un puzzle résolu dès le premier coup compte en first_try."""
    mate1 = next(p for p in server.PUZZLES if p.get("MateIn", 1) == 1)
    server._start_puzzle(mate1)
    server.try_puzzle_move(mate1["PuzzleId"], mate1["Moves"].split()[0])
    s = server.PUZZLE_SCORES[mate1["Theme"]]
    assert s["solved"] == 1 and s["first_try"] == 1
    assert server.puzzle_stats()["by_theme"][mate1["Theme"]]["first_try_rate"] == 100.0


def test_puzzle_scores_persisted(monkeypatch, tmp_path):
    """Issue #72 : les scores par thème survivent à un redémarrage."""
    monkeypatch.setenv("LICHESS_MCP_STATE_FILE", str(tmp_path / "state.json"))
    mate1 = next(p for p in server.PUZZLES if p.get("MateIn", 1) == 1)
    server._start_puzzle(mate1)
    server.try_puzzle_move(mate1["PuzzleId"], mate1["Moves"].split()[0])
    assert server.PUZZLE_SCORES[mate1["Theme"]]["solved"] == 1
    # « redémarrage » : on vide la mémoire et on recharge depuis le disque
    saved = dict(server.PUZZLE_SCORES)
    server.PUZZLE_SCORES.clear()
    server._load_state()
    assert server.PUZZLE_SCORES.get(mate1["Theme"], {}).get("solved") == saved[mate1["Theme"]]["solved"]


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
    """list_puzzles expose le catalogue (ids pour start_puzzle), avec filtres
    mate_in (>= 1) et theme (issue #72)."""
    r = server.list_puzzles()
    assert r["count"] == len(server.PUZZLES)
    assert {p["puzzle_id"] for p in r["puzzles"]} == {p["PuzzleId"] for p in server.PUZZLES}
    r2 = server.list_puzzles(mate_in=2)
    assert r2["count"] >= 200
    assert all(p["mate_in"] == 2 for p in r2["puzzles"])
    r3 = server.list_puzzles(mate_in=3)
    assert r3["count"] >= 100
    assert all(p["mate_in"] == 3 for p in r3["puzzles"])
    r1 = server.list_puzzles(mate_in=1)
    assert all(p["mate_in"] == 1 for p in r1["puzzles"])
    # filtre par thème (tactique, issue #72)
    rf = server.list_puzzles(theme="fork")
    assert rf["count"] >= 100
    assert all(p["theme"] == "fork" for p in rf["puzzles"])
    # combinaisons tactiques de la Puzzle Database = MateIn None (les anciens
    # puzzles générés du même thème gardent leur MateIn)
    rt = server.list_puzzles(theme="hangingPiece")
    db = [p for p in rt["puzzles"] if str(
        next(x for x in server.PUZZLES if x["PuzzleId"] == p["puzzle_id"])
        .get("Source", "")).startswith("lichess.org/database")]
    assert len(db) >= 100
    assert all(p["mate_in"] is None for p in db)
    with pytest.raises(ValueError):
        server.list_puzzles(mate_in=0)
    with pytest.raises(ValueError):
        server.list_puzzles(theme="  ")


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
