"""Analyse de parties avec Stockfish : évaluations, blunders et PGN annoté.

Nécessite un binaire `stockfish` dans le PATH (installé automatiquement dans
l'image Docker, ou chemin via LICHESS_MCP_STOCKFISH). Retourne un PGN annoté
compatible Lichess (commentaires `[%eval ...]`, NAGs pour les erreurs) ainsi
qu'un résumé des coups critiques.
"""

from __future__ import annotations

import os
import shutil
import time

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


def engine_options(depth: int | None = None) -> dict:
    """Options UCI de Stockfish : Threads et Hash (issues #54 et #63).

    Par défaut, le process démarre avec 1 seul thread — sur une machine
    multi-cœur (ex. Raspberry Pi 4 cœurs), l'analyse était ~3x plus lente que
    nécessaire. Threads = min(cœurs, 4) par défaut, Hash = 256 Mo ;
    surcharge possible via LICHESS_MCP_STOCKFISH_THREADS / _HASH.

    Règle (demandée par Nicolas) : **pas de multi-threads si depth <= 10** —
    à faible profondeur, l'overhead de synchronisation des threads coûte plus
    cher que le calcul lui-même, donc on force Threads=1. Au-delà
    (depth > 10), on exploite les cœurs (min(cœurs, 4)).
    """
    def env_int(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        try:
            return max(1, int(raw)) if raw else default
        except ValueError:
            return default

    cpus = os.cpu_count() or 1
    default_threads = 1 if (depth is not None and depth <= 10) else min(cpus, 4)
    return {
        "Threads": env_int("LICHESS_MCP_STOCKFISH_THREADS", default_threads),
        "Hash": env_int("LICHESS_MCP_STOCKFISH_HASH", 256),
    }


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


def _clk_str(ms: int) -> str:
    """Temps restant au format Lichess `[%clk]` : H:MM:SS."""
    total_sec = max(0, int(ms)) // 1000
    h, rem = divmod(total_sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _set_pgn_headers(game: chess.pgn.Game, *, white_name: str | None,
                     black_name: str | None, result: str | None, date: str | None,
                     headers: dict | None) -> None:
    """Renseigne les en-têtes du PGN exporté (White/Black/Result/Date...).

    `headers` (en-têtes d'un PGN source) est prioritaire ; les valeurs par
    défaut python-chess ("?", "????.??.??") sont remplacées.
    """
    if headers:
        for key, value in headers.items():
            game.headers[key] = str(value)
    if white_name is not None:
        game.headers["White"] = str(white_name)
    if black_name is not None:
        game.headers["Black"] = str(black_name)
    if result is not None:
        game.headers["Result"] = str(result)
    if date is not None:
        game.headers["Date"] = str(date)
    if "?" in str(game.headers.get("Event") or ""):
        game.headers["Event"] = "Analyse lichess-mcp"
    if "?" in str(game.headers.get("Site") or ""):
        game.headers["Site"] = "lichess-mcp (local)"
    if "?" in str(game.headers.get("Date") or ""):
        game.headers["Date"] = time.strftime("%Y.%m.%d", time.gmtime())


def _annotate_move(node, move: chess.Move, *, san: str, board: chess.Board,
                   mover: chess.Color, score_after: int, delta: int,
                   best_san: str | None, pv_san: list[str],
                   move_clock: dict | None, critical: list[dict]):
    """Annote un coup dans le PGN (eval, clk, NAG/label) et classe le delta.

    Retourne (nouveau node, label, entrée pour `evals`, camp en erreur ou None).
    """
    node = node.add_variation(move)
    comment = f"[%eval {_eval_str(score_after)}]"
    if move_clock:
        comment += f" [%clk {_clk_str(move_clock['remaining_ms'])}]"
    klass = _classify(delta)
    label = None
    error_side = None
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
        error_side = "white" if mover == chess.WHITE else "black"
    node.comment = comment
    entry = {
        "san": san,
        "move_number": board.fullmove_number,
        "side": "white" if mover == chess.WHITE else "black",
        "eval_white": score_after if mover == chess.WHITE else -score_after,
        "label": label,
        "best_move": best_san if label else None,
        "pv_san": pv_san,
    }
    return node, label, entry, error_side


def analyze_moves(moves: list[chess.Move], depth: int = 15,
                  root: chess.Board | None = None,
                  move_clocks: list[dict | None] | None = None, *,
                  white_name: str | None = None, black_name: str | None = None,
                  result: str | None = None, date: str | None = None,
                  headers: dict | None = None,
                  progress_cb=None) -> dict:
    """Analyse une séquence de coups depuis une position de départ.

    Par défaut la position initiale ; `root` permet d'analyser depuis un FEN
    arbitraire (échiquiers d'analyse) — sans quoi les coups seraient illégaux
    depuis la position initiale et le protocole moteur échouerait.

    `move_clocks` (optionnel) : relevés d'horloge par coup (champ `clock` de
    l'historique) ; s'ils sont fournis, chaque coup du PGN est annoté
    `[%clk H:MM:SS]` avec le temps restant du camp qui vient de jouer.

    En-têtes du PGN exporté : `headers` (dict) est prioritaire ; sinon White /
    Black / Result / Date sont renseignés depuis `white_name`, `black_name`,
    `result` (1-0, 0-1, 1/2-1/2, *) et `date` (défaut : aujourd'hui, format
    PGN YYYY.MM.DD) — l'export est ainsi directement exploitable.

    `progress_cb(i, n)` (optionnel) est appelé après chaque coup analysé
    (i de 1 à n) — utilisé par le serveur pour suivre une analyse asynchrone.

    Retourne le PGN annoté, un résumé par camp et la liste des coups critiques.
    """
    path = stockfish_path()
    if not path:
        raise ValueError(
            "Stockfish n'est pas installé. Installez-le (apt install stockfish) "
            "ou définissez LICHESS_MCP_STOCKFISH vers le binaire."
        )
    depth = max(1, min(int(depth), 30))
    root = root.copy(stack=False) if root is not None else chess.Board()

    game = chess.pgn.Game()
    game.setup(root)
    _set_pgn_headers(game, white_name=white_name, black_name=black_name,
                     result=result, date=date, headers=headers)
    node = game
    board = root.copy(stack=False)
    critical: list[dict] = []
    evals: list[dict] = []
    errors_white = errors_black = 0

    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        # Threads/Hash dès le spawn, avant tout go depth (issues #54/#63) :
        # mono-thread si depth <= 10, multi-cœurs au-delà.
        engine.configure(engine_options(depth))
        # Analyse de la position initiale (avant le 1er coup) : le résultat
        # « after » de chaque coup est réutilisé comme « before » du suivant
        # (même position, même profondeur) — sinon chaque position était
        # analysée 2 fois, soit ~50 % du temps d'analyse en doublon (issue #63).
        info = engine.analyse(board, chess.engine.Limit(depth=depth))
        for i, move in enumerate(moves):
            mover = board.turn
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
                info_after = None
            else:
                info_after = engine.analyse(board, chess.engine.Limit(depth=depth))
                score_after = _pov_score_cp(info_after, mover)
            # Position suivante = position courante : réutilisée au tour suivant.
            if info_after is not None:
                info = info_after
            delta = score_after - score_before
            move_clock = (move_clocks[i]
                          if move_clocks is not None and i < len(move_clocks) else None)
            node, label, entry, error_side = _annotate_move(
                node, move, san=san, board=board, mover=mover,
                score_after=score_after, delta=delta, best_san=best_san,
                pv_san=pv_san, move_clock=move_clock, critical=critical)
            evals.append(entry)
            if error_side == "white":
                errors_white += 1
            elif error_side:
                errors_black += 1
            if progress_cb is not None:
                progress_cb(i + 1, len(moves))

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
