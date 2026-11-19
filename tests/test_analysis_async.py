"""Tests de l'analyse asynchrone et de sa file sérielle (issue #73 + complément).

`analyze_game` rend la main immédiatement ; `analysis_status` suit la
progression jusqu'à done/failed ; un second appel sur la MÊME partie renvoie
already_running. **File sérielle** (complément #73) : une seule analyse
"running" à la fois sur tout le serveur — les autres demandes sont "queued"
avec leur position (queue_pos), progression bloquée à 0 tant qu'elles ne sont
pas en tête de file ; les intervalles d'exécution du moteur sont disjoints.
L'API web expose analysis_running / analysis_queued ; les jobs sont persistés
(un job "running" ou "queued" au redémarrage passe "failed").

Le vrai Stockfish est remplacé par un faux `analyze_moves` lent (avec
progress_cb), donc ces tests tournent partout.
"""

import asyncio
import time

import httpx
import pytest
from httpx import ASGITransport

import server


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.delenv("LICHESS_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LICHESS_MCP_STATE_FILE", str(tmp_path / "state.json"))
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    server.CHEAT_ALERTS.clear()
    server.ANALYSIS_JOBS.clear()
    server._ANALYSIS_QUEUE.clear()
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    server.CHEAT_ALERTS.clear()
    server.ANALYSIS_JOBS.clear()
    server._ANALYSIS_QUEUE.clear()


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

    return _AsyncClient()


def _game_with_moves(n: int = 4) -> str:
    gid = server.new_game(white="A", black="B")["game_id"]
    line = [("e4", "white"), ("e5", "black"), ("Nf3", "white"), ("Nc6", "black")]
    for mv, c in line[:n]:
        server.play_move(gid, mv, color=c)
    return gid


