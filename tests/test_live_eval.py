"""Issue #94 : jauge d'évaluation temps réel pour les spectateurs (web) +
outil MCP spectator_analysis pour les agents spectateurs.

Le calcul Stockfish est monkeypatché (pas de Stockfish en local) : on teste le
déclenchement (spectateur présent), le recalcul à chaque coup, l'arrêt sans
spectateur, le filtrage fair-play côté web (live_eval jamais renvoyée aux
joueurs) et l'outil MCP.
"""

import asyncio
import time

import chess
import httpx
import pytest
from httpx import ASGITransport

import server


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.delenv("LICHESS_MCP_AUTH_TOKEN", raising=False)
    server.GAMES.clear()
    yield
    server.GAMES.clear()


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


def _fake_compute(game):
    """Remplace le calcul Stockfish : eval fixe dépendant du nombre de coups."""
    ply = len(game["board"].move_stack)
    return {
        "eval_white": 100 + ply,
        "eval": f"+{100 + ply:.0f}.00",
        "pv_uci": ["e2e4"],
        "pv_san": ["e4"],
        "depth": server._LIVE_EVAL_DEPTH,
        "fen": game["board"].fen(),
        "at_ply": ply,
        "at": "now",
    }


def _mk_game():
    return server._create_game("Blancs", "Noirs", "medium", None, kind="game")


def _wait_live(gid, timeout=5.0):
    """Attend la fin du thread live éventuel."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with server._LIVE_LOCK:
            if gid not in server._LIVE_RUNNING:
                return True
        time.sleep(0.05)
    return False


def test_no_live_eval_without_spectator(client, monkeypatch):
    """Sans spectateur : aucune analyse live déclenchée, live_eval absent du state."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    r = client.get(f"/web/api/games/{gid}")
    assert r.status_code == 200
    assert r.json()["live_eval"] is None
    assert server.GAMES[gid]["live_eval"] is None
    # un joueur (non spectateur) qui rejoint ne déclenche rien non plus
    client.post(f"/web/api/games/{gid}/join", json={"name": "Alice", "color": "white"})
    _wait_live(gid)
    assert server.GAMES[gid]["live_eval"] is None
    assert client.get(f"/web/api/games/{gid}").json()["live_eval"] is None


def test_spectator_join_triggers_live_eval_and_web_filter(client, monkeypatch):
    """Un spectateur déclenche l'eval live ; seul le state demandé avec
    spectator=1 la contient (fair-play : jamais renvoyée aux joueurs)."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    client.post(f"/web/api/games/{gid}/join", json={"name": "Alice", "color": "white"})
    r = client.post(f"/web/api/games/{gid}/join", json={"name": "Bob", "spectate": True})
    assert r.status_code == 200
    assert r.json()["live_eval"] is not None  # le join spectateur la renvoie
    assert _wait_live(gid)
    game = server.GAMES[gid]
    assert game["live_eval"]["eval_white"] == 100  # 0 coup joué
    assert game["live_eval"]["at_ply"] == 0
    # spectateur web (spectator=1) -> reçoit live_eval
    r = client.get(f"/web/api/games/{gid}?spectator=1")
    assert r.json()["live_eval"]["eval_white"] == 100
    # joueur (your_color, sans spectator=1) -> JAMAIS live_eval (fair-play)
    r = client.get(f"/web/api/games/{gid}?your_color=white")
    assert r.json()["live_eval"] is None
    r = client.get(f"/web/api/games/{gid}")
    assert r.json()["live_eval"] is None


def test_live_eval_recomputed_on_move(client, monkeypatch):
    """À chaque coup joué, l'eval live est recalculée (at_ply suit)."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    client.post(f"/web/api/games/{gid}/join", json={"name": "Alice", "color": "white"})
    client.post(f"/web/api/games/{gid}/join", json={"name": "Bob", "spectate": True})
    assert _wait_live(gid)
    assert server.GAMES[gid]["live_eval"]["at_ply"] == 0
    # Alice joue e2e4
    r = client.post(f"/web/api/games/{gid}/move",
                    json={"move": "e2e4", "your_color": "white", "your_name": "Alice"})
    assert r.status_code == 200
    assert _wait_live(gid)
    live = server.GAMES[gid]["live_eval"]
    assert live["at_ply"] == 1
    assert live["eval_white"] == 101  # recalculé (fake dépend du pli)
    # le state du move (joueur) ne contient pas live_eval (fair-play)
    assert r.json()["live_eval"] is None


