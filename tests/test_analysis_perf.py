"""Tests de perf d'analyse (issues #63 et #54).

- `analyze_moves` ne doit analyser chaque position qu'une fois (le résultat
  « after » d'un coup est réutilisé comme « before » du suivant) : N coups ->
  N+1 analyses au lieu de 2N.
- Règle demandée : pas de multi-threads si depth <= 10 (Threads=1), sinon
  min(cœurs, 4).
"""

import os

import chess
import chess.engine
import pytest

import analysis
import engine as engine_mod


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.delenv("LICHESS_MCP_STOCKFISH_THREADS", raising=False)
    monkeypatch.delenv("LICHESS_MCP_STOCKFISH_HASH", raising=False)


@pytest.fixture(autouse=True)
def _reset_stockfish_proc():
    """Isole le process persistant du moteur entre les tests."""
    engine_mod._stockfish_state["proc"] = None
    yield
    engine_mod._stockfish_state["proc"] = None


def test_threads_rule_depth_le_10_is_monothread():
    """depth <= 10 -> Threads=1 (pas de multi-threads)."""
    assert analysis.engine_options(depth=1)["Threads"] == 1
    assert analysis.engine_options(depth=10)["Threads"] == 1


def test_threads_rule_depth_gt_10_is_multicore():
    """depth > 10 -> Threads = min(cœurs, 4) ; sans depth -> multi-cœurs."""
    cpus = os.cpu_count() or 1
    assert analysis.engine_options(depth=11)["Threads"] == min(cpus, 4)
    assert analysis.engine_options(depth=18)["Threads"] == min(cpus, 4)
    assert analysis.engine_options()["Threads"] == min(cpus, 4)
    # Hash par défaut conservé dans tous les cas
    assert analysis.engine_options(depth=5)["Hash"] == 256


class _FakeEngine:
    """Faux process Stockfish : compte les appels à analyse/play."""

    def __init__(self):
        self.analyse_calls = 0
        self.play_calls = 0
        self.opts = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def configure(self, opts):
        self.opts = opts

    def analyse(self, board, limit):
        self.analyse_calls += 1
        return {"score": chess.engine.PovScore(chess.engine.Cp(25), chess.WHITE), "pv": []}

    def play(self, board, limit):
        self.play_calls += 1
        return chess.engine.PlayResult(next(iter(board.legal_moves)), None, None)


def _patch_fake_stockfish(monkeypatch, fake):
    monkeypatch.setattr(analysis, "stockfish_path", lambda: "/fake/stockfish")
    monkeypatch.setattr(
        chess.engine.SimpleEngine, "popen_uci", staticmethod(lambda path: fake)
    )


def test_analyze_moves_analyse_chaque_position_une_seule_fois(monkeypatch):
    """Issue #63 : N coups -> N+1 analyses (avant le fix : 2N)."""
    fake = _FakeEngine()
    _patch_fake_stockfish(monkeypatch, fake)
    moves = [chess.Move.from_uci(u) for u in ("e2e4", "e7e5", "g1f3", "b8c6")]
    result = analysis.analyze_moves(moves, depth=5)
    assert fake.analyse_calls == len(moves) + 1, (
        f"{fake.analyse_calls} analyses pour {len(moves)} coups (attendu {len(moves) + 1})"
    )
    # le résultat reste complet : un eval par coup
    assert len(result["evals"]) == len(moves)
    assert "[%eval" in result["pgn"]
    # et la règle de threads est appliquée (depth 5 <= 10 -> mono-thread)
    assert fake.opts["Threads"] == 1


def test_analyze_moves_depth_18_passe_en_multicore(monkeypatch):
    """depth 18 -> Threads = min(cœurs, 4) sur le process d'analyse."""
    fake = _FakeEngine()
    _patch_fake_stockfish(monkeypatch, fake)
    cpus = os.cpu_count() or 1
    analysis.analyze_moves([chess.Move.from_uci("e2e4")], depth=18)
    assert fake.opts["Threads"] == min(cpus, 4)


def test_engine_passe_son_depth_aux_options(monkeypatch):
    """Le moteur de jeu passe aussi son depth à engine_options (règle #63)."""
    fake = _FakeEngine()
    monkeypatch.setattr(engine_mod, "_stockfish_path", lambda: "/fake/stockfish")
    monkeypatch.setattr(
        chess.engine.SimpleEngine, "popen_uci", staticmethod(lambda path: fake)
    )
    seen = {}
    real_options = analysis.engine_options

    def spy(depth=None):
        seen["depth"] = depth
        return real_options(depth)

    monkeypatch.setattr(analysis, "engine_options", spy)
    eng = engine_mod.Engine("easy")  # easy -> depth 4
    eng.choose_move(chess.Board())
    assert seen["depth"] == 4
    assert fake.opts["Threads"] == 1  # depth 4 <= 10 -> mono-thread
    assert fake.play_calls == 1

    eng_hard = engine_mod.Engine("hard")  # hard -> depth 12
    eng_hard.choose_move(chess.Board())
    assert seen["depth"] == 12
    assert fake.opts["Threads"] == min(os.cpu_count() or 1, 4)


def test_engine_reutilise_un_seul_process_stockfish(monkeypatch):
    """Issue #63 (bonus) : un seul spawn Stockfish pour plusieurs coups du
    moteur, au lieu d'un process par coup."""
    spawns = []

    class FakeProc(_FakeEngine):
        def __init__(self):
            super().__init__()
            spawns.append(1)

    monkeypatch.setattr(engine_mod, "_stockfish_path", lambda: "/fake/stockfish")
    monkeypatch.setattr(
        chess.engine.SimpleEngine, "popen_uci", staticmethod(lambda p: FakeProc())
    )
    eng = engine_mod.Engine("medium")
    for _ in range(3):
        assert eng.choose_move(chess.Board()) is not None
    assert len(spawns) == 1, f"{len(spawns)} spawns pour 3 coups (attendu 1)"


def test_engine_respawn_si_process_mort(monkeypatch):
    """Si le process persistant meurt (EngineError), il est respawné une fois."""
    spawns = []
    state = {"play": 0}

    class DyingProc(_FakeEngine):
        def play(self, board, limit):
            state["play"] += 1
            if state["play"] == 1:
                raise chess.engine.EngineError("process died")
            return super().play(board, limit)

    def fake_popen(path):
        spawns.append(1)
        return DyingProc()

    monkeypatch.setattr(engine_mod, "_stockfish_path", lambda: "/fake/stockfish")
    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", staticmethod(fake_popen))
    eng = engine_mod.Engine("medium")
    move = eng.choose_move(chess.Board())
    assert move is not None
    assert len(spawns) == 2  # 1er process mort -> respawn
