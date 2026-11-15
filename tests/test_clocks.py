"""Tests des horloges de partie (10+0, drapeau, temps visibles)."""

import pytest

import server


@pytest.fixture(autouse=True)
def _clean_state():
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()


def test_new_game_with_clock():
    r = server.new_game(white="A", black="B", clock="10+0")
    # horloge présente mais PAS démarrée : les deux camps ne sont pas encore couverts
    assert r["clock"]["white_ms"] == 600_000
    assert r["clock"]["black_ms"] == 600_000
    assert r["clock"]["running"] is None
    assert r["clock"]["started"] is False
    # partie sans horloge par défaut
    assert server.new_game()["clock"] is None
    assert server.new_game(clock="infinite")["clock"] is None


def test_clock_starts_only_when_both_sides_covered():
    """L'horloge ne démarre que lorsque les deux camps sont occupés (joueur ou moteur)."""
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    clock = server.get_board(gid)["clock"]
    assert clock["started"] is False and clock["running"] is None
    server.join_game(gid, "B", color="black")
    clock = server.get_board(gid)["clock"]
    assert clock["started"] is True
    assert clock["running"] == "white"
    assert 599_000 < clock["white_ms"] <= 600_000


def test_clock_starts_with_engine_covering_one_side():
    """Contre le moteur, un seul joueur suffit à démarrer l'horloge."""
    gid = server.new_game(white="A", engine_color="black", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    clock = server.get_board(gid)["clock"]
    assert clock["started"] is True and clock["running"] == "white"


def test_first_white_move_blocked_until_other_side_covered():
    """Avec une horloge, le 1er coup des blancs exige que le camp noir soit couvert."""
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    with pytest.raises(ValueError, match="black n'a pas encore été rejoint"):
        server.play_move(gid, "e4", color="white")
    # sans horloge : aucun blocage (partie ouverte)
    gid2 = server.new_game(white="A", black="B")["game_id"]
    assert server.play_move(gid2, "e4", color="white")["move_played"] == "e4"
    # camp adverse couvert par le moteur : 1er coup autorisé
    gid3 = server.new_game(white="A", engine_color="black", clock="10+0")["game_id"]
    assert server.play_move(gid3, "e4", color="white")["move_played"] == "e4"
    # dès que l'adversaire rejoint, le 1er coup est autorisé
    server.join_game(gid, "B", color="black")
    assert server.play_move(gid, "e4", color="white", player="A")["move_played"] == "e4"


def test_clock_invalid_format_rejected():
    for bad in ("abc", "10+", "+5", "0+0", "999+0"):
        with pytest.raises(ValueError):
            server.new_game(clock=bad)


def test_clock_decremented_after_move():
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    game = server.GAMES[gid]
    game["turn_started_at"] -= 3.0  # simuler 3 s de réflexion
    server.play_move(gid, "e4", color="white", player="A")
    # ~3 s débitées (tolérance d'exécution)
    assert 600_000 - 3_200 < game["clock_ms"]["white"] <= 600_000 - 2_800


def test_flag_on_timeout():
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    game = server.GAMES[gid]
    # les blancs réfléchissent depuis plus de 10 minutes
    game["turn_started_at"] -= 601
    with pytest.raises(ValueError, match="drapeau"):
        server.play_move(gid, "e4", color="white", player="A")
    assert game["flagged"] == "white"
    # la partie est terminée : statut, résultat (avec vainqueur), état web
    assert server._game_status(game) == "terminée"
    state = server._web_state(gid, game, None)
    assert state["over"] is True
    assert "gagne au drapeau" in state["result"]
    assert "B (black)" in state["result"]
    assert state["winner"] == {"name": "B", "color": "black"}
    # les coups suivants sont refusés
    with pytest.raises(ValueError, match="drapeau"):
        server.play_move(gid, "e5", color="black")


def test_get_board_shows_both_clocks():
    """L'agent voit les temps restants des deux camps pour gérer son temps."""
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    b = server.get_board(gid)
    assert b["clock"]["white_ms"] <= 600_000
    assert b["clock"]["black_ms"] == 600_000
    assert b["clock"]["running"] == "white"
    assert b["clock"]["base_ms"] == 600_000
    # sans horloge : champ null
    gid2 = server.new_game()["game_id"]
    assert server.get_board(gid2)["clock"] is None


def test_web_new_game_with_clock(client):
    r = client.post("/web/api/games", json={
        "your_name": "A", "your_color": "white",
        "against_engine": False, "clock": "10+0",
    })
    assert r.status_code == 200
    state = r.json()
    # le créateur occupe son camp, mais l'horloge ne démarre pas tant que
    # l'adversaire n'a pas rejoint
    assert state["white_seat"] == "A"
    assert state["clock"]["started"] is False
    assert state["clock"]["running"] is None
    assert state["clock"]["white_ms"] == 600_000
    # l'adversaire rejoint : l'horloge démarre
    r = client.post(f"/web/api/games/{state['game_id']}/join",
                    json={"name": "B", "color": "black"})
    assert r.status_code == 200
    clock = r.json()["clock"]
    assert clock["started"] is True
    assert clock["running"] == "white"
    assert 599_000 < clock["white_ms"] <= 600_000


def test_web_new_game_invalid_clock(client):
    r = client.post("/web/api/games", json={
        "your_name": "A", "your_color": "white", "clock": "abc",
    })
    assert r.status_code == 400


def test_engine_game_clock_runs_for_both_sides():
    """Contre le moteur : l'horloge du moteur est aussi débitée (très peu)."""
    gid = server.new_game(engine_color="black", clock="10+0")["game_id"]
    game = server.GAMES[gid]
    server.play_move(gid, "e4", color="white")
    # le moteur a répondu quasi instantanément : son horloge a bougé d'à peine
    assert game["clock_ms"]["black"] > 599_000
    assert game["clock_ms"]["white"] <= 600_000


def test_web_move_debits_player_clock(client):
    """Régression : le coup joué via l'interface web doit débiter l'horloge
    du JOUEUR (sinon son temps de réflexion était facturé au moteur)."""
    r = client.post("/web/api/games", json={
        "your_name": "A", "your_color": "white",
        "against_engine": True, "clock": "10+0",
    })
    gid = r.json()["game_id"]
    game = server.GAMES[gid]
    black_before = game["clock_ms"]["black"]
    game["turn_started_at"] -= 5.0  # 5 s de réflexion du joueur
    r = client.post(f"/web/api/games/{gid}/move",
                    json={"move": "e2e4", "your_color": "white", "your_name": "A"})
    assert r.status_code == 200
    # l'horloge du joueur a été débitée (~5 s), celle du moteur à peine
    assert game["clock_ms"]["white"] < 600_000 - 4_000
    assert game["clock_ms"]["black"] > black_before - 500


def test_move_clocks_in_history(client):
    """Le temps consommé par coup est exposé dans l'historique (debrief)."""
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    game = server.GAMES[gid]
    game["turn_started_at"] -= 2.5
    server.play_move(gid, "e4", color="white", player="A")
    state = server._web_state(gid, game, None)
    h0 = state["history"][0]
    assert h0["san"] == "e4"
    assert h0["clock"]["color"] == "white"
    assert 2_000 < h0["clock"]["spent_ms"] < 3_000
    assert h0["clock"]["remaining_ms"] < 600_000


def test_get_board_ply_clocks():
    """get_board(ply=N) : temps restants de chaque camp à ce moment-là."""
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    game = server.GAMES[gid]
    game["turn_started_at"] -= 2.0
    server.play_move(gid, "e4", color="white", player="A")
    server.play_move(gid, "e5", color="black", player="B")
    # après le 1er coup : blanc a joué (~2 s), noir pas encore (base)
    b1 = server.get_board(gid, ply=1)
    assert b1["ply_clocks"]["white_ms"] < 600_000 - 1_000
    assert b1["ply_clocks"]["black_ms"] == 600_000
    # position initiale : les deux au temps de base
    b0 = server.get_board(gid, ply=0)
    assert b0["ply_clocks"] == {"white_ms": 600_000, "black_ms": 600_000}
    # sans horloge : champ null
    gid2 = server.new_game(white="A", black="B")["game_id"]
    assert server.get_board(gid2, ply=0)["ply_clocks"] is None


def test_analyze_game_pgn_has_clk():
    """analyze_game : le PGN annoté contient [%clk H:MM:SS] après chaque coup."""
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    game = server.GAMES[gid]
    game["turn_started_at"] -= 2.0
    server.play_move(gid, "e4", color="white", player="A")
    server.play_move(gid, "e5", color="black", player="B")
    result = server.analyze_game(gid, depth=5, publish=False)
    assert "[%clk 0:09:5" in result["pgn"]
    assert "[%eval" in result["pgn"]


def test_wait_for_turn_returns_game_over_on_flag():
    """Régression : un drapeau tombé doit être vu par wait_for_turn.

    Sans détection dans la boucle, l'agent adverse attendait en boucle des
    timeouts sans jamais voir la fin de partie.
    """
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    game = server.GAMES[gid]
    server.play_move(gid, "e4", color="white", player="A")
    # noirs au trait avec 1 s restante, réflexion déjà écoulée : drapeau
    game["clock_ms"]["black"] = 1_000
    game["turn_started_at"] -= 5.0
    result = server.wait_for_turn(gid, color="white")
    assert result["ready"] is False
    assert result["game_over"] == "Temps écoulé ! A (white) gagne au drapeau."


def test_get_board_shows_flag_result():
    """L'agent qui poll get_board doit voir la fin au drapeau explicitement."""
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    game = server.GAMES[gid]
    server.play_move(gid, "e4", color="white", player="A")
    game["clock_ms"]["black"] = 1_000
    game["turn_started_at"] -= 5.0
    b = server.get_board(gid)
    assert b["over"] is True
    assert b["result"] == "Temps écoulé ! A (white) gagne au drapeau."
    assert b["winner"] == {"name": "A", "color": "white"}
    assert b["clock"]["running"] is None
