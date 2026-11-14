"""Tests du système de places (camps verrouillés au premier arrivé)."""

import pytest

import server


@pytest.fixture(autouse=True)
def _clean_state():
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()


# ---------------------------------------------------------------- join_game (MCP)

def test_join_claims_requested_color():
    gid = server.new_game(white="Blancs", black="Noirs")["game_id"]
    r = server.join_game(gid, "Nicolas", color="white")
    assert r["your_color"] == "white"
    assert r["white_seat"] == "Nicolas"
    assert r["black_seat"] is None


def test_join_second_player_gets_other_color():
    gid = server.new_game()["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    r = server.join_game(gid, "Agent IA")
    assert r["your_color"] == "black"


def test_join_occupied_color_rejected():
    gid = server.new_game()["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    with pytest.raises(ValueError, match="déjà occupé"):
        server.join_game(gid, "Agent IA", color="white")


def test_join_same_player_both_colors_rejected():
    gid = server.new_game()["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    with pytest.raises(ValueError, match="occupe déjà le camp white"):
        server.join_game(gid, "Nicolas", color="black")


def test_join_rejoin_idempotent():
    gid = server.new_game()["game_id"]
    server.join_game(gid, "Nicolas", color="black")
    r = server.join_game(gid, "Nicolas")
    assert r["your_color"] == "black"
    assert r["black_seat"] == "Nicolas"


def test_join_full_game_rejected():
    gid = server.new_game()["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    server.join_game(gid, "Agent IA", color="black")
    with pytest.raises(ValueError, match="Partie complète"):
        server.join_game(gid, "Spectateur")


def test_join_engine_side_rejected():
    gid = server.new_game(engine_color="black")["game_id"]
    with pytest.raises(ValueError, match="moteur"):
        server.join_game(gid, "Nicolas", color="black")
    r = server.join_game(gid, "Nicolas")
    assert r["your_color"] == "white"


def test_join_auto_skips_engine_color():
    gid = server.new_game(engine_color="white")["game_id"]
    r = server.join_game(gid, "Nicolas")
    assert r["your_color"] == "black"


def test_join_empty_name_rejected():
    gid = server.new_game()["game_id"]
    with pytest.raises(ValueError, match="non vide"):
        server.join_game(gid, "   ")


# ---------------------------------------------------------------- play_move + places

def test_play_move_on_claimed_seat_requires_owner():
    gid = server.new_game()["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    with pytest.raises(ValueError, match="occupé par « Nicolas »"):
        server.play_move(gid, "e4", color="white")
    with pytest.raises(ValueError, match="occupé par « Nicolas »"):
        server.play_move(gid, "e4", color="white", player="Agent IA")
    r = server.play_move(gid, "e4", color="white", player="Nicolas")
    assert r["move_played"] == "e4"


def test_play_move_with_player_claims_free_seat():
    gid = server.new_game()["game_id"]
    r = server.play_move(gid, "e4", color="white", player="Nicolas")
    assert r["white_seat"] == "Nicolas"
    server.play_move(gid, "e5", color="black", player="Agent IA")
    with pytest.raises(ValueError, match="ne peut pas jouer les deux camps"):
        server.play_move(gid, "a3", color="white", player="Agent IA")


def test_player_cannot_play_both_sides():
    gid = server.new_game()["game_id"]
    server.play_move(gid, "e4", color="white", player="Nicolas")
    with pytest.raises(ValueError, match="les deux camps"):
        server.play_move(gid, "e5", color="black", player="Nicolas")
    r = server.play_move(gid, "e5", color="black", player="Agent IA")
    assert r["move_played"] == "e5"


def test_unclaimed_seats_stay_open():
    gid = server.new_game()["game_id"]
    r = server.play_move(gid, "e4", color="white")
    assert r["move_played"] == "e4"
    r = server.play_move(gid, "e5", color="black")
    assert r["move_played"] == "e5"


def test_engine_side_still_rejected_with_player():
    gid = server.new_game(engine_color="black")["game_id"]
    with pytest.raises(ValueError, match="moteur"):
        server.play_move(gid, "e5", color="black", player="Nicolas")


def test_analysis_board_accepts_seats():
    gid = server.new_analysis_board()["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    r = server.play_move(gid, "e4", color="white", player="Nicolas")
    assert r["move_played"] == "e4"


# ---------------------------------------------------------------- API web

def test_web_join(client):
    gid = server.new_game(white="Blancs", black="Noirs")["game_id"]
    r = client.post(f"/web/api/games/{gid}/join", json={"name": "Nicolas", "color": "white"})
    assert r.status_code == 200
    state = r.json()
    assert state["your_color"] == "white"
    assert state["white_seat"] == "Nicolas"
    assert state["joined"] is True


def test_web_join_occupied_color(client):
    gid = server.new_game()["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    r = client.post(f"/web/api/games/{gid}/join", json={"name": "Agent IA", "color": "white"})
    assert r.status_code == 400


def test_web_join_empty_name(client):
    gid = server.new_game()["game_id"]
    r = client.post(f"/web/api/games/{gid}/join", json={"name": "  "})
    assert r.status_code == 400


def test_web_move_seat_enforced(client):
    gid = server.new_game()["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    r = client.post(f"/web/api/games/{gid}/move",
                    json={"move": "e2e4", "your_color": "white", "your_name": "Agent IA"})
    assert r.status_code == 400
    r = client.post(f"/web/api/games/{gid}/move",
                    json={"move": "e2e4", "your_color": "white", "your_name": "Nicolas"})
    assert r.status_code == 200
    assert r.json()["moves"][0] == "e4"


def test_web_move_auto_claims_with_name(client):
    gid = server.new_game()["game_id"]
    r = client.post(f"/web/api/games/{gid}/move",
                    json={"move": "e2e4", "your_color": "white", "your_name": "Nicolas"})
    assert r.status_code == 200
    assert r.json()["white_seat"] == "Nicolas"


def test_web_move_player_cannot_play_both_sides(client):
    gid = server.new_game()["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    server.GAMES[gid]["board"].push_uci("e2e4")
    r = client.post(f"/web/api/games/{gid}/move",
                    json={"move": "e7e5", "your_color": "black", "your_name": "Nicolas"})
    assert r.status_code == 400


def test_join_as_spectator():
    """L'agent peut rejoindre en spectateur : aucune place prise, illimité."""
    gid = server.new_game(white="A", black="B")["game_id"]
    r = server.join_game(gid, "Observateur", spectate=True)
    assert r["you_are"] == "spectator"
    assert r["spectators"] == 1
    assert r["white_seat"] is None and r["black_seat"] is None

    # illimité : plusieurs spectateurs, même sur une partie complète
    server.join_game(gid, "Nicolas", color="white")
    server.join_game(gid, "Agent IA", color="black")
    for i in range(5):
        server.join_game(gid, f"Spec{i}", spectate=True)
    r = server.join_game(gid, "Dernier", spectate=True)
    assert r["spectators"] == 7

    # idempotent pour un même nom
    server.join_game(gid, "Observateur", spectate=True)
    assert len(server.GAMES[gid]["spectators"]) == 7


def test_spectator_counter_in_web_state(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    server.join_game(gid, "Spec1", spectate=True)
    server.join_game(gid, "Spec2", spectate=True)
    state = client.get(f"/web/api/games/{gid}").json()
    assert state["spectators"] == 2


def test_web_join_spectate_endpoint(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    r = client.post(f"/web/api/games/{gid}/join", json={"name": "WebSpec", "spectate": True})
    assert r.status_code == 200
    state = r.json()
    assert state["your_color"] is None
    assert state["spectators"] == 1
    assert server.GAMES[gid]["white_seat"] is None
