"""Tests des évolutions : SAN ambigu refusé, en-têtes PGN, resign(player=...),
bouton/endpoint d'abandon web, nom du vainqueur, horloge à deux joueurs."""

import asyncio
import re
import shutil

import httpx
import pytest
from httpx import ASGITransport

import server

STOCKFISH = shutil.which("stockfish") or shutil.which("/tmp/sf/stockfish")
needs_stockfish = pytest.mark.skipif(not STOCKFISH, reason="Stockfish non installé")

# Deux cavaliers blancs (c7, f6) peuvent prendre la dame en d5 : « Nxd5 » est ambigu.
AMBIGUOUS_FEN = "7k/2N5/5N2/3q4/8/8/8/4K3 w - - 0 1"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("LICHESS_MCP_AUTH_TOKEN", raising=False)
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()


@pytest.fixture
def client():
    app = server.mcp.http_app()

    class _AsyncClient:
        def __init__(self):
            self._client = httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            )

        def _run(self, coro):
            return asyncio.run(coro)

        def get(self, path, headers=None):
            return self._run(self._client.get(path, headers=headers))

        def post(self, path, json=None, headers=None):
            return self._run(self._client.post(path, json=json, headers=headers))

    return _AsyncClient()


# ---------------------------------------------------------------- 1) SAN ambigu

def test_play_move_ambiguous_san_rejected():
    gid = server.new_analysis_board(AMBIGUOUS_FEN)["game_id"]
    with pytest.raises(ValueError, match="ambigu"):
        server.play_move(gid, "Nxd5", color="white")
    # la position n'a pas bougé
    assert len(server.GAMES[gid]["board"].move_stack) == 0


def test_play_move_disambiguated_san_accepted():
    gid = server.new_analysis_board(AMBIGUOUS_FEN)["game_id"]
    r = server.play_move(gid, "Ncd5", color="white")
    assert r["move_played"] == "Ncxd5"
    gid2 = server.new_analysis_board(AMBIGUOUS_FEN)["game_id"]
    assert server.play_move(gid2, "Nfd5", color="white")["move_played"] == "Nfxd5"
    gid3 = server.new_analysis_board(AMBIGUOUS_FEN)["game_id"]
    assert server.play_move(gid3, "c7d5", color="white")["move_played"] == "Ncxd5"


def test_web_move_ambiguous_san_rejected(client):
    gid = server.new_analysis_board(AMBIGUOUS_FEN)["game_id"]
    r = client.post(f"/web/api/games/{gid}/move",
                    json={"move": "Nxd5", "your_color": "white", "your_name": "A"})
    assert r.status_code == 400
    assert "ambigu" in r.json()["error"]
    r = client.post(f"/web/api/games/{gid}/move",
                    json={"move": "Ncd5", "your_color": "white", "your_name": "A"})
    assert r.status_code == 200
    assert r.json()["moves"][0] == "Ncxd5"


# ---------------------------------------------------------------- 2) en-têtes PGN

@needs_stockfish
def test_analyze_game_pgn_headers_complete():
    gid = server.new_game(white="Nicolas", black="Agent IA")["game_id"]
    for uci, color in (("f2f3", "white"), ("e7e5", "black"), ("g2g4", "white"), ("d8h4", "black")):
        server.play_move(gid, uci, color=color)
    r = server.analyze_game(gid, depth=6, publish=False, wait=True)
    pgn = r["pgn"]
    assert '[White "Nicolas"]' in pgn
    assert '[Black "Agent IA"]' in pgn
    assert '[Result "0-1"]' in pgn  # mat du berger : les noirs gagnent
    assert re.search(r'\[Date "\d{4}\.\d{2}\.\d{2}"\]', pgn)


@needs_stockfish
def test_analyze_game_pgn_result_on_resign():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.play_move(gid, "e4", color="white")
    server.play_move(gid, "e5", color="black")
    server.resign(gid, color="black")
    r = server.analyze_game(gid, depth=5, publish=False, wait=True)
    assert '[Result "1-0"]' in r["pgn"]  # les noirs ont abandonné
    assert '[White "A"]' in r["pgn"]
    assert '[Black "B"]' in r["pgn"]


@needs_stockfish
def test_analyze_pgn_keeps_source_headers():
    pgn = ('[Event "Test"]\n[White "Alice"]\n[Black "Bob"]\n[Result "1-0"]\n\n'
           '1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0')
    r = server.analyze_pgn(pgn, depth=5)
    assert '[Event "Test"]' in r["pgn"]
    assert '[White "Alice"]' in r["pgn"]
    assert '[Black "Bob"]' in r["pgn"]
    assert '[Result "1-0"]' in r["pgn"]