def test_live_eval_stops_when_no_spectator(client, monkeypatch):
    """Plus aucun spectateur -> le cache live_eval est vidé (économie Pi)."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    client.post(f"/web/api/games/{gid}/join", json={"name": "Bob", "spectate": True})
    assert _wait_live(gid)
    assert server.GAMES[gid]["live_eval"] is not None
    # Bob "part" (plus de spectateur enregistré)
    server.GAMES[gid]["spectators"].clear()
    r = client.get(f"/web/api/games/{gid}?spectator=1")
    assert r.json()["live_eval"] is None
    assert server.GAMES[gid]["live_eval"] is None


def test_spectator_analysis_tool(monkeypatch):
    """Outil MCP spectator_analysis : eval + PV de la position courante."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    out = server.spectator_analysis(gid)
    assert out["game_id"] == gid
    assert out["eval_white"] == 100
    assert out["eval"] == "+100.00"
    assert out["pv_san"] == ["e4"] and out["pv_uci"] == ["e2e4"]
    assert out["depth"] == server._LIVE_EVAL_DEPTH
    assert out["turn"] == "white" and out["plies"] == 0 and out["over"] is False
    # cache : second appel sans recalcul (même at_ply)
    out2 = server.spectator_analysis(gid)
    assert out2["eval_white"] == 100
    # partie inconnue -> erreur
    with pytest.raises(ValueError):
        server.spectator_analysis("inconnu00")
    # kind != game -> refusé
    gid2 = server._create_game("Blancs", "Noirs", "medium", None, kind="analysis")
    with pytest.raises(ValueError):
        server.spectator_analysis(gid2)


def test_evaluate_position_requires_stockfish(monkeypatch):
    """analysis.evaluate_position lève une erreur claire sans Stockfish."""
    import analysis
    monkeypatch.setattr(analysis, "stockfish_path", lambda: None)
    monkeypatch.setattr(analysis, "_LIVE_ENGINE_STATE", {"engine": None})
    with pytest.raises(ValueError, match="Stockfish"):
        analysis.evaluate_position(chess.Board(), depth=10)


def test_evaluate_position_uses_persistent_engine_and_reports_depth(monkeypatch):
    """Issue #94 retour (divergence de valeur) : le moteur live est PERSISTANT
    (partagé entre appels, comme le post-mortem) et la profondeur réellement
    atteinte est rapportée (depth_reached) pour diagnostiquer."""
    import analysis

    class FakeEngine:
        def __init__(self):
            self.calls = 0
            self.configured = []

        def configure(self, opts):
            self.configured.append(opts)

        def analyse(self, board, limit):
            self.calls += 1
            return {"score": chess.engine.PovScore(chess.engine.Cp(40), chess.WHITE),
                    "pv": [chess.Move.from_uci("e2e4")], "depth": limit.depth}

    fake = FakeEngine()
    monkeypatch.setattr(analysis, "_live_stockfish", lambda: fake)
    r1 = analysis.evaluate_position(chess.Board(), depth=10)
    r2 = analysis.evaluate_position(chess.Board(), depth=10)
    assert fake.calls == 2                      # même process réutilisé
    assert r1["depth"] == 10 and r1["depth_reached"] == 10
    assert r1["eval_white"] == 40 and r1["eval"] == "+0.40"
    assert r1["pv_san"] == ["e4"] and r1["fen"] == chess.Board().fen()
    assert r2["eval_white"] == 40


def test_san_safe_never_raises_and_legal_moves_complete(monkeypatch):
    """Issue #94 retour annexe : legal_moves ne doit jamais omettre un coup ni
    planter (SAN ambigu -> UCI en secours)."""
    gid = server._create_game("Blancs", "Noirs", "medium", None, kind="game")
    # position avec deux cavaliers pouvant prendre la même case (c7/f6 x d5)
    server.GAMES[gid]["board"] = chess.Board("7k/2N5/5N2/3q4/8/8/8/4K3 w - - 0 1")
    out = server.legal_moves(gid)
    board = server.GAMES[gid]["board"]
    assert out["count"] == len(list(board.legal_moves))
    assert len(out["moves"]) == out["count"]  # aucun coup omis
    # la prise de la dame en d5 y figure, désambiguïsée (Ncxd5 / Nfxd5)
    assert any(m in ("Ncd5", "Nfd5", "Ncxd5", "Nfxd5") for m in out["moves"])
    # cas extrême : san() qui lève -> repli UCI, pas d'exception
    class BoomBoard(chess.Board):
        def san(self, move, **kw):
            raise chess.AmbiguousMoveError("boom")
    b = BoomBoard("7k/2N5/5N2/3q4/8/8/8/4K3 w - - 0 1")
    mv = next(iter(b.legal_moves))
    assert server._san_safe(b, mv) == mv.uci()


