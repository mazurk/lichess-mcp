"""Tests de la persistance : les parties (et sessions de puzzle, alertes
triche) survivent à un redémarrage du serveur.

Un « redémarrage » est simulé par : vidage de GAMES/ACTIVE_PUZZLES/CHEAT_ALERTS
puis `server._load_state()` (rechargement depuis le fichier d'état).
"""

import json

import pytest

import server


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Chaque test a son propre fichier d'état (jamais le data/ du dépôt)."""
    monkeypatch.setenv("LICHESS_MCP_STATE_FILE", str(tmp_path / "server_state.json"))
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    server.CHEAT_ALERTS.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    server.CHEAT_ALERTS.clear()


def _restart():
    """Simule un redémarrage du serveur : mémoire vidée, état rechargé du disque."""
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    server.CHEAT_ALERTS.clear()
    server._load_state()


def _state_file(tmp_path):
    return tmp_path / "server_state.json"


def test_game_persisted_and_reloaded(tmp_path):
    gid = server.new_game(white="Nicolas", black="Agent IA", clock="10+0")["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    server.join_game(gid, "Agent IA", color="black")
    server.play_move(gid, "e4", color="white", player="Nicolas")
    server.play_move(gid, "e5", color="black", player="Agent IA")
    fen_before = server.GAMES[gid]["board"].fen()
    assert _state_file(tmp_path).is_file()  # écrit à chaque mutation

    _restart()

    assert gid in server.GAMES
    game = server.GAMES[gid]
    assert game["board"].fen() == fen_before
    assert server.get_moves(gid)["moves"] == ["e4", "e5"]
    assert game["white_name"] == "Nicolas" and game["black_name"] == "Agent IA"
    assert game["white_seat"] == "Nicolas" and game["black_seat"] == "Agent IA"
    assert game["clock_started"] is True
    # horloge des noirs en route (trait aux noirs) : ~10 min, un peu entamée
    assert 599_000 < game["clock_ms"]["black"] <= 600_000
    assert server.game_status(gid)["over"] is False


def test_analysis_board_custom_fen_reloaded(tmp_path):
    fen = "7k/2N5/5N2/3q4/8/8/8/4K3 w - - 0 1"
    gid = server.new_analysis_board(fen)["game_id"]
    server.play_move(gid, "Ncd5", color="white")
    fen_before = server.GAMES[gid]["board"].fen()

    _restart()

    assert gid in server.GAMES
    assert server.GAMES[gid]["kind"] == "analysis"
    assert server.GAMES[gid]["board"].fen() == fen_before
    # les SAN sont recalculés depuis la bonne position racine
    assert server.get_moves(gid)["moves"] == ["Ncxd5"]


def test_resigned_game_stays_over_after_restart(tmp_path):
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    server.resign(gid, color="black")

    _restart()

    status = server.game_status(gid)
    assert status["over"] is True
    assert "A (white) gagne" in status["result"]
    assert status["winner"] == {"name": "A", "color": "white"}


def test_clock_frozen_during_server_downtime(tmp_path):
    """Le temps du redémarrage ne compte pas : l'horloge reprend où elle en était."""
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    game = server.GAMES[gid]
    game["turn_started_at"] -= 10  # blancs ont réfléchi 10 s
    server._persist()
    # simule un arrêt de 100 s du serveur
    path = _state_file(tmp_path)
    state = json.loads(path.read_text(encoding="utf-8"))
    state["saved_at"] -= 100
    path.write_text(json.dumps(state), encoding="utf-8")
    turn_before = server.GAMES[gid]["turn_started_at"]

    _restart()

    game = server.GAMES[gid]
    # turn_started_at décalé d'environ la durée d'arrêt -> temps restant identique
    assert abs((game["turn_started_at"] - turn_before) - 100) < 5
    assert game["clock_ms"]["white"] == 600_000
    assert game["clock_started"] is True


def test_flagged_game_stays_over_after_restart(tmp_path):
    gid = server.new_game(white="A", black="B", clock="1+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    game = server.GAMES[gid]
    game["turn_started_at"] -= 61  # drapeau blanc
    server._check_flag(game)
    assert game["flagged"] == "white"
    server._persist()

    _restart()

    assert server.GAMES[gid]["flagged"] == "white"
    assert server.game_status(gid)["over"] is True
    assert "B (black) gagne au drapeau" in server.game_status(gid)["result"]


def test_delete_is_persisted(tmp_path):
    gid = server.new_game(white="A", black="B")["game_id"]
    server.delete_game(gid)

    _restart()

    assert gid not in server.GAMES
    assert all(g["game_id"] != gid for g in server.list_games()["games"])


def test_published_puzzle_reloaded_and_continues(tmp_path):
    puzzle = next(p for p in server.PUZZLES if p.get("MateIn") == 2)
    pid = puzzle["PuzzleId"]
    line = puzzle["Moves"].split()  # [clé, réponse adverse, mat]
    gid = server.start_puzzle(pid, name="Puzzle test")["game_id"]
    r = server.try_puzzle_move(pid, line[0], game_id=gid)  # clé + réponse auto
    assert r["correct"] is True
    fen_before = server.GAMES[gid]["board"].fen()

    _restart()

    assert gid in server.GAMES
    game = server.GAMES[gid]
    assert game["kind"] == "puzzle"
    assert game["name"] == "Puzzle test"
    assert game["puzzle"]["puzzle"]["PuzzleId"] == pid
    assert game["puzzle"]["plies_done"] == 2  # clé + réponse adverse jouées
    assert game["board"].fen() == fen_before
    # la résolution peut continuer après redémarrage : coup de mat final
    assert server.try_puzzle_move(pid, line[2], game_id=gid)["solved"] is True


def test_next_puzzle_session_reloaded(tmp_path):
    r = server.next_puzzle()
    pid = r["puzzle_id"]
    assert pid in server.ACTIVE_PUZZLES

    _restart()

    assert pid in server.ACTIVE_PUZZLES
    session = server.ACTIVE_PUZZLES[pid]
    assert session["puzzle"]["PuzzleId"] == pid
    assert session["plies_done"] == 0


def test_cheat_alerts_persisted(tmp_path):
    gid = server.new_game(white="A", black="B")["game_id"]
    alert = server._note_cheat(gid)  # partie en cours -> alerte enregistrée
    assert alert is not None
    server._persist()

    _restart()

    assert len(server.CHEAT_ALERTS) == 1
    assert server.CHEAT_ALERTS[0]["analyzed"] == gid
    assert gid in server.CHEAT_ALERTS[0]["while_in_progress"]


def test_published_analysis_survives_restart(tmp_path):
    """Régression : l'analyse publiée (analyze_game, publish=True) doit être
    restaurée au redémarrage.

    `_game_to_json` sérialise `analysis`, mais `_game_from_json` ne la
    restaurait pas : après un restart du conteneur, la partie analysée
    redevenait `analysed=False` dans /web/api/games — elle quittait le mode
    « Analyse » de l'interface et n'apparaissait plus que comme « terminée ».
    """
    gid = server.new_game(white="Nicolas", black="Agent IA")["game_id"]
    server.play_move(gid, "e4", color="white")
    server.GAMES[gid]["analysis"] = {"depth": 12, "evals": [0.31, 0.12], "at": 1700000000.0}
    server.GAMES[gid]["last_analysis_at"] = 1700000000.0
    server._persist()
    raw = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    assert next(g for g in raw["games"] if g["game_id"] == gid)["analysis"]

    _restart()

    game = server.GAMES[gid]
    assert game["analysis"] == {"depth": 12, "evals": [0.31, 0.12], "at": 1700000000.0}
    assert game["last_analysis_at"] == 1700000000.0
    # l'API web la classe bien comme analysée (filtre du mode « Analyse »)
    assert server._web_state(gid, game, None)["analysed"] is True


def test_corrupt_state_file_starts_empty(tmp_path):
    _state_file(tmp_path).write_text("{ pas du json valide !!!", encoding="utf-8")
    server.GAMES["bidon"] = {"kind": "game"}

    _restart()  # ne doit pas lever d'exception

    assert server.GAMES == {}


def test_state_file_is_atomic_and_versioned(tmp_path):
    server.new_game(white="A", black="B")
    path = _state_file(tmp_path)
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["version"] == 1
    assert "saved_at" in state
    assert len(state["games"]) == 1
    g = state["games"][0]
    assert "root_fen" in g and "moves" in g and g["kind"] == "game"
    assert not path.with_name(path.name + ".tmp").exists()  # tmp nettoyé par os.replace


def test_persist_concurrent_no_error(tmp_path, monkeypatch):
    """Régression issue #55 : des _persist concurrents ne doivent plus lever
    d'Errno 2 (course sur le fichier .tmp), même en 7 appels parallèles."""
    import threading

    server.GAMES.clear()
    for i in range(30):
        server.new_game(white=f"W{i}", black=f"B{i}")
    errors = []

    def run():
        try:
            for _ in range(5):
                server._persist()
        except OSError as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(7)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