def _fake_analyze_moves(monkeypatch, delay: float = 0.05, fail: bool = False,
                        intervals: list | None = None):
    """Faux analyze_moves lent. Si `intervals` est une liste, chaque exécution
    y enregistre son intervalle [début, fin] pour prouver la sérialisation."""
    def fake(moves, depth=15, root=None, move_clocks=None, *, white_name=None,
             black_name=None, result=None, date=None, headers=None, progress_cb=None):
        t_start = time.time()
        try:
            for i in range(len(moves)):
                if fail and i == max(1, len(moves) // 2):
                    raise RuntimeError("stockfish crash simulé")
                time.sleep(delay)
                if progress_cb is not None:
                    progress_cb(i + 1, len(moves))
            return {"pgn": "1. e4 e5 2. Nf3", "evals": [{"i": i} for i in range(len(moves))],
                    "critical": [], "summary": {"white": {}, "black": {}}}
        finally:
            if intervals is not None:
                intervals.append((t_start, time.time()))
    monkeypatch.setattr(server, "analyze_moves", fake)


def _wait_status(gid: str, statuses=("done", "failed"), timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = server.analysis_status(gid)
        if out["status"] in statuses:
            return out
        time.sleep(0.02)
    raise AssertionError(f"analyse {gid} toujours pas {statuses} après {timeout}s")


def test_analyze_game_returns_immediately_then_done(monkeypatch):
    """Critère #73 : analyze_game rend la main en < 2 s (file vide -> running) ;
    analysis_status montre la progression puis done avec la réponse complète."""
    _fake_analyze_moves(monkeypatch, delay=0.15)
    gid = _game_with_moves(4)
    t0 = time.time()
    r = server.analyze_game(gid, depth=14, publish=False)
    dt = time.time() - t0
    assert dt < 2.0, f"analyze_game a bloqué {dt:.1f}s"
    assert r["status"] == "running" and r["analysis_id"] == gid
    assert r["moves"] == 4 and r["depth"] == 14
    out = _wait_status(gid)
    assert out["status"] == "done"
    assert out["progress"] == out["total"] == 4
    assert "pgn" in out and "evals" in out  # réponse complète
    assert out["elapsed_s"] >= 0.5  # le calcul a bien pris du temps


def test_already_running_no_second_stockfish(monkeypatch):
    """Un second appel sur la MÊME partie pendant le calcul -> already_running."""
    _fake_analyze_moves(monkeypatch, delay=0.2)
    gid = _game_with_moves(4)
    server.analyze_game(gid, depth=10, publish=False)
    r2 = server.analyze_game(gid, depth=10, publish=False)
    assert r2["status"] == "running" and r2.get("already_running") is True
    assert _wait_status(gid)["status"] == "done"


def test_second_game_queued_then_serialized(monkeypatch):
    """Complément #73 : une 2e analyse sur une AUTRE partie part en file
    ("queued", queue_pos=1, progress=0) et n'est exécutée qu'après la 1re."""
    intervals: list = []
    _fake_analyze_moves(monkeypatch, delay=0.15, intervals=intervals)
    gid1 = _game_with_moves(4)
    gid2 = _game_with_moves(4)

    r1 = server.analyze_game(gid1, depth=14, publish=False)
    assert r1["status"] == "running"  # file vide : démarre immédiatement
    r2 = server.analyze_game(gid2, depth=10, publish=False)
    assert r2["status"] == "queued" and r2["queue_pos"] == 1

    # tant que la 1re calcule, la 2e est bloquée en file, progression à 0
    st2 = server.analysis_status(gid2)
    assert st2["status"] == "queued" and st2["queue_pos"] == 1 and st2["progress"] == 0

    assert _wait_status(gid1)["status"] == "done"
    # la 2e passe running puis done
    out2 = _wait_status(gid2)
    assert out2["status"] == "done"
    # sérialisation prouvée : les intervalles d'exécution du moteur sont disjoints
    assert len(intervals) == 2
    (a0, a1), (b0, b1) = intervals
    assert a1 <= b0 + 1e-6 or b1 <= a0 + 1e-6, f"analyses parallèles ! {intervals}"


def test_queue_positions_fifo(monkeypatch):
    """Complément #73 : 3 analyses en rafale -> positions 1, 2 dans la file,
    exécution dans l'ordre FIFO."""
    intervals: list = []
    _fake_analyze_moves(monkeypatch, delay=0.1, intervals=intervals)
    gids = [_game_with_moves(2) for _ in range(3)]
    r1 = server.analyze_game(gids[0], depth=10, publish=False)
    r2 = server.analyze_game(gids[1], depth=10, publish=False)
    r3 = server.analyze_game(gids[2], depth=10, publish=False)
    assert r1["status"] == "running"
    assert r2["status"] == "queued" and r2["queue_pos"] == 1
    assert r3["status"] == "queued" and r3["queue_pos"] == 2
    for gid in gids:
        assert _wait_status(gid)["status"] == "done"
    # ordre FIFO : gid1 termine avant gid2, gid2 avant gid3
    ends = sorted(intervals, key=lambda iv: iv[0])
    assert len(ends) == 3
    assert ends[0][1] <= ends[1][0] + 1e-6 <= ends[1][1] <= ends[2][0] + 1e-6


def test_wait_true_returns_full_result(monkeypatch):
    """wait=True : comportement bloquant historique, réponse complète."""
    _fake_analyze_moves(monkeypatch, delay=0.02)
    gid = _game_with_moves(3)
    r = server.analyze_game(gid, depth=8, publish=False, wait=True)
    assert r["status"] == "done" and "pgn" in r


def test_wait_true_waits_through_queue(monkeypatch):
    """Complément #73 : wait=True bloque jusqu'à la fin DU job demandé,
    même s'il était en file derrière un autre."""
    _fake_analyze_moves(monkeypatch, delay=0.1)
    gid1 = _game_with_moves(4)
    gid2 = _game_with_moves(4)
    server.analyze_game(gid1, depth=14, publish=False)  # occupe le moteur
    r = server.analyze_game(gid2, depth=8, publish=False, wait=True)
    assert r["status"] == "done" and r["analysis_id"] == gid2 and "pgn" in r
    assert _wait_status(gid1)["status"] == "done"


def test_failed_job_reports_error(monkeypatch):
    _fake_analyze_moves(monkeypatch, delay=0.02, fail=True)
    gid = _game_with_moves(4)
    server.analyze_game(gid, depth=10, publish=False)
    out = _wait_status(gid)
    assert out["status"] == "failed"
    assert "crash simulé" in out["error"]


def test_failed_job_frees_queue(monkeypatch):
    """Complément #73 : un job qui échoue libère la file pour le suivant."""
    _fake_analyze_moves(monkeypatch, delay=0.05)
    gid1 = _game_with_moves(4)
    gid2 = _game_with_moves(4)
    # gid1 plante au milieu du calcul
    def fake_fail_first(moves, depth=15, root=None, move_clocks=None, *,
                         white_name=None, black_name=None, result=None, date=None,
                         headers=None, progress_cb=None):
        if depth == 14:
            raise RuntimeError("stockfish crash simulé")
        time.sleep(0.02)
        return {"pgn": "x", "evals": [], "critical": [], "summary": {}}
    monkeypatch.setattr(server, "analyze_moves", fake_fail_first)
    server.analyze_game(gid1, depth=14, publish=False)
    server.analyze_game(gid2, depth=10, publish=False)
    assert _wait_status(gid1)["status"] == "failed"
    # gid2 a bien pris la suite et terminé
    assert _wait_status(gid2)["status"] == "done"


def test_analysis_status_none_then_done_from_published(monkeypatch):
    gid = _game_with_moves(2)
    assert server.analysis_status(gid)["status"] == "none"
    # analyse publiée (session antérieure) -> status done
    server.GAMES[gid]["analysis"] = {"depth": 9, "evals": [], "at": 1.0}
    out = server.analysis_status(gid)
    assert out["status"] == "done" and out["depth"] == 9 and out["published"] is True


def test_web_exposes_analysis_running_and_queued(client, monkeypatch):
    """Critères #73 : l'API web expose analysis_running pendant le calcul et
    analysis_queued pour une analyse en file d'attente."""
    _fake_analyze_moves(monkeypatch, delay=0.15)
    gid1 = _game_with_moves(4)
    gid2 = _game_with_moves(4)
    server.analyze_game(gid1, depth=10, publish=True)
    server.analyze_game(gid2, depth=10, publish=False)
    games = client.get("/web/api/games").json()["games"]
    g1 = next(g for g in games if g["game_id"] == gid1)
    g2 = next(g for g in games if g["game_id"] == gid2)
    assert g1["analysis_running"] is True and g1["analysis_queued"] is False
    assert g2["analysis_queued"] is True and g2["analysis_running"] is False
    state = client.get(f"/web/api/games/{gid2}").json()
    assert state["analysis_queued"] is True and state["analysis_running"] is False
    assert _wait_status(gid1)["status"] == "done"
    assert _wait_status(gid2)["status"] == "done"
    games = client.get("/web/api/games").json()["games"]
    g1 = next(g for g in games if g["game_id"] == gid1)
    g2 = next(g for g in games if g["game_id"] == gid2)
    assert g1["analysis_running"] is False and g1["analysed"] is True
    assert g2["analysis_running"] is False and g2["analysis_queued"] is False


def test_jobs_persisted_and_active_becomes_failed_on_restart(monkeypatch, tmp_path):
    """Les jobs sont persistés ; au redémarrage un job running OU queued -> failed."""
    _fake_analyze_moves(monkeypatch, delay=0.02)
    gid = _game_with_moves(3)
    server.analyze_game(gid, depth=8, publish=True, wait=True)
    assert server.analysis_status(gid)["status"] == "done"
    # persistance + rechargement : le job done est restauré
    server.ANALYSIS_JOBS.clear()
    server._load_state()
    assert server.analysis_status(gid)["status"] == "done"

    # un job running au moment du "redémarrage" -> failed au rechargement
    gid2 = _game_with_moves(2)
    _fake_analyze_moves(monkeypatch, delay=0.3)
    server.analyze_game(gid2, depth=8, publish=False)
    assert server.analysis_status(gid2)["status"] == "running"
    server.ANALYSIS_JOBS.clear()
    server._ANALYSIS_QUEUE.clear()
    server._load_state()
    out = server.analysis_status(gid2)
    assert out["status"] == "failed" and "redémarrage" in out["error"]

    # un job QUEUED au moment du "redémarrage" -> failed aussi (complément #73)
    gid3 = _game_with_moves(2)
    gid4 = _game_with_moves(2)
    _fake_analyze_moves(monkeypatch, delay=0.3)
    server.analyze_game(gid3, depth=8, publish=False)   # running
    server.analyze_game(gid4, depth=8, publish=False)   # queued derrière
    assert server.analysis_status(gid4)["status"] == "queued"
    server.ANALYSIS_JOBS.clear()
    server._ANALYSIS_QUEUE.clear()
    server._load_state()
    out = server.analysis_status(gid4)
    assert out["status"] == "failed" and "redémarrage" in out["error"]