def _wait_live_ply(gid, ply, timeout=8.0):
    """Attend que le cache live atteigne le pli donné (et plus de worker actif)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        game = server.GAMES.get(gid)
        le = (game or {}).get("live_eval")
        with server._LIVE_LOCK:
            running = gid in server._LIVE_RUNNING
        if le and le.get("at_ply") == ply and not running:
            return True
        time.sleep(0.05)
    return False


def test_live_eval_compute_snapshots_position(monkeypatch):
    """Issue #94 retour : snapshot atomique — eval/fen/at_ply viennent de la même
    position, même si un coup est joué pendant le calcul."""
    import analysis
    seen = {}

    def fake_eval(board, depth=10):
        seen["fen"] = board.fen()
        seen["plies"] = len(board.move_stack)
        return {"eval_white": 42, "eval": "+0.42", "pv_uci": [], "pv_san": [],
                "depth": depth, "fen": board.fen()}

    monkeypatch.setattr(analysis, "evaluate_position", fake_eval)
    gid = _mk_game()
    game = server.GAMES[gid]
    res = server._live_eval_compute(game)
    # un coup joué APRÈS le calcul ne doit pas corrompre at_ply/fen du résultat
    game["board"].push(chess.Move.from_uci("e2e4"))
    assert res["at_ply"] == 0
    assert res["fen"] == seen["fen"]
    assert seen["plies"] == 0


def test_live_eval_catches_up_after_move_during_compute(client, monkeypatch):
    """Issue #94 retour : un coup joué pendant le calcul déclenche un recalcul
    (le worker relance) — la jauge ne reste pas figée."""
    def slow_compute(game):
        board = game["board"].copy(stack=True)
        ply = len(board.move_stack)
        time.sleep(0.4)  # simule le temps Stockfish
        return {"eval_white": 100 + ply, "eval": "+1.00", "pv_uci": [],
                "pv_san": [], "depth": 10, "fen": board.fen(),
                "at_ply": ply, "at": "now"}

    monkeypatch.setattr(server, "_live_eval_compute", slow_compute)
    gid = _mk_game()
    client.post(f"/web/api/games/{gid}/join", json={"name": "Alice", "color": "white"})
    client.post(f"/web/api/games/{gid}/join", json={"name": "Bob", "spectate": True})
    time.sleep(0.1)  # le worker démarre (calcul en cours)
    client.post(f"/web/api/games/{gid}/move",
                json={"move": "e2e4", "your_color": "white", "your_name": "Alice"})
    assert _wait_live_ply(gid, 1), "le cache live doit rattraper le pli 1"
    assert server.GAMES[gid]["live_eval"]["at_ply"] == 1
    assert server.GAMES[gid]["live_eval"]["eval_white"] == 101


def test_live_evals_history_for_spectator(client, monkeypatch):
    """Issue #94 retour : évals des positions passées visibles pour le
    spectateur (live_evals par pli), jamais pour un joueur."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    client.post(f"/web/api/games/{gid}/join", json={"name": "Alice", "color": "white"})
    client.post(f"/web/api/games/{gid}/join", json={"name": "Bob", "spectate": True})
    assert _wait_live_ply(gid, 0)
    client.post(f"/web/api/games/{gid}/move",
                json={"move": "e2e4", "your_color": "white", "your_name": "Alice"})
    assert _wait_live_ply(gid, 1)
    hist = server.GAMES[gid]["live_evals"]
    assert hist == {0: 100, 1: 101}
    # spectateur web : reçoit l'historique
    r = client.get(f"/web/api/games/{gid}?spectator=1")
    assert r.json()["live_evals"] == {"0": 100, "1": 101}
    # joueur : jamais (fair-play)
    r = client.get(f"/web/api/games/{gid}?your_color=white")
    assert r.json()["live_evals"] is None


def test_live_eval_follows_mcp_moves(monkeypatch):
    """Issue #94 : un coup joué via l'outil MCP play_move déclenche aussi le
    recalcul live (pas seulement l'endpoint web) — la jauge UI suit chaque coup."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    server.join_game(gid, "Alice", color="white")
    server.join_game(gid, "Bob", spectate=True)
    assert _wait_live_ply(gid, 0)
    server.play_move(gid, "e2e4", color="white", player="Alice")
    assert _wait_live_ply(gid, 1), "le coup MCP doit déclencher le recalcul live"
    assert server.GAMES[gid]["live_eval"]["at_ply"] == 1
    server.play_move(gid, "e7e5", color="black", player="Bob")
    assert _wait_live_ply(gid, 2)
    assert server.GAMES[gid]["live_eval"]["at_ply"] == 2
    assert server.GAMES[gid]["live_evals"] == {0: 100, 1: 101, 2: 102}


def test_spectator_analysis_response_is_atomically_consistent(monkeypatch):
    """Issue #94 retour : plies/fen/eval de spectator_analysis viennent du même
    snapshot (pas de plies frais + eval périmé)."""
    monkeypatch.setattr(server, "_live_eval_compute", _fake_compute)
    gid = _mk_game()
    out = server.spectator_analysis(gid)
    assert out["plies"] == 0
    assert out["fen"] == server.GAMES[gid]["board"].fen()
    assert out["eval_white"] == 100
    # un coup est joué, puis on rejoue l'outil : tout vient du même snapshot
    server.GAMES[gid]["board"].push(chess.Move.from_uci("e2e4"))
    out2 = server.spectator_analysis(gid)  # recalcule (cache périmé) sur snapshot pli 1
    assert out2["plies"] == 1
    board_after = chess.Board(out2["fen"])
    assert len(board_after.move_stack) == 0  # fen racine de la position au pli 1
    assert board_after.fen() != chess.Board().fen()  # position après e4
    assert out2["eval_white"] == 101  # fake dépend du pli du snapshot
