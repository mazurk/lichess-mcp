"""Serveur MCP d'échecs hors ligne, inspiré de Lichess.

Aucune connexion à lichess.org : les parties se jouent en local contre un
moteur intégré (ou Stockfish s'il est installé), et les puzzles suivent le
format Lichess (FEN + coups UCI).
"""

from __future__ import annotations

import json
import os
import random
import uuid
from pathlib import Path

import chess
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier

from engine import Engine


class StaticTokenVerifier(TokenVerifier):
    """Vérificateur de token statique (Authorization: Bearer <token>)."""

    def __init__(self, token: str, **kwargs):
        super().__init__(**kwargs)
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token == self._token:
            return AccessToken(token=token, client_id="local-agent", scopes=[])
        return None


def _build_auth() -> TokenVerifier | None:
    token = os.environ.get("LICHESS_MCP_AUTH_TOKEN", "").strip()
    return StaticTokenVerifier(token) if token else None


mcp = FastMCP("lichess-mcp", auth=_build_auth())

GAMES: dict[str, dict] = {}
PUZZLES: list[dict] = json.loads(
    (Path(__file__).parent / "puzzles.json").read_text(encoding="utf-8")
)
ACTIVE_PUZZLES: dict[str, dict] = {}


def _board_str(board: chess.Board) -> str:
    lines = [f"  +{'-'*16}+"]
    for rank in range(7, -1, -1):
        row = " ".join(
            (board.piece_at(chess.square(f, rank)).symbol()
             if board.piece_at(chess.square(f, rank)) else ".")
            for f in range(8)
        )
        lines.append(f"{rank + 1} |{row}|")
    lines.append("  +----------------+")
    lines.append("    a b c d e f g h")
    return "\n".join(lines)


def _game_over_info(board: chess.Board, player_color: str) -> str:
    if board.is_checkmate():
        loser = "white" if board.turn == chess.WHITE else "black"
        winner = "black" if loser == "white" else "white"
        return f"Mat ! Les {winner} gagnent."
    if board.is_stalemate():
        return "Pat ! Partie nulle."
    if board.is_insufficient_material():
        return "Nulle : matériel insuffisant."
    if board.is_fifty_moves():
        return "Nulle : règle des 50 coups."
    if board.is_repetition(3):
        return "Nulle : répétition triple."
    return ""


def _parse_move(board: chess.Board, move: str) -> chess.Move | None:
    for candidate in (board.parse_san, board.parse_uci):
        try:
            return candidate(move)
        except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError):
            continue
    return None


def _get_game(game_id: str) -> dict:
    if game_id not in GAMES:
        raise ValueError(f"Partie inconnue : {game_id}")
    return GAMES[game_id]


def _engine_reply(game: dict) -> str | None:
    board = game["board"]
    if board.is_game_over():
        return None
    move = game["engine"].choose_move(board)
    if move is None:
        return None
    san = board.san(move)
    board.push(move)
    return san


@mcp.tool()
def new_game(color: str = "white", level: str = "medium") -> dict:
    """Démarre une nouvelle partie contre le moteur local.

    Args:
        color: votre couleur, "white" ou "black" (vous jouez ce camp).
        level: difficulté du moteur, "easy", "medium" ou "hard".
    """
    color = color.lower()
    if color not in ("white", "black"):
        raise ValueError("color doit valoir 'white' ou 'black'.")
    level = level.lower()
    if level not in ("easy", "medium", "hard"):
        raise ValueError("level doit valoir 'easy', 'medium' ou 'hard'.")

    game_id = uuid.uuid4().hex[:8]
    game = {
        "board": chess.Board(),
        "player_color": color,
        "level": level,
        "engine": Engine(level),
        "resigned": False,
    }
    GAMES[game_id] = game

    result = {
        "game_id": game_id,
        "your_color": color,
        "level": level,
        "board": _board_str(game["board"]),
        "fen": game["board"].fen(),
        "your_turn": color == "white",
    }
    if color == "black":
        reply = _engine_reply(game)
        result["engine_move"] = reply
        result["board"] = _board_str(game["board"])
        result["fen"] = game["board"].fen()
        result["your_turn"] = True
    return result


@mcp.tool()
def get_board(game_id: str) -> dict:
    """Affiche l'échiquier actuel (ASCII), le FEN et le trait."""
    game = _get_game(game_id)
    board = game["board"]
    return {
        "board": _board_str(board),
        "fen": board.fen(),
        "your_turn": board.turn == (
            chess.WHITE if game["player_color"] == "white" else chess.BLACK
        ),
        "in_check": board.is_check(),
        "move_number": board.fullmove_number,
    }


@mcp.tool()
def legal_moves(game_id: str) -> dict:
    """Liste tous les coups légaux au format SAN."""
    game = _get_game(game_id)
    if game["board"].is_game_over():
        return {"moves": [], "note": "La partie est terminée."}
    return {"moves": [game["board"].san(m) for m in game["board"].legal_moves]}


