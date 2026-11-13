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


STOCKFISH_CANDIDATES = [
    "/usr/games/stockfish",   # Debian/Ubuntu (hors PATH par défaut)
    "/usr/bin/stockfish",
    "/usr/local/bin/stockfish",
    "/opt/homebrew/bin/stockfish",   # macOS (Homebrew)
]


def stockfish_path() -> str | None:
    env_path = os.environ.get("LICHESS_MCP_STOCKFISH", "").strip()
    if env_path:
        return env_path if os.path.isfile(env_path) else None
    found = shutil.which("stockfish")
    if found:
        return found
    for candidate in STOCKFISH_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


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


def _pv_to_san(board: chess.Board, pv: list[chess.Move], max_plies: int = 6) -> list[str]:
    """Convertit la ligne principale Stockfish en SAN (continuation recommandée)."""
    pv_san = []
    pv_board = board.copy(stack=False)
    for pv_move in pv[:max_plies]:
        try:
            pv_san.append(pv_board.san(pv_move))
            pv_board.push(pv_move)
        except ValueError:
            break
    return pv_san


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
    evals: list[dict] = []
    errors_white = errors_black = 0

    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        for move in moves:
            mover = board.turn
            info = engine.analyse(board, chess.engine.Limit(depth=depth))
            score_before = _pov_score_cp(info, mover)
            pv = info.get("pv") or []
            best_san = board.san(pv[0]) if pv else None
            pv_san = _pv_to_san(board, pv)
            san = board.san(move)
            board.push(move)

            if board.is_checkmate():
                # Le camp qui vient de jouer mate : pas d'analyse nécessaire,
                # et la convention UCI « mate 0 » rend le signe ambigu.
                score_after = 100000 - 100
            else:
                info_after = engine.analyse(board, chess.engine.Limit(depth=depth))
                score_after = _pov_score_cp(info_after, mover)
            score_white = score_after if mover == chess.WHITE else -score_after
            delta = score_after - score_before

            node = node.add_variation(move)
            comment = f"[%eval {_eval_str(score_after)}]"
            klass = _classify(delta)
            label = None
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
            evals.append({
                "san": san,
                "move_number": board.fullmove_number,
                "side": "white" if mover == chess.WHITE else "black",
                "eval_white": score_white,
                "label": label,
                "best_move": best_san if label else None,
                "pv_san": pv_san,
            })

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
        "evals": evals,
    }
