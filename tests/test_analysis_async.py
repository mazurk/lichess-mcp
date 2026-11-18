"""Tests de l'analyse asynchrone (issue #73).

`analyze_game` rend la main immédiatement (status "running") sauf wait=True ;
`analysis_status` suit la progression jusqu'à done/failed ; un second appel
pendant le calcul renvoie already_running ; l'API web expose analysis_running ;
les jobs sont persistés (un job "running" au redémarrage passe "failed").

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
    yield
    server.GAMES.clear()
    server.ACTIVE_PUZZLES.clear()
    server.CHEAT_ALERTS.clear()
    server.ANALYSIS_JOBS.clear()


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


def _fake_analyze_moves(monkeypatch, delay: float = 0.05, fail: bool = False):
    def fake(moves, depth=15, root=None, move_clocks=None, *, white_name=None,
             black_name=None, result=None, date=None, headers=None, progress_cb=None):
        for i in range(len(moves)):
            if fail and i == max(1, len(moves) // 2):
                raise RuntimeError("stockfish crash simulé")
            time.sleep(delay)
            if progress_cb is not None:
                progress_cb(i + 1, len(moves))
        return {"pgn": "1. e4 e5 2. Nf3", "evals": [{"i": i} for i in range(len(moves))],
                "critical": [], "summary": {"white": {}, "black": {}}}
    monkeypatch.setattr(server, "analyze_moves", fake)


def _wait_done(gid: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = server.analysis_status(gid)
        if out["status"] in ("done", "failed"):
            return out
        time.sleep(0.02)
    raise AssertionError(f"analyse {gid} toujours running après {timeout}s")


def test_analyze_game_returns_immediately_then_done(monkeypatch):
    """Critère #73 : analyze_game rend la main en < 2 s avec status running ;
    analysis_status montre la progression puis done avec la réponse complète."""
    _fake_analyze_moves(monkeypatch, delay=0.15)
    gid = _game_with_moves(4)
    t0 = time.time()
    r = server.analyze_game(gid, depth=14, publish=False)
    dt = time.time() - t0
    assert dt < 2.0, f"analyze_game a bloqué {dt:.1f}s"
    assert r["status"] == "running" and r["analysis_id"] == gid
    assert r["moves"] == 4 and r["depth"] == 14
    out = _wait_done(gid)
    assert out["status"] == "done"
    assert out["progress"] == out["total"] == 4
    assert "pgn" in out and "evals" in out  # réponse complète
    assert out["elapsed_s"] >= 0.5  # le calcul a bien pris du temps


def test_already_running_no_second_stockfish(monkeypatch):
    """Un second appel pendant le calcul -> already_running (pas de 2e moteur)."""
    _fake_analyze_moves(monkeypatch, delay=0.2)
    gid = _game_with_moves(4)
    server.analyze_game(gid, depth=10, publish=False)
    r2 = server.analyze_game(gid, depth=10, publish=False)
    assert r2["status"] == "running" and r2.get("already_running") is True
    assert _wait_done(gid)["status"] == "done"


def test_wait_true_returns_full_result(monkeypatch):
    """wait=True : comportement bloquant historique, réponse complète."""
    _fake_analyze_moves(monkeypatch, delay=0.02)
    gid = _game_with_moves(3)
    r = server.analyze_game(gid, depth=8, publish=False, wait=True)
    assert r["status"] == "done" and "pgn" in r


def test_failed_job_reports_error(monkeypatch):
    _fake_analyze_moves(monkeypatch, delay=0.02, fail=True)
    gid = _game_with_moves(4)
    server.analyze_game(gid, depth=10, publish=False)
    out = _wait_done(gid)
    assert out["status"] == "failed"
    assert "crash simulé" in out["error"]


def test_analysis_status_none_then_done_from_published(monkeypatch):
    gid = _game_with_moves(2)
    assert server.analysis_status(gid)["status"] == "none"
    # analyse publiée (session antérieure) -> status done
    server.GAMES[gid]["analysis"] = {"depth": 9, "evals": [], "at": 1.0}
    out = server.analysis_status(gid)
    assert out["status"] == "done" and out["depth"] == 9 and out["published"] is True


def test_web_exposes_analysis_running(client, monkeypatch):
    """Critère #73 : l'API web expose analysis_running pendant le calcul."""
    _fake_analyze_moves(monkeypatch, delay=0.15)
    gid = _game_with_moves(4)
    server.analyze_game(gid, depth=10, publish=True)
    games = client.get("/web/api/games").json()["games"]
    g = next(g for g in games if g["game_id"] == gid)
    assert g["analysis_running"] is True and g["analysed"] is False
    state = client.get(f"/web/api/games/{gid}").json()
    assert state["analysis_running"] is True
    out = _wait_done(gid)
    assert out["status"] == "done" and out.get("published") is True
    games = client.get("/web/api/games").json()["games"]
    g = next(g for g in games if g["game_id"] == gid)
    assert g["analysis_running"] is False and g["analysed"] is True


def test_jobs_persisted_and_running_becomes_failed_on_restart(monkeypatch, tmp_path):
    """Les jobs sont persistés ; au redémarrage un job running -> failed."""
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
    server._load_state()
    out = server.analysis_status(gid2)
    assert out["status"] == "failed" and "redémarrage" in out["error"]