@mcp.tool()
def play_move(game_id: str, move: str) -> dict:
    """Joue votre coup (SAN ou UCI), puis le moteur répond automatiquement.

    Args:
        game_id: identifiant de la partie.
        move: votre coup, ex. "e4", "Nf3", "O-O", "e2e4" ou "e7e8q".
    """
    game = _get_game(game_id)
    board = game["board"]
    if game["resigned"]:
        raise ValueError("La partie est terminée (abandon).")
    if board.is_game_over():
        raise ValueError("La partie est déjà terminée.")
    if board.turn != (chess.WHITE if game["player_color"] == "white" else chess.BLACK):
        raise ValueError("Ce n'est pas votre tour.")

    parsed = _parse_move(board, move)
    if parsed is None or parsed not in board.legal_moves:
        raise ValueError(f"Coup illégal ou invalide : {move}")

    san = board.san(parsed)
    board.push(parsed)
    reply = _engine_reply(game)

    result = {
        "your_move": san,
        "engine_move": reply,
        "board": _board_str(board),
        "fen": board.fen(),
        "your_turn": not board.is_game_over() and not game["resigned"],
        "in_check": board.is_check(),
    }
    end = _game_over_info(board, game["player_color"])
    if end:
        result["game_over"] = end
    return result


@mcp.tool()
def undo(game_id: str) -> dict:
    """Annule votre dernier coup et la réponse du moteur."""
    game = _get_game(game_id)
    board = game["board"]
    for _ in range(2):
        if board.move_stack:
            board.pop()
    return {
        "board": _board_str(board),
        "fen": board.fen(),
        "your_turn": True,
    }


@mcp.tool()
def resign(game_id: str) -> dict:
    """Abandonne la partie."""
    game = _get_game(game_id)
    game["resigned"] = True
    return {"game_over": "Vous avez abandonné. Le moteur gagne."}


@mcp.tool()
def game_status(game_id: str) -> dict:
    """Statut de la partie : trait, numéro de coup, fin éventuelle."""
    game = _get_game(game_id)
    board = game["board"]
    status = {
        "your_color": game["player_color"],
        "level": game["level"],
        "move_number": board.fullmove_number,
        "moves_played": len(board.move_stack),
        "in_check": board.is_check(),
        "over": game["resigned"] or board.is_game_over(),
    }
    if game["resigned"]:
        status["result"] = "Abandon du joueur."
    elif board.is_game_over():
        status["result"] = _game_over_info(board, game["player_color"])
    return status


@mcp.tool()
def next_puzzle() -> dict:
    """Tire un puzzle au format Lichess : trouvez le meilleur coup (mat en 1)."""
    puzzle = random.choice(PUZZLES)
    board = chess.Board(puzzle["FEN"])
    puzzle_id = puzzle["PuzzleId"]
    ACTIVE_PUZZLES[puzzle_id] = puzzle
    return {
        "puzzle_id": puzzle_id,
        "theme": puzzle["Theme"],
        "board": _board_str(board),
        "fen": puzzle["FEN"],
        "side_to_play": "white" if board.turn == chess.WHITE else "black",
        "hint": "Trouvez le coup gagnant (mat en 1).",
    }


@mcp.tool()
def try_puzzle_move(puzzle_id: str, move: str) -> dict:
    """Propose une solution pour un puzzle tiré avec next_puzzle.

    Args:
        puzzle_id: identifiant du puzzle.
        move: coup proposé, en SAN ou UCI.
    """
    if puzzle_id not in ACTIVE_PUZZLES:
        raise ValueError(f"Puzzle inconnu : {puzzle_id}")
    puzzle = ACTIVE_PUZZLES[puzzle_id]
    board = chess.Board(puzzle["FEN"])
    parsed = _parse_move(board, move)
    if parsed is None or parsed not in board.legal_moves:
        return {"correct": False, "note": f"Coup invalide ou illégal : {move}"}
    if parsed.uci() == puzzle["Moves"]:
        return {
            "correct": True,
            "note": f"Bravo ! {board.san(parsed)} est la solution ({puzzle['Theme']}).",
        }
    return {
        "correct": False,
        "note": f"{board.san(parsed)} n'est pas la solution. Réessayez.",
        "hint": "Cherchez un mat en un coup.",
    }


if __name__ == "__main__":
    transport = os.environ.get("LICHESS_MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "http":
        host = os.environ.get("LICHESS_MCP_HOST", "127.0.0.1").strip()
        port = int(os.environ.get("LICHESS_MCP_PORT", "8000").strip())
        mcp.run(transport="http", host=host, port=port)
    elif transport == "stdio":
        mcp.run()
    else:
        raise ValueError(
            f"LICHESS_MCP_TRANSPORT invalide : {transport!r} (valeurs : stdio, http)"
        )
