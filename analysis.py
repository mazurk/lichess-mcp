"""Analyse de parties avec Stockfish : évaluations, blunders et PGN annoté.

Nécessite un binaire `stockfish` dans le PATH (installé automatiquement dans
l'image Docker, ou chemin via LICHESS_MCP_STOCKFISH). Retourne un PGN annoté
compatible Lichess (commentaires `[%eval ...]`, NAGs pour les erreurs) ainsi
qu'un résumé des coups critiques.
"""

from __future__ import annotations

import os
import shutil

import chess
import chess.engine
import chess.pgn

BLUNDER_THRESHOLD = 300
MISTAKE_THRESHOLD = 150
INACCURACY_THRESHOLD = 60


def stockfish_path() -> str | None:
    return os.environ.get("LICHESS_MCP_STOCKFISH") or shutil.which("stockfish")


def _pov_score_cp(info: dict, mover: chess.Color) -> int:
    """Score d'une position du point de vue du camp `mover`, en centipions."""
    score = info["score"].pov(mover)
    if score.is_mate():
        mate = score.mate()
        return 100000 - mate * 100 if mate > 0 else -100000 - mate * 100
    return score.score()


def _classify(delta: int) -> tuple[int, str] | None:
    """Classification d'un coup selon la chute d'évaluation (centipions)."""
    if delta <= -BLUNDER_THRESHOLD:
        return (chess.pgn.NAG_BLUNDER, "blunder")
    if delta <= -MISTAKE_THRESHOLD:
        return (chess.pgn.NAG_MISTAKE, "mistake")
    if delta <= -INACCURACY_THRESHOLD:
        return (chess.pgn.NAG_SPECULATIVE_MOVE, "inaccuracy")
    return None


def _eval_str(score: int) -> str:
    if score >= 90000:
        return f"M{100000 - score}"
    if score <= -90000:
        return f"-M{100000 + score}"
    return f"{score / 100:.2f}"


def analyze_moves(moves: list[chess.Move], depth: int = 15) -> dict:
    """Analyse une séquence de coups depuis la position initiale.

    Retourne le PGN annoté, un résumé par camp et la liste des coups critiques.
    """
    path = stockfish_path()
    if not path:
        raise ValueError(
            "Stockfish n'est pas installé. Installez-le (apt install stockfish) "
            "ou définissez LICHESS_MCP_STOCKFISH vers le binaire."
        )
    depth = max(1, min(int(depth), 30))

    game = chess.pgn.Game()
    game.headers["Event"] = "Analyse lichess-mcp"
    node = game
    board = chess.Board()
    critical: list[dict] = []
    errors_white = errors_black = 0

    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        for move in moves:
            mover = board.turn
            info = engine.analyse(board, chess.engine.Limit(depth=depth))
            score_before = _pov_score_cp(info, mover)
            pv = info.get("pv") or []
            best_san = board.san(pv[0]) if pv else None
            san = board.san(move)
            board.push(move)

            info_after = engine.analyse(board, chess.engine.Limit(depth=depth))
            score_after = _pov_score_cp(info_after, mover)
            delta = score_after - score_before

            node = node.add_variation(move)
            comment = f"[%eval {_eval_str(score_after)}]"
            klass = _classify(delta)
            if klass:
                nag, label = klass
                node.nags.add(nag)
                comment += f" {label}"
                if best_san:
                    comment += f", meilleur coup : {best_san}"
                critical.append({
                    "move": san,
                    "move_number": board.fullmove_number,
                    "side": "white" if mover == chess.WHITE else "black",
                    "label": label,
                    "eval": _eval_str(score_after),
                    "best_move": best_san,
                    "eval_loss": round(delta / 100, 2),
                })
                if mover == chess.WHITE:
                    errors_white += 1
                else:
                    errors_black += 1
            node.comment = comment

    return {
        "pgn": str(game),
        "summary": {
            "moves": len(moves),
            "depth": depth,
            "critical_moves": len(critical),
            "white_errors": errors_white,
            "black_errors": errors_black,
        },
        "critical": critical,
    }
