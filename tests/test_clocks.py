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
    assert 599_000 < r["clock"]["white_ms"] <= 600_000
    assert r["clock"]["black_ms"] == 600_000
    assert r["clock"]["running"] == "white"
    # partie sans horloge par défaut
    assert server.new_game()["clock"] is None
    assert server.new_game(clock="infinite")["clock"] is None


def test_clock_invalid_format_rejected():
    for bad in ("abc", "10+", "+5", "0+0", "999+0"):
        with pytest.raises(ValueError):
            server.new_game(clock=bad)


def test_clock_decremented_after_move():
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    game = server.GAMES[gid]
    game["turn_started_at"] -= 3.0  # simuler 3 s de réflexion
    server.play_move(gid, "e4", color="white")
    # ~3 s débitées (tolérance d'exécution)
    assert 600_000 - 3_200 < game["clock_ms"]["white"] <= 600_000 - 2_800


def test_flag_on_timeout():
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    game = server.GAMES[gid]
    # les blancs réfléchissent depuis plus de 10 minutes
    game["turn_started_at"] -= 601
    with pytest.raises(ValueError, match="perdu au drapeau"):
        server.play_move(gid, "e4", color="white")
    assert game["flagged"] == "white"
    # la partie est terminée : statut, résultat, état web
    assert server._game_status(game) == "terminée"
    state = server._web_state(gid, game, None)
    assert state["over"] is True
    assert "gagne au drapeau" in state["result"]
    # les coups suivants sont refusés
    with pytest.raises(ValueError, match="drapeau"):
        server.play_move(gid, "e5", color="black")


def test_get_board_shows_both_clocks():
    """L'agent voit les temps restants des deux camps pour gérer son temps."""
    gid = server.new_game(white="A", black="B", clock="10+0")["game_id"]
    b = server.get_board(gid)
    assert b["clock"]["white_ms"] <= 600_000
    assert b["clock"]["black_ms"] == 600_000
    assert b["clock"]["running"] == "white"
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
    assert 599_000 < state["clock"]["white_ms"] <= 600_000
    assert state["clock"]["running"] == "white"


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