# ---------------------------------------------------------------- 2b) resign(player=...)

def test_resign_by_player():
    gid = server.new_game(white="Nicolas", black="Agent IA")["game_id"]
    server.join_game(gid, "Nicolas", color="white")
    server.join_game(gid, "Agent IA", color="black")
    r = server.resign(gid, player="Agent IA")
    assert "Agent IA (black) abandonne" in r["game_over"]
    assert "Nicolas (white) gagne" in r["game_over"]
    assert r["winner"] == {"name": "Nicolas", "color": "white"}


def test_resign_player_unknown_rejected():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.join_game(gid, "A", color="white")
    with pytest.raises(ValueError, match="n'occupe aucun camp"):
        server.resign(gid, player="Inconnu")


def test_resign_player_color_conflict_rejected():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.join_game(gid, "A", color="white")
    with pytest.raises(ValueError, match="occupe le camp white"):
        server.resign(gid, player="A", color="black")


def test_resign_on_finished_game_rejected():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.resign(gid, color="white")
    with pytest.raises(ValueError, match="déjà terminée"):
        server.resign(gid, color="black")


# ---------------------------------------------------------------- 3) abandon web

def test_resign_button_present_in_page():
    html = (server.WEB_DIR / "index.html").read_text(encoding="utf-8")
    app = (server.WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert 'id="btnResign"' in html
    assert "btnResign" in app and "/resign" in app
    # le bouton n'est affiché que pour un joueur : condition sur your_color + spectateur
    assert "spectator" in app


def test_web_resign_endpoint(client):
    gid = server.new_game(white="A", black="B")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    # nom ne correspondant pas au siège
    r = client.post(f"/web/api/games/{gid}/resign",
                    json={"your_color": "white", "your_name": "X"})
    assert r.status_code == 400
    # abandon par le joueur A : le vainqueur est nommé
    r = client.post(f"/web/api/games/{gid}/resign",
                    json={"your_color": "white", "your_name": "A"})
    assert r.status_code == 200
    state = r.json()
    assert state["over"] is True
    assert "A (white) abandonne" in state["result"]
    assert "B (black) gagne" in state["result"]
    assert state["winner"] == {"name": "B", "color": "black"}
    # partie déjà terminée
    r = client.post(f"/web/api/games/{gid}/resign",
                    json={"your_color": "black", "your_name": "B"})
    assert r.status_code == 400
    # un spectateur n'a pas de camp : refusé
    r = client.post(f"/web/api/games/{gid}/resign", json={"your_color": "spectate"})
    assert r.status_code == 400


def test_web_resign_engine_side_rejected(client):
    r = client.post("/web/api/games", json={
        "your_name": "A", "your_color": "white", "against_engine": True,
    })
    gid = r.json()["game_id"]
    r = client.post(f"/web/api/games/{gid}/resign",
                    json={"your_color": "black", "your_name": "A"})
    assert r.status_code == 400


# ---------------------------------------------------------------- 4) vainqueur nommé

def test_wait_for_turn_names_winner_on_resign():
    gid = server.new_game(white="A", black="B")["game_id"]
    server.resign(gid, color="white")
    r = server.wait_for_turn(gid, color="black", timeout_seconds=1)
    assert r["ready"] is False
    assert "A (white) abandonne" in r["game_over"]
    assert "B (black) gagne" in r["game_over"]
    assert r["winner"] == {"name": "B", "color": "black"}


def test_game_status_names_winner_on_flag():
    gid = server.new_game(white="A", black="B", clock="1+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    game = server.GAMES[gid]
    game["turn_started_at"] -= 61  # le trait est aux blancs, temps écoulé
    status = server.game_status(gid)
    assert status["over"] is True
    assert "B (black) gagne au drapeau" in status["result"]
    assert status["winner"] == {"name": "B", "color": "black"}


def test_play_move_error_names_winner_on_flag():
    gid = server.new_game(white="A", black="B", clock="1+0")["game_id"]
    server.join_game(gid, "A", color="white")
    server.join_game(gid, "B", color="black")
    server.GAMES[gid]["turn_started_at"] -= 61
    with pytest.raises(ValueError, match=r"B \(black\) gagne au drapeau"):
        server.play_move(gid, "e4", color="white", player="A")
