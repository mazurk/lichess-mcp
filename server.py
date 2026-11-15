"""Serveur MCP d'échecs hors ligne, inspiré de Lichess.

Aucune connexion à lichess.org : les parties se jouent en local. Chaque camp
est identifié par un nom de joueur ("Nicolas", "Agent IA", "Moteur"...).
Le moteur local n'est qu'un joueur optionnel désigné par son camp
(engine_color) ; une partie libre laisse les deux camps joués de l'extérieur
(humains ou agents, quiconque connaît l'identifiant de la partie).
"""

from __future__ import annotations

import io
import json
import os
import random
import re
import time
import uuid
from pathlib import Path

import chess
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, TokenVerifier
import hashlib

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from analysis import analyze_moves
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
# Alertes « triche détectée » : tout appel à analyze_game effectué alors
# qu'une partie ou un puzzle est en cours y est enregistré (alerte globale,
# quel que soit l'échiquier analysé) et affiché sur l'interface web.
CHEAT_ALERTS: list[dict] = []


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


def _game_over_info(board: chess.Board, game: dict) -> str:
    if game["flagged"] is not None:
        loser = game["flagged"]
        winner_color = "black" if loser == "white" else "white"
        winner = game[f"{winner_color}_name"]
        return f"Temps écoulé ! {winner} ({winner_color}) gagne au drapeau."
    if board.is_checkmate():
        loser_color = "white" if board.turn == chess.WHITE else "black"
        winner_color = "black" if loser_color == "white" else "white"
        winner = game[f"{winner_color}_name"]
        return f"Mat ! {winner} ({winner_color}) gagne."
    if board.is_stalemate():
        return "Pat ! Partie nulle."
    if board.is_insufficient_material():
        return "Nulle : matériel insuffisant."
    if board.is_fifty_moves():
        return "Nulle : règle des 50 coups."
    if board.is_repetition(3):
        return "Nulle : répétition triple."
    return ""


def _result_text(game: dict) -> str:
    """Texte de fin de partie — rappelle toujours le nom du vainqueur s'il y en a un."""
    if game["resigned"]:
        loser = game["resigned_by"]
        winner_color = "black" if loser == "white" else "white"
        return (
            f"{game[f'{loser}_name']} ({loser}) abandonne. "
            f"{game[f'{winner_color}_name']} ({winner_color}) gagne."
        )
    return _game_over_info(game["board"], game)


def _winner(game: dict) -> dict | None:
    """Vainqueur structuré {"name", "color"}, ou None (nulle / partie en cours)."""
    board = game["board"]
    if game["resigned"] or game["flagged"] is not None:
        loser = game["resigned_by"] if game["resigned"] else game["flagged"]
        winner_color = "black" if loser == "white" else "white"
    elif board.is_checkmate():
        loser_color = "white" if board.turn == chess.WHITE else "black"
        winner_color = "black" if loser_color == "white" else "white"
    else:
        return None
    return {"name": game[f"{winner_color}_name"], "color": winner_color}


def _pgn_result(game: dict) -> str:
    """Résultat au format PGN (1-0, 0-1, 1/2-1/2, *) pour les en-têtes d'analyse."""
    board = game["board"]
    if game["resigned"] or game["flagged"] is not None:
        loser = game["resigned_by"] if game["resigned"] else game["flagged"]
        return "0-1" if loser == "white" else "1-0"
    if board.is_checkmate():
        return "0-1" if board.turn == chess.WHITE else "1-0"
    if (board.is_stalemate() or board.is_insufficient_material()
            or board.is_fifty_moves() or board.is_repetition(3)):
        return "1/2-1/2"
    return "*"


_SAN_RE = re.compile(
    r"^(?P<piece>[KQRBN])?"
    r"(?P<from_file>[a-h])?(?P<from_rank>[1-8])?"
    r"(?P<capture>x)?"
    r"(?P<to>[a-h][1-8])"
    r"(?:=?(?P<promo>[QRBN]))?"
    r"(?P<suffix>[+#])?$"
)
_UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
_SAN_PIECES = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK,
               "B": chess.BISHOP, "N": chess.KNIGHT}
_SAN_PROMOS = {"Q": chess.QUEEN, "R": chess.ROOK, "B": chess.BISHOP, "N": chess.KNIGHT}


def _match_san(board: chess.Board, text: str) -> list[chess.Move]:
    """Coups légaux correspondant exactement à un SAN (désambiguïté comprise)."""
    m = _SAN_RE.match(text)
    if not m:
        return []
    g = m.groupdict()
    to = chess.parse_square(g["to"])
    promo = _SAN_PROMOS.get(g["promo"])
    piece_type = _SAN_PIECES.get(g["piece"])
    from_file = ord(g["from_file"]) - 97 if g["from_file"] else None
    from_rank = int(g["from_rank"]) - 1 if g["from_rank"] else None
    capture = bool(g["capture"])
    matches = []
    for mv in board.legal_moves:
        if mv.to_square != to:
            continue
        if (mv.promotion or None) != promo:
            continue
        piece = board.piece_at(mv.from_square)
        if piece is None or piece.color != board.turn:
            continue
        if piece_type is not None:
            if piece.piece_type != piece_type:
                continue
        elif piece.piece_type != chess.PAWN:
            continue
        if from_file is not None and chess.square_file(mv.from_square) != from_file:
            continue
        if from_rank is not None and chess.square_rank(mv.from_square) != from_rank:
            continue
        if board.is_capture(mv) != capture:
            continue
        matches.append(mv)
    return matches


def _parse_move(board: chess.Board, move: str) -> chess.Move | None:
    """Convertit un coup (SAN ou UCI) en chess.Move.

    Lève ValueError si le SAN est ambigu (plusieurs coups légaux correspondent
    exactement) : jamais de résolution silencieuse. Retourne None si le coup
    est invalide ou illégal.
    """
    text = str(move).strip()
    if _UCI_RE.match(text):
        try:
            parsed = board.parse_uci(text)
        except ValueError:
            return None
        return parsed if parsed in board.legal_moves else None
    matches = _match_san(board, text)
    if len(matches) > 1:
        sans = sorted(board.san(mv) for mv in matches)
        raise ValueError(
            f"« {text} » est ambigu : {len(matches)} coups correspondent "
            f"({', '.join(sans)}). Précisez la case de départ, ex. « {sans[0]} »."
        )
    if len(matches) == 1:
        return matches[0]
    # repli : SAN gérés par python-chess (roque « O-O », variantes d'écriture)
    try:
        return board.parse_san(text)
    except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError,
            chess.AmbiguousMoveError):
        return None


def _get_game(game_id: str) -> dict:
    if game_id not in GAMES:
        raise ValueError(f"Partie inconnue : {game_id}")
    return GAMES[game_id]


def _color_side(color: str) -> chess.Color:
    return chess.WHITE if color == "white" else chess.BLACK


def _check_color(color: str | None) -> str:
    if color is None:
        raise ValueError("color doit valoir 'white' ou 'black'.")
    color = color.lower()
    if color not in ("white", "black"):
        raise ValueError("color doit valoir 'white' ou 'black'.")
    return color


def _check_level(level: str) -> str:
    level = level.lower()
    if level not in ("easy", "medium", "hard"):
        raise ValueError("level doit valoir 'easy', 'medium' ou 'hard'.")
    return level


def _turn_info(game: dict) -> dict:
    """Informations de trait neutres : qui joue les blancs, les noirs, le trait."""
    board = game["board"]
    return {
        "white_name": game["white_name"],
        "black_name": game["black_name"],
        "turn": "white" if board.turn == chess.WHITE else "black",
        "engine_color": game["engine_color"],
        "kind": game["kind"],
        **_seat_info(game),
    }


def _engine_reply(game: dict) -> str | None:
    board = game["board"]
    if board.is_game_over():
        return None
    engine_color = game["engine_color"]
    _apply_clock_move(game, engine_color)
    move = game["engine"].choose_move(board)
    if move is None:
        return None
    san = board.san(move)
    board.push(move)
    return san


def _side_covered(game: dict, color: str) -> bool:
    """Un camp est « couvert » s'il est occupé par un joueur ou joué par le moteur."""
    return game[f"{color}_seat"] is not None or game["engine_color"] == color


def _both_sides_covered(game: dict) -> bool:
    return _side_covered(game, "white") and _side_covered(game, "black")


def _maybe_start_clock(game: dict) -> None:
    """Démarre l'horloge dès que les deux camps sont couverts (joueur ou moteur).

    Avant cela, aucun temps ne s'écoule : l'horloge ne tourne pas à vide
    en attendant que l'adversaire rejoigne la partie.
    """
    if game["clock_cfg"] is None or game["clock_started"]:
        return
    if _both_sides_covered(game):
        game["clock_started"] = True
        game["turn_started_at"] = time.time()


def _ensure_clock_started(game: dict) -> None:
    """Démarre l'horloge si ce n'est pas fait : au plus tard au premier coup
    (le blocage du 1er coup des blancs garantit que l'autre camp est alors
    couvert, donc ce démarrage n'arrive jamais « à vide »)."""
    _maybe_start_clock(game)
    if (game["clock_cfg"] is not None and not game["clock_started"]
            and not game["board"].move_stack):
        game["clock_started"] = True
        game["turn_started_at"] = time.time()


def _check_first_move_allowed(game: dict, color: str) -> None:
    """Partie chronométrée : le premier coup des blancs exige que le camp noir
    soit déjà couvert (joueur rejoint ou moteur) — sinon l'horloge du joueur
    blanc tournerait seule en attendant l'adversaire."""
    if game["clock_cfg"] is None or game["kind"] != "game":
        return
    if game["board"].move_stack or color != "white":
        return
    if not _side_covered(game, "black"):
        raise ValueError(
            "Le camp black n'a pas encore été rejoint (join_game ou player=...) : "
            "avec une horloge, le premier coup des blancs n'est autorisé que "
            "lorsque les deux camps sont occupés."
        )


def _parse_clock(clock: str | None) -> dict | None:
    """Analyse un réglage d'horloge : None/"infinite" ou "10+0" (minutes+incrément).

    Retourne {"base_ms": ..., "inc_ms": ...} ou None (partie sans horloge).
    """
    if clock is None:
        return None
    value = str(clock).strip().lower()
    if value in ("", "infinite", "infini", "inf", "none"):
        return None
    match = re.match(r"^(\d+)(?:\+(\d+))?$", value)
    if not match:
        raise ValueError(
            "clock doit valoir 'infinite' ou suivre le format 'minutes+incrément' "
            "ex. '10+0' (10 minutes, 0 seconde d'incrément)."
        )
    minutes = int(match.group(1))
    inc = int(match.group(2) or 0)
    if not 1 <= minutes <= 180 or inc > 60:
        raise ValueError("clock : base entre 1 et 180 minutes, incrément max 60 s.")
    return {"base_ms": minutes * 60_000, "inc_ms": inc * 1000}


def _create_game(white_name: str, black_name: str, level: str,
                 engine_color: str | None, kind: str = "game",
                 board: chess.Board | None = None,
                 clock_cfg: dict | None = None,
                 name: str | None = None) -> dict:
    game = {
        "board": board or chess.Board(),
        "white_name": white_name,
        "black_name": black_name,
        "name": name,  # nom lisible (échiquiers d'analyse, puzzles)
        "engine_color": engine_color,
        "level": level,
        "engine": Engine(level) if engine_color else None,
        "resigned": False,
        "resigned_by": None,
        "kind": kind,
        "white_seat": None,
        "black_seat": None,
        "spectators": {},  # nom -> True (nombre illimité, compteur pour l'UI)
        "clock_cfg": clock_cfg,
        "clock_ms": {"white": clock_cfg["base_ms"], "black": clock_cfg["base_ms"]}
        if clock_cfg else None,
        # horloge arrêtée tant que les deux camps ne sont pas couverts
        "clock_started": False,
        "turn_started_at": None if clock_cfg else time.time(),
        "flagged": None,  # camp qui a perdu au temps
        "move_clocks": [],  # temps consommé par coup (debrief)
    }
    _maybe_start_clock(game)
    game_id = uuid.uuid4().hex[:8]
    GAMES[game_id] = game
    return game_id


def _is_over(game: dict) -> bool:
    # Détection centralisée : un drapeau tombé doit être visible de TOUTE
    # lecture d'état (game_status, get_board, list_games, état web, resign...),
    # pas seulement des appels qui pensent à appeler _check_flag.
    _check_flag(game)
    return (game["resigned"] or game["flagged"] is not None
            or game["board"].is_game_over())


def _check_flag(game: dict) -> None:
    """Détecte le drapeau du camp au trait si son temps est épuisé."""
    if game["clock_cfg"] is None or game["flagged"] is not None:
        return
    if game["turn_started_at"] is None:
        return  # horloge pas encore démarrée (adversaire pas arrivé)
    if game["resigned"] or game["board"].is_game_over():
        return
    color = "white" if game["board"].turn == chess.WHITE else "black"
    elapsed_ms = (time.time() - game["turn_started_at"]) * 1000
    if elapsed_ms > game["clock_ms"][color]:
        game["clock_ms"][color] = 0
        game["flagged"] = color


def _apply_clock_move(game: dict, color: str) -> None:
    """Débite l'horloge du camp qui vient de jouer, crédite l'incrément,
    et enregistre le temps consommé (indicateur de debrief)."""
    if game["clock_cfg"] is None:
        game["move_clocks"].append(None)
        return
    if game["turn_started_at"] is None:
        _maybe_start_clock(game)
    elapsed_ms = (0.0 if game["turn_started_at"] is None
                  else (time.time() - game["turn_started_at"]) * 1000)
    remaining = game["clock_ms"][color] - elapsed_ms
    spent = min(elapsed_ms, game["clock_ms"][color])
    if remaining <= 0:
        game["clock_ms"][color] = 0
        game["flagged"] = color
    else:
        game["clock_ms"][color] = remaining + game["clock_cfg"]["inc_ms"]
    game["turn_started_at"] = time.time()
    game["move_clocks"].append({
        "color": color,
        "spent_ms": int(max(0, spent)),
        "remaining_ms": int(max(0, game["clock_ms"][color])),
    })


def _clock_state(game: dict) -> dict | None:
    """Temps restants (ms) au moment de l'appel + camp dont l'horloge tourne."""
    if game["clock_cfg"] is None:
        return None
    _check_flag(game)
    running = None
    if not _is_over(game) and game["turn_started_at"] is not None:
        running = "white" if game["board"].turn == chess.WHITE else "black"
    result = {
        "running": running,
        # False tant qu'un des deux camps n'est pas encore couvert (joueur/moteur)
        "started": game["clock_started"],
        "base_ms": game["clock_cfg"]["base_ms"],
    }
    for color in ("white", "black"):
        remaining = game["clock_ms"][color]
        if running == color:
            remaining -= (time.time() - game["turn_started_at"]) * 1000
        result[f"{color}_ms"] = max(0, int(remaining))
    return result


def _clocks_at_ply(game: dict, ply: int) -> dict | None:
    """Temps restants de chaque camp après le coup `ply` (relecture).

    Pour chaque camp : son dernier relevé parmi les coups 0..ply-1 ; s'il
    n'a pas encore joué, le temps de base de la cadence.
    """
    if game["clock_cfg"] is None:
        return None
    clocks = game["move_clocks"]
    result: dict[str, int | None] = {"white_ms": None, "black_ms": None}
    for color in ("white", "black"):
        for i in range(min(ply, len(clocks)) - 1, -1, -1):
            entry = clocks[i]
            if entry and entry["color"] == color:
                result[f"{color}_ms"] = entry["remaining_ms"]
                break
        if result[f"{color}_ms"] is None:
            result[f"{color}_ms"] = game["clock_cfg"]["base_ms"]
    return result


def _seat_info(game: dict) -> dict:
    return {"white_seat": game["white_seat"], "black_seat": game["black_seat"]}


@mcp.tool()
def new_game(white: str = "Blancs", black: str = "Noirs",
             level: str = "medium", engine_color: str | None = None,
             clock: str | None = None) -> dict:
    """Démarre une nouvelle partie.

    Chaque camp porte un nom de joueur (humain, agent IA, ou "Moteur").
    Quiconque connaît l'identifiant de la partie peut jouer un camp via
    play_move(color=...).

    Args:
        white: nom du joueur des blancs.
        black: nom du joueur des noirs.
        level: difficulté du moteur, "easy", "medium" ou "hard" (ignoré si le
            moteur ne joue pas).
        engine_color: camp joué par le moteur local, "white" ou "black" — il
            répond alors automatiquement à chaque coup adverse. None pour une
            partie libre : les deux camps sont joués de l'extérieur.
        clock: horloge de la partie, "10+0" (10 minutes + 0 s d'incrément)
            ou "infinite"/None pour jouer sans limite de temps. Le temps
            restant est visible via get_board et sur l'interface web ; le
            camp dont le temps s'épuise perd au drapeau. L'horloge ne démarre
            que lorsque les deux camps sont couverts (joueur ayant rejoint via
            join_game, ou moteur) : avec une horloge, le premier coup des
            blancs est refusé tant que le camp noir n'est pas couvert.
    """
    level = _check_level(level)
    if engine_color is not None:
        engine_color = _check_color(engine_color)
    if engine_color == "white":
        white = "Moteur"
    clock_cfg = _parse_clock(clock)

    game_id = _create_game(white, black, level, engine_color, clock_cfg=clock_cfg)
    game = GAMES[game_id]

    result = {
        "game_id": game_id,
        "level": level,
        "clock": _clock_state(game),
        "board": _board_str(game["board"]),
        "fen": game["board"].fen(),
    }
    if engine_color == "white":
        reply = _engine_reply(game)
        result["engine_move"] = reply
        result["board"] = _board_str(game["board"])
        result["fen"] = game["board"].fen()
    result.update(_turn_info(game))
    return result


@mcp.tool()
def new_analysis_board(fen: str | None = None, name: str | None = None) -> dict:
    """Crée un échiquier d'analyse (dry-run) : testez des coups des deux camps.

    Idéal pour explorer une position : fournissez un FEN (ou rien pour la
    position initiale), puis jouez librement avec play_move (les deux couleurs,
    sans moteur), annulez avec undo, consultez legal_moves/get_board, et
    analysez avec analyze_game. Ne pollue pas les parties en cours.

    L'échiquier est visible sur l'interface web (mode Consulter) ; donnez-lui
    un `name` pour le retrouver facilement dans la liste.

    Args:
        fen: position de départ au format FEN (défaut : position initiale).
        name: nom lisible de l'échiquier (ex. "Ouverture française"), affiché
            dans la liste des parties.
    """
    try:
        board = chess.Board(fen) if fen else chess.Board()
    except ValueError as exc:
        raise ValueError(f"FEN invalide : {exc}") from exc
    game_id = _create_game("Blancs", "Noirs", "medium", None,
                           kind="analysis", board=board, name=name)
    game = GAMES[game_id]
    return {
        "game_id": game_id,
        "name": game["name"],
        "board": _board_str(game["board"]),
        "fen": game["board"].fen(),
        **_turn_info(game),
    }


@mcp.tool()
def list_games(kind: str | None = None) -> dict:
    """Liste les parties présentes sur le serveur, sans avoir besoin de leur identifiant.

    Pour chaque partie : son identifiant (à passer à join_game / get_board /
    play_move), les noms des joueurs, le camp joué par le moteur, le type
    ("game" = partie, "analysis" = échiquier d'analyse/dry-run, "puzzle" =
    puzzle publié), le camp au trait, le nombre de coups, le FEN actuel, le
    statut ("en cours" / "terminée"), si elle a été analysée et les camps
    libres/rejoignables. Utile pour découvrir les parties avant de les
    rejoindre, les consulter ou les analyser.

    Args:
        kind: filtre optionnel — "game", "analysis" ou "puzzle". None = tout.
    """
    if kind is not None:
        kind = str(kind).strip().lower()
        if kind not in ("game", "analysis", "puzzle"):
            raise ValueError("kind doit valoir 'game', 'analysis' ou 'puzzle' (ou None).")
    games = []
    for game_id, game in GAMES.items():
        if kind is not None and game["kind"] != kind:
            continue
        board = game["board"]
        analysis = game.get("analysis")
        games.append({
            "game_id": game_id,
            "white_name": game["white_name"],
            "black_name": game["black_name"],
            "engine_color": game["engine_color"],
            "kind": game["kind"],
            "turn": "white" if board.turn == chess.WHITE else "black",
            "plies": len(board.move_stack),
            "fen": board.fen(),
            "status": _game_status(game),
            "analysed": analysis is not None,
            "analysis_depth": analysis["depth"] if analysis else None,
            **_seat_info(game),
        })
    games.sort(key=lambda g: g["game_id"])
    return {"games": games, "count": len(games)}


@mcp.tool()
def join_game(game_id: str, player: str, color: str | None = None,
              spectate: bool = False) -> dict:
    """Rejoint une partie en occupant un camp (premier arrivé, premier servi),
    ou en tant que spectateur.

    La création d'une partie n'attribue aucune place : le premier joueur qui
    rejoint choisit son camp, qui devient alors verrouillé à son nom ; le
    second joueur obtient l'autre. Un même joueur ne peut pas occuper les
    deux camps. Rejoindre à nouveau avec le même nom est idempotent.

    Avec spectate=True, aucune place n'est prise : le joueur est enregistré
    comme spectateur (nombre illimité), visible dans le compteur de l'UI.

    Args:
        game_id: identifiant de la partie.
        player: nom du joueur qui rejoint.
        color: camp souhaité, "white" ou "black" ; None pour le premier camp
            libre. Refusé si le camp est déjà occupé ou joué par le moteur.
        spectate: rejoindre comme spectateur (aucune place, illimité).
    """
    game = _get_game(game_id)
    player = str(player).strip()
    if not player:
        raise ValueError("player doit être un nom non vide.")

    if spectate:
        game["spectators"][player] = True
        return {
            "game_id": game_id,
            "you_are": "spectator",
            "spectators": len(game["spectators"]),
            "board": _board_str(game["board"]),
            "fen": game["board"].fen(),
            **_turn_info(game),
            "note": "Mode spectateur : utilisez get_board pour suivre la partie.",
        }

    for c in ("white", "black"):
        if game[f"{c}_seat"] == player:
            if color is not None and _check_color(color) != c:
                raise ValueError(
                    f"« {player} » occupe déjà le camp {c} et ne peut pas "
                    "occuper les deux camps."
                )
            return {
                "game_id": game_id,
                "your_color": c,
                "note": "camp déjà occupé par ce joueur",
                "board": _board_str(game["board"]),
                "fen": game["board"].fen(),
                **_turn_info(game),
            }

    if color is None:
        free = [
            c for c in ("white", "black")
            if game[f"{c}_seat"] is None and game["engine_color"] != c
        ]
        if not free:
            raise ValueError("Partie complète : les deux camps sont occupés.")
        color = free[0]
    else:
        color = _check_color(color)
        if game["engine_color"] == color:
            raise ValueError(f"Le camp {color} est joué par le moteur.")
        seat = game[f"{color}_seat"]
        if seat is not None:
            raise ValueError(f"Le camp {color} est déjà occupé par « {seat} ».")

    game[f"{color}_seat"] = player
    game[f"{color}_name"] = player
    _maybe_start_clock(game)  # l'horloge démarre quand les deux camps sont couverts
    return {
        "game_id": game_id,
        "your_color": color,
        "board": _board_str(game["board"]),
        "fen": game["board"].fen(),
        **_turn_info(game),
    }


@mcp.tool()
def get_board(game_id: str, ply: int | None = None) -> dict:
    """Affiche l'échiquier (ASCII), le FEN et le trait.

    Args:
        game_id: identifiant de la partie ou de l'échiquier d'analyse.
        ply: navigation dans l'historique — numéro du coup **après lequel**
            afficher la position (1 = après le 1er coup, 0 = position
            initiale). Par défaut (None) : position actuelle. Permet de
            rejouer la partie coup par coup comme sur l'interface web.
            `ply_clocks` donne les temps restants de chaque camp à ce
            moment de la partie (utile pour le debrief chrono).
    """
    game = _get_game(game_id)
    board = game["board"]
    total = len(board.move_stack)
    if ply is None:
        ply = total
    ply = int(ply)
    if ply < 0 or ply > total:
        raise ValueError(f"ply doit valoir entre 0 et {total} (coups joués).")
    # Copie du plateau (conserve le FEN de départ des échiquiers d'analyse),
    # puis dépilage jusqu'au coup demandé — un simple rejouage depuis la
    # position initiale ignorerait le FEN de départ.
    replay = board.copy()
    while len(replay.move_stack) > ply:
        replay.pop()
    result = {
        "board": _board_str(replay),
        "fen": replay.fen(),
        "in_check": replay.is_check(),
        "move_number": replay.fullmove_number,
        "ply": ply,
        "total_moves": total,
        "clock": _clock_state(game),
        # temps restants de chaque camp après le coup `ply` (relecture)
        "ply_clocks": _clocks_at_ply(game, ply),
        # fin de partie explicite (drapeau, abandon, mat...) pour que
        # l'agent qui poll get_board soit informé sans avoir à deviner
        # via l'horloge à 0.
        "over": _is_over(game),
        "result": _result_text(game) or None,
        "winner": _winner(game),
        **_turn_info(game),
        # en relecture, le trait est celui de la position rejouée
        "turn": "white" if replay.turn == chess.WHITE else "black",
    }
    if ply < total:
        next_move = board.move_stack[ply]
        result["note"] = (
            f"Position après le coup {ply} sur {total} "
            f"(relecture). Prochain coup joué : {replay.san(next_move)}. "
            "Jouez avec play_move uniquement sur la position actuelle "
            "(ply={total_moves})."
        )
    return result


@mcp.tool()
def legal_moves(game_id: str) -> dict:
    """Liste tous les coups légaux au format SAN."""
    game = _get_game(game_id)
    if game["board"].is_game_over():
        return {"moves": [], "note": "La partie est terminée."}
    return {"moves": [game["board"].san(m) for m in game["board"].legal_moves]}


@mcp.tool()
def play_move(game_id: str, move: str, color: str | None = None,
              player: str | None = None) -> dict:
    """Joue un coup (SAN ou UCI) pour le camp indiqué.

    Args:
        game_id: identifiant de la partie (ou de l'échiquier d'analyse).
        move: le coup, ex. "e4", "Nf3", "O-O", "e2e4" ou "e7e8q". Un SAN ambigu
            (ex. « Nxd5 » alors que deux cavaliers peuvent prendre en d5) est
            refusé avec la liste des coups possibles — précisez la case de
            départ (ex. « Ncd5 »). Aucun coup n'est jamais résolu silencieusement.
        color: camp qui joue, "white" ou "black". Par défaut, le camp au trait.
            Refusé si c'est le camp joué par le moteur (il joue lui-même).
            Avec une horloge, le premier coup des blancs est refusé tant que
            le camp adverse n'est pas couvert (rejoint ou moteur).
        player: nom du joueur. Si le camp est occupé (join_game), ce nom doit
            correspondre à l'occupant ; si le camp est libre, le passer
            revient à l'occuper. Sans player, un camp libre reste jouable par
            quiconque (partie ouverte).
    """
    game = _get_game(game_id)
    board = game["board"]
    _check_flag(game)
    if game["flagged"] is not None:
        raise ValueError(f"La partie est terminée : {_result_text(game)}")
    if game["resigned"]:
        raise ValueError(f"La partie est terminée : {_result_text(game)}")
    if board.is_game_over():
        raise ValueError(f"La partie est déjà terminée : {_result_text(game)}")

    if color is None:
        color = "white" if board.turn == chess.WHITE else "black"
    color = _check_color(color)
    if game["engine_color"] == color:
        raise ValueError(f"Le camp {color} est joué par le moteur.")
    if board.turn != _color_side(color):
        raise ValueError(f"Ce n'est pas le tour des {color}s.")
    _check_first_move_allowed(game, color)

    seat = game[f"{color}_seat"]
    other = "black" if color == "white" else "white"
    if player is not None:
        player = str(player).strip()
        if game[f"{other}_seat"] == player:
            raise ValueError(
                f"« {player} » occupe déjà le camp {other} et ne peut pas "
                "jouer les deux camps."
            )
        if seat is not None and player != seat:
            raise ValueError(
                f"Le camp {color} est occupé par « {seat} » — rejoignez "
                "l'autre camp avec join_game."
            )
        if seat is None and player:
            game[f"{color}_seat"] = player
            game[f"{color}_name"] = player
            _maybe_start_clock(game)
    elif seat is not None:
        raise ValueError(
            f"Le camp {color} est occupé par « {seat} » — identifiez-vous "
            f"avec player=\"{seat}\" ou rejoignez l'autre camp."
        )

    parsed = _parse_move(board, move)  # lève ValueError si le SAN est ambigu
    if parsed is None or parsed not in board.legal_moves:
        raise ValueError(f"Coup illégal ou invalide : {move}")

    san = board.san(parsed)
    _ensure_clock_started(game)  # au plus tard au premier coup
    board.push(parsed)
    _apply_clock_move(game, color)
    reply = None
    if game["engine_color"] and not board.is_game_over():
        reply = _engine_reply(game)

    result = {
        "move_played": san,
        "color_played": color,
        "engine_move": reply,
        "board": _board_str(board),
        "fen": board.fen(),
        "in_check": board.is_check(),
        **_turn_info(game),
    }
    end = _result_text(game)
    if end:
        result["game_over"] = end
        winner = _winner(game)
        if winner:
            result["winner"] = winner
    return result


@mcp.tool()
def wait_for_turn(game_id: str, color: str | None = None, timeout_seconds: int = 60) -> dict:
    """Attend (long-poll) que ce soit le tour du camp demandé, puis retourne l'état.

    Permet de jouer en temps réel sans signaler ses coups par ailleurs :
    l'appel rend la main dès que le camp demandé a le trait — ou au timeout.

    Args:
        game_id: identifiant de la partie.
        color: camp attendu, "white" ou "black".
        timeout_seconds: durée d'attente maximale (1 à 300 s, défaut 60).
    """
    game = _get_game(game_id)
    board = game["board"]
    color = _check_color(color)
    target = _color_side(color)
    timeout_seconds = max(1, min(int(timeout_seconds), 300))

    deadline = time.monotonic() + timeout_seconds
    while True:
        # détecte la chute du drapeau du camp au trait AVANT de tester la fin :
        # sans cela, un drapeau tombé resterait invisible et l'agent attendrait
        # en boucle des timeouts sans jamais voir la partie terminée.
        _check_flag(game)
        over = _is_over(game)
        if over:
            end = _result_text(game)
            result = {
                "ready": False,
                "game_over": end,
                **_turn_info(game),
            }
            winner = _winner(game)
            if winner:
                result["winner"] = winner
            return result
        if board.turn == target:
            return {
                "ready": True,
                "board": _board_str(board),
                "fen": board.fen(),
                **_turn_info(game),
            }
        if time.monotonic() >= deadline:
            return {
                "ready": False,
                "timed_out": True,
                "board": _board_str(board),
                "fen": board.fen(),
                **_turn_info(game),
            }
        time.sleep(0.5)


@mcp.tool()
def undo(game_id: str) -> dict:
    """Annule le dernier coup (et la réponse du moteur s'il vient de jouer)."""
    game = _get_game(game_id)
    board = game["board"]
    for _ in range(2 if game["engine_color"] else 1):
        if board.move_stack:
            board.pop()
    return {
        "board": _board_str(board),
        "fen": board.fen(),
        **_turn_info(game),
    }


@mcp.tool()
def resign(game_id: str, color: str | None = None, player: str | None = None) -> dict:
    """Abandonne la partie pour un camp ou un joueur — le vainqueur est nommé.

    Args:
        game_id: identifiant de la partie.
        color: camp qui abandonne, "white" ou "black" (défaut : camp au trait).
        player: nom du joueur qui abandonne — résolu en camp via les places
            prises (join_game). Refusé si ce nom n'occupe aucun camp, ou si
            `color` désigne un camp différent.
    """
    game = _get_game(game_id)
    board = game["board"]
    if player is not None:
        player = str(player).strip()
        if not player:
            raise ValueError("player doit être un nom non vide.")
        seats = [c for c in ("white", "black") if game[f"{c}_seat"] == player]
        if not seats:
            raise ValueError(f"« {player} » n'occupe aucun camp de cette partie.")
        if color is not None and _check_color(color) != seats[0]:
            raise ValueError(f"« {player} » occupe le camp {seats[0]}, pas {color}.")
        color = seats[0]
    if color is None:
        color = "white" if board.turn == chess.WHITE else "black"
    color = _check_color(color)
    if _is_over(game):
        raise ValueError(f"La partie est déjà terminée : {_result_text(game)}")
    game["resigned"] = True
    game["resigned_by"] = color
    result = {"game_over": _result_text(game)}
    winner = _winner(game)
    if winner:
        result["winner"] = winner
    return result


@mcp.tool()
def game_status(game_id: str) -> dict:
    """Statut de la partie : joueurs, trait, numéro de coup, fin éventuelle.

    La chute d'un drapeau est détectée à chaque lecture : `over` bascule dès
    que le temps du camp au trait est épuisé, avec `result` et `winner`.
    """
    game = _get_game(game_id)
    board = game["board"]
    status = {
        "level": game["level"],
        "move_number": board.fullmove_number,
        "moves_played": len(board.move_stack),
        "in_check": board.is_check(),
        "over": _is_over(game),
        **_turn_info(game),
    }
    if _is_over(game):
        status["result"] = _result_text(game)
        winner = _winner(game)
        if winner:
            status["winner"] = winner
    return status


@mcp.tool()
def get_moves(game_id: str, from_ply: int | None = None) -> dict:
    """Liste les coups joués d'une partie (ou d'un échiquier d'analyse / puzzle publié).

    Les coups sont renvoyés en SAN (notation standard : « e4 », « Nf3 »,
    « O-O », « exd5 »...) et en UCI (« e2e4 », « g1f3 »...), dans l'ordre joué.
    `from_ply` ne renvoie que la fin de la partie : c'est l'index du premier
    demi-coup renvoyé (0 = depuis le début ; défaut None = tous les coups).

    Args:
        game_id: identifiant de la partie, de l'échiquier d'analyse ou du puzzle publié.
        from_ply: index (0-based) du premier demi-coup à renvoyer, ou None pour tout.
    """
    game = _get_game(game_id)
    board = game["board"]
    moves = list(board.move_stack)
    sans = _san_history(board)
    ucis = [mv.uci() for mv in moves]
    start = 0 if from_ply is None else int(from_ply)
    if start < 0 or start > len(moves):
        raise ValueError(
            f"from_ply doit être entre 0 et {len(moves)} ({len(moves)} demi-coups joués)."
        )
    return {
        "game_id": game_id,
        "plies": len(moves),
        "from_ply": start,
        "count": len(moves) - start,
        "moves": sans[start:],
        "uci": ucis[start:],
    }


@mcp.tool()
def delete_game(game_id: str) -> dict:
    """Supprime définitivement une partie, un échiquier d'analyse ou un puzzle
    publié du serveur (utile pour nettoyer les échiquiers d'analyse créés pour
    tester des coups).

    La partie disparaît de list_games et de l'interface web ; les appels
    ultérieurs sur cet identifiant échouent (« Partie inconnue »).

    Args:
        game_id: identifiant à supprimer (retourné par new_game,
            new_analysis_board, start_puzzle ou list_games).
    """
    game = _get_game(game_id)  # lève ValueError si inconnu
    kind = game["kind"]
    puzzle_id = ((game.get("puzzle") or {}).get("puzzle") or {}).get("PuzzleId")
    was_over = _is_over(game)
    del GAMES[game_id]
    if puzzle_id:
        ACTIVE_PUZZLES.pop(puzzle_id, None)
    return {
        "deleted": game_id,
        "kind": kind,
        "was_over": was_over,
        "remaining": len(GAMES),
    }


def _start_puzzle(puzzle: dict) -> dict:
    """Initialise une session de puzzle (état multi-coups pour les mats en 2)."""
    board = chess.Board(puzzle["FEN"])
    session = {
        "puzzle": puzzle,
        "board": board.copy(stack=False),
        "plies_done": 0,
    }
    ACTIVE_PUZZLES[puzzle["PuzzleId"]] = session
    return session


def _puzzle_progress(game: dict) -> dict | None:
    """Progression d'un puzzle publié (kind="puzzle") — sans la solution."""
    session = game.get("puzzle")
    if not session or "puzzle" not in session:
        return None
    puzzle = session["puzzle"]
    line = puzzle["Moves"].split()
    return {
        "puzzle_id": puzzle["PuzzleId"],
        "theme": puzzle.get("Theme", ""),
        "mate_in": puzzle.get("MateIn", 1),
        "plies_done": session.get("plies_done", 0),
        "total_plies": len(line),
        "solved": bool(session.get("solved", False)),
    }


@mcp.tool()
def next_puzzle() -> dict:
    """Tire un puzzle : trouvez le mat (en 1 ou 2 coups).

    Pour un mat en 2, jouez le coup clé avec try_puzzle_move : la réponse
    de l'adversaire est jouée automatiquement, puis trouvez le coup de mat.
    """
    puzzle = random.choice(PUZZLES)
    session = _start_puzzle(puzzle)
    board = session["board"]
    mate_in = puzzle.get("MateIn", 1)
    hint = ("Trouvez le coup gagnant (mat en 1)."
            if mate_in == 1
            else f"Mat en {mate_in} : trouvez le coup clé, l'adversaire "
                 "répondra, puis donnez le mat.")
    return {
        "puzzle_id": puzzle["PuzzleId"],
        "theme": puzzle["Theme"],
        "mate_in": mate_in,
        "board": _board_str(board),
        "fen": puzzle["FEN"],
        "side_to_play": "white" if board.turn == chess.WHITE else "black",
        "hint": hint,
    }


@mcp.tool()
def list_puzzles(mate_in: int | None = None) -> dict:
    """Liste le catalogue des puzzles disponibles (base locale puzzles.json).

    Donne les identifiants à passer à start_puzzle(puzzle_id=...).

    Args:
        mate_in: filtre optionnel — 1 (mat en 1) ou 2 (mat en 2). None = tous.
    """
    if mate_in is not None:
        mate_in = int(mate_in)
        if mate_in not in (1, 2):
            raise ValueError("mate_in doit valoir 1 ou 2 (ou None).")
    puzzles = [
        {
            "puzzle_id": p["PuzzleId"],
            "theme": p.get("Theme", ""),
            "mate_in": p.get("MateIn", 1),
        }
        for p in PUZZLES
        if mate_in is None or p.get("MateIn", 1) == mate_in
    ]
    return {"count": len(puzzles), "puzzles": puzzles}


@mcp.tool()
def start_puzzle(puzzle_id: str | None = None,
                 name: str | None = None) -> dict:
    """Tire un puzzle et le publie sur un échiquier observable (mode Puzzle).

    Contrairement à next_puzzle (invisible), le puzzle est matérialisé sur le
    serveur : l'interface web peut l'afficher (mode Puzzles) pour observer
    l'agent en train de le résoudre. Jouez ensuite les coups avec
    try_puzzle_move(..., game_id=...) : chaque coup validé apparaît sur
    l'échiquier web, avec la progression du puzzle.

    Args:
        puzzle_id: identifiant d'un puzzle précis (voir list_puzzles ;
            défaut : tirage aléatoire).
        name: nom lisible de l'échiquier (ex. "Puzzle du jour"), affiché dans
            la liste des parties.
    """
    if puzzle_id is not None:
        puzzle = next((p for p in PUZZLES if p["PuzzleId"] == puzzle_id), None)
        if puzzle is None:
            raise ValueError(f"Puzzle inconnu : {puzzle_id}")
    else:
        puzzle = random.choice(PUZZLES)
    board = chess.Board(puzzle["FEN"])
    label = (name or f"Puzzle {puzzle['PuzzleId']}").strip()
    game_id = _create_game("Blancs", "Noirs", "medium", None,
                           kind="puzzle", board=board, name=label)
    game = GAMES[game_id]
    # session de puzzle adossée à l'échiquier : les coups validés sont
    # poussés sur game["board"], visible sur le web en temps réel.
    game["puzzle"] = {"puzzle": puzzle, "board": game["board"], "plies_done": 0}
    mate_in = puzzle.get("MateIn", 1)
    hint = ("Trouvez le coup gagnant (mat en 1)."
            if mate_in == 1
            else f"Mat en {mate_in} : trouvez le coup clé, l'adversaire "
                 "répondra, puis donnez le mat.")
    return {
        "game_id": game_id,
        "puzzle_id": puzzle["PuzzleId"],
        "theme": puzzle["Theme"],
        "mate_in": mate_in,
        "board": _board_str(game["board"]),
        "fen": puzzle["FEN"],
        "side_to_play": "white" if game["board"].turn == chess.WHITE else "black",
        "hint": hint,
        "note": "Puzzle publié : l'interface web peut l'observer (mode "
                 "Puzzles). Jouez avec try_puzzle_move(..., game_id=...).",
    }


@mcp.tool()
def try_puzzle_move(puzzle_id: str, move: str,
                    game_id: str | None = None) -> dict:
    """Propose une solution pour un puzzle tiré avec next_puzzle/start_puzzle.

    Pour un mat en 2 : le coup clé est validé, la réponse de l'adversaire
    est jouée automatiquement, puis le coup de mat est demandé.

    Avec game_id (puzzle publié via start_puzzle), chaque coup validé est
    joué sur l'échiquier du serveur : l'interface web suit la résolution.

    Args:
        puzzle_id: identifiant du puzzle.
        move: coup proposé, en SAN ou UCI.
        game_id: échiquier du puzzle publié (via start_puzzle) ; sans cet
            argument, le puzzle reste invisible (session next_puzzle).
    """
    if game_id is not None:
        game = _get_game(game_id)
        if game.get("kind") != "puzzle" or not game.get("puzzle"):
            raise ValueError(f"{game_id} n'est pas un échiquier de puzzle.")
        session = game["puzzle"]
        if session["puzzle"]["PuzzleId"] != puzzle_id:
            raise ValueError(
                f"Le puzzle de l'échiquier {game_id} est "
                f"{session['puzzle']['PuzzleId']}, pas {puzzle_id}."
            )
    else:
        session = ACTIVE_PUZZLES.get(puzzle_id)
        if session is None or "puzzle" not in session:
            raise ValueError(f"Puzzle inconnu : {puzzle_id}")
    puzzle = session["puzzle"]
    board = session["board"]
    line = puzzle["Moves"].split()
    plies_done = session["plies_done"]

    try:
        parsed = _parse_move(board, move)
    except ValueError as exc:  # SAN ambigu
        return {"correct": False, "note": str(exc)}
    if parsed is None or parsed not in board.legal_moves:
        return {"correct": False, "note": f"Coup invalide ou illégal : {move}"}

    if parsed.uci() != line[plies_done]:
        mate_in = puzzle.get("MateIn", 1)
        hint = ("Cherchez un mat en un coup."
                if mate_in == 1 else "Cherchez le coup clé du mat en 2.")
        return {
            "correct": False,
            "note": f"{board.san(parsed)} n'est pas la solution. Réessayez.",
            "hint": hint,
        }

    san = board.san(parsed)
    board.push(parsed)
    plies_done += 1

    # réponse de l'adversaire : jouée automatiquement
    if plies_done < len(line) and plies_done % 2 == 1:
        reply = chess.Move.from_uci(line[plies_done])
        reply_san = board.san(reply)
        board.push(reply)
        plies_done += 1
        session["plies_done"] = plies_done
        return {
            "correct": True,
            "note": f"{san} ! L'adversaire répond {reply_san}. Trouvez le mat.",
            "board": _board_str(board),
            "fen": board.fen(),
            "mate_in": puzzle.get("MateIn", 1),
        }

    session["plies_done"] = plies_done
    session["solved"] = True
    mate_in = puzzle.get("MateIn", 1)
    return {
        "correct": True,
        "solved": True,
        "note": f"Bravo ! {san} — mat en {mate_in} trouvé ({puzzle['Theme']}).",
    }


def _note_cheat(analyzed_id: str) -> dict | None:
    """Enregistre une alerte « triche détectée » si un analyze_game est lancé
    alors qu'une partie ou un puzzle est en cours — quel que soit l'échiquier
    analysé (même un id différent de la partie en cours)."""
    in_progress = [gid for gid, g in GAMES.items()
                   if g["kind"] in ("game", "puzzle") and not _is_over(g)]
    if not in_progress:
        return None
    alert = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "analyzed": analyzed_id,
        "while_in_progress": in_progress,
    }
    CHEAT_ALERTS.append(alert)
    return alert


@mcp.tool()
def analyze_game(game_id: str, depth: int = 15, publish: bool = True) -> dict:
    """Analyse une partie (ou un échiquier d'analyse) avec Stockfish.

    Retourne un PGN annoté compatible Lichess ([%eval ...], erreurs marquées)
    et la liste des coups critiques (imprécisions, erreurs, blunders).
    Le PGN exporté porte des en-têtes complets : White/Black (noms des joueurs),
    Result (1-0, 0-1, 1/2-1/2 ou *) et Date du jour.

    Avec publish=True (défaut), l'analyse est aussi **publiée sur l'interface
    web** : la page de la partie affiche la jauge d'évaluation et marque les
    coups gaffés (blunder/erreur/imprécision) pour le debrief.

    **Alerte anti-triche** : si une partie ou un puzzle est en cours au moment
    de l'appel (quel que soit l'échiquier analysé), l'appel est enregistré
    comme « triche détectée » (`cheat_detected` dans le résultat) et une
    alerte est affichée sous l'échiquier sur l'interface web.

    Args:
        game_id: identifiant de la partie ou de l'échiquier d'analyse.
        depth: profondeur d'analyse Stockfish (1 à 30, défaut 15).
        publish: publier l'analyse sur l'interface web (défaut True).
    """
    game = _get_game(game_id)
    cheat_alert = _note_cheat(game_id)  # tout appel, même sur un autre échiquier
    moves = list(game["board"].move_stack)
    if not moves:
        raise ValueError("Aucun coup à analyser dans cette partie.")
    # La position racine est le FEN de départ (échiquiers d'analyse inclus) :
    # sans elle, les coups seraient illégaux depuis la position initiale.
    result = analyze_moves(moves, depth, root=game["board"].root(),
                           move_clocks=game["move_clocks"],
                           white_name=game["white_name"],
                           black_name=game["black_name"],
                           result=_pgn_result(game))
    result["game_id"] = game_id
    result["cheat_detected"] = cheat_alert is not None
    if cheat_alert is not None:
        result["cheat_alert"] = cheat_alert
    if publish:
        game["analysis"] = {"depth": depth, "evals": result["evals"]}
        result["published"] = True
    return result


@mcp.tool()
def analyze_pgn(pgn: str, depth: int = 15) -> dict:
    """Analyse une partie au format PGN avec Stockfish.

    Utile pour analyser une partie jouée ailleurs (ex. exportée de lichess.org)
    sans avoir à la rejouer coup par coup.

    Args:
        pgn: la partie au format PGN.
        depth: profondeur d'analyse Stockfish (1 à 30, défaut 15).
    """
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("PGN invalide ou illisible.")
    if game.errors:
        # Localiser le coup fautif : rejouer la ligne principale jusqu'à
        # l'erreur pour donner le numéro de coup, le camp et la position.
        board = game.board()
        legal_plies = 0
        for move in game.mainline_moves():
            board.push(move)
            legal_plies += 1
        move_number = board.fullmove_number
        side = "Blancs" if board.turn == chess.WHITE else "Noirs"
        # message de python-chess tronqué de son suffixe technique
        details = " ; ".join(
            str(e).split(" while parsing")[0] for e in game.errors
        )
        raise ValueError(
            f"PGN rejeté au coup {move_number} ({side} à jouer) après "
            f"{legal_plies} coups légaux — {details}. "
            f"Position au moment du rejet : {board.fen()}"
        )
    moves = list(game.mainline_moves())
    if not moves:
        raise ValueError("Aucun coup trouvé dans ce PGN.")
    # La position racine du PGN (en-tête FEN) doit être transmise : sans elle,
    # les coups seraient rejoués depuis la position initiale. Les en-têtes du
    # PGN source (White, Black, Date, Result...) sont conservés dans l'export.
    headers = {k: str(v) for k, v in game.headers.items()}
    return analyze_moves(moves, depth, root=game.board(), headers=headers)


# ---------------------------------------------------------------------------
# Interface web : plateau interactif jouable dans le navigateur.
# Les parties sont partagées avec les outils MCP : agents et navigateurs voient
# (et peuvent jouer dans) les mêmes parties. Le mode spectateur est géré côté
# client (le navigateur n'envoie simplement aucun coup).
# ---------------------------------------------------------------------------

WEB_DIR = Path(__file__).parent / "web"


def _san_history(board: chess.Board) -> list[str]:
    # rejouer depuis la position racine (FEN de départ des échiquiers
    # d'analyse) — depuis la position initiale standard, les SAN d'un board
    # à FEN personnalisé seraient faux (python-chess génère du garbage sur
    # des coups illégaux dans la position de rejouage).
    replay = board.copy()
    while replay.move_stack:
        replay.pop()
    history = []
    for move in board.move_stack:
        history.append(replay.san(move))
        replay.push(move)
    return history


def _full_history(game: dict) -> list[dict]:
    """Historique complet pour la navigation web : pour chaque coup, son SAN,
    son UCI, le FEN de la position qui en résulte — et le temps consommé
    (horloges), indicateur précieux pour le debrief."""
    board = game["board"]
    # rejouer depuis la position racine (FEN de départ des échiquiers d'analyse)
    replay = board.copy()
    while replay.move_stack:
        replay.pop()
    history = []
    for i, move in enumerate(board.move_stack):
        entry = {
            "san": replay.san(move),
            "uci": move.uci(),
        }
        replay.push(move)
        entry["fen"] = replay.fen()
        clocks = game["move_clocks"]
        if i < len(clocks) and clocks[i] is not None:
            entry["clock"] = clocks[i]
        history.append(entry)
    return history


def _game_status(game: dict) -> str:
    """Statut lisible d'une partie : en cours / terminée / analysée."""
    if _is_over(game):
        return "terminée"
    return "en cours"


def _web_state(game_id: str, game: dict, your_color: str | None) -> dict:
    board = game["board"]
    over = _is_over(game)
    grid = [
        [
            (board.piece_at(chess.square(f, rank)).symbol()
             if board.piece_at(chess.square(f, rank)) else ".")
            for f in range(8)
        ]
        for rank in range(7, -1, -1)
    ]
    result = _result_text(game) if over else ""
    winner = _winner(game) if over else None
    your_turn = (
        not over and your_color is not None
        and board.turn == _color_side(your_color)
    )
    return {
        "game_id": game_id,
        "fen": board.fen(),
        "start_fen": board.root().fen(),
        "board": grid,
        "white_name": game["white_name"],
        "black_name": game["black_name"],
        "engine_color": game["engine_color"],
        "kind": game["kind"],
        "name": game["name"],
        "puzzle": _puzzle_progress(game),
        **_seat_info(game),
        "your_color": your_color,
        "your_turn": your_turn,
        "turn": "white" if board.turn == chess.WHITE else "black",
        "in_check": board.is_check(),
        "over": over,
        "clock": _clock_state(game),
        "result": result,
        "winner": winner,
        # alerte anti-triche globale : tout analyze_game lancé pendant une
        # partie/un puzzle en cours est affiché sous l'échiquier
        "cheat_detected": bool(CHEAT_ALERTS),
        "cheat_alerts": CHEAT_ALERTS[-5:],
        "moves": _san_history(board),
        "history": _full_history(game),
        "status": _game_status(game),
        "analysed": game.get("analysis") is not None,
        "spectators": len(game["spectators"]),
        "legal_moves_uci": [] if over or your_color is None else [m.uci() for m in board.legal_moves],
        "last_move": board.peek().uci() if board.move_stack else None,
        "analysis": game.get("analysis"),
    }


def _web_authorized(request: Request) -> bool:
    token = os.environ.get("LICHESS_MCP_AUTH_TOKEN", "").strip()
    if not token:
        return True
    return request.headers.get("authorization", "") == f"Bearer {token}"


_SECURITY_HEADERS = {
    # Empêche l'exécution de scripts externes et limite l'impact d'une XSS
    # résiduelle : seuls les scripts/styles du serveur et les data-URI
    # (pièces SVG) sont autorisés.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


_WEB_VERSION_CACHE: dict[str, str] = {}


def _web_version() -> str:
    """Empreinte du contenu des scripts web, calculée au premier accès.

    Toute modification de util.js/pieces.js/app.js change l'empreinte, donc
    l'URL versionnée (?v=...) référencée par la page : les navigateurs
    rechargent automatiquement les scripts après un déploiement, sans
    manipulation manuelle de version.
    """
    if "v" not in _WEB_VERSION_CACHE:
        h = hashlib.sha256()
        for name in ("util.js", "pieces.js", "app.js"):
            path = WEB_DIR / name
            h.update(path.read_bytes() if path.is_file() else name.encode())
        _WEB_VERSION_CACHE["v"] = h.hexdigest()[:10]
    return _WEB_VERSION_CACHE["v"]


def _secure_file_response(path: Path, media_type: str | None = None,
                          cache_control: str = "no-cache") -> FileResponse:
    """Sert un fichier statique avec en-têtes de sécurité.

    Par défaut `Cache-Control: no-cache` : le navigateur doit revalider à
    chaque chargement (l'ETag rend la revalidation gratuite) — sans ça, le
    navigateur peut garder en cache une ancienne version de la page pendant
    des jours après un déploiement (cache heuristique). Les scripts JS sont
    référencés versionnés (?v=N) dans la page : ils peuvent être cachés
    longtemps sans risque.
    """
    headers = dict(_SECURITY_HEADERS)
    headers["Cache-Control"] = cache_control
    return FileResponse(path, media_type=media_type, headers=headers)


@mcp.custom_route("/", methods=["GET"])
async def web_index(request: Request) -> Response:
    index = WEB_DIR / "index.html"
    if not index.is_file():
        return JSONResponse(
            {"error": "web/index.html est absent du déploiement (vérifiez le Dockerfile)"},
            status_code=500,
        )
    content = index.read_text(encoding="utf-8").replace("__BUILD__", _web_version())
    headers = dict(_SECURITY_HEADERS)
    headers["Cache-Control"] = "no-cache"
    # ETag sur le contenu final : la revalidation navigateur est gratuite
    headers["ETag"] = f'"{hashlib.sha256(content.encode()).hexdigest()[:16]}"'
    return Response(content, media_type="text/html", headers=headers)


@mcp.custom_route("/util.js", methods=["GET"])
async def web_util_js(request: Request) -> FileResponse:
    util = WEB_DIR / "util.js"
    if not util.is_file():
        return JSONResponse(
            {"error": "web/util.js est absent du déploiement (vérifiez le Dockerfile)"},
            status_code=500,
        )
    return _secure_file_response(util, "application/javascript",
                                 cache_control="public, max-age=31536000, immutable")


@mcp.custom_route("/app.js", methods=["GET"])
async def web_app_js(request: Request) -> FileResponse:
    app = WEB_DIR / "app.js"
    if not app.is_file():
        return JSONResponse(
            {"error": "web/app.js est absent du déploiement (vérifiez le Dockerfile)"},
            status_code=500,
        )
    return _secure_file_response(app, "application/javascript",
                                 cache_control="public, max-age=31536000, immutable")


@mcp.custom_route("/pieces.js", methods=["GET"])
async def web_pieces_js(request: Request) -> FileResponse:
    pieces = WEB_DIR / "pieces.js"
    if not pieces.is_file():
        return JSONResponse(
            {"error": "web/pieces.js est absent du déploiement (vérifiez le Dockerfile)"},
            status_code=500,
        )
    return _secure_file_response(pieces, "application/javascript",
                                 cache_control="public, max-age=31536000, immutable")


@mcp.custom_route("/web/api/games", methods=["GET"])
async def web_list_games(request: Request) -> JSONResponse:
    """Liste des parties du serveur (menu déroulant de l'interface web)."""
    if not _web_authorized(request):
        return JSONResponse({"error": "non autorisé"}, status_code=401)
    games = []
    for game_id, game in GAMES.items():
        board = game["board"]
        analysis = game.get("analysis")
        games.append({
            "game_id": game_id,
            "white_name": game["white_name"],
            "black_name": game["black_name"],
            "engine_color": game["engine_color"],
            "kind": game["kind"],
            "name": game["name"],
            "plies": len(board.move_stack),
            "status": _game_status(game),
            "analysed": analysis is not None,
            "analysis_depth": analysis["depth"] if analysis else None,
            "puzzle": _puzzle_progress(game),
        })
    games.sort(key=lambda g: g["game_id"])
    return JSONResponse({"games": games})


@mcp.custom_route("/web/api/games", methods=["POST"])
async def web_new_game(request: Request) -> JSONResponse:
    if not _web_authorized(request):
        return JSONResponse({"error": "non autorisé"}, status_code=401)
    body = await request.json()
    your_color = str(body.get("your_color", "white")).lower()
    if your_color not in ("white", "black"):
        return JSONResponse({"error": "your_color doit valoir 'white' ou 'black'"}, status_code=400)
    against_engine = bool(body.get("against_engine", True))
    try:
        level = _check_level(str(body.get("level", "medium")))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    your_name = str(body.get("your_name", "Joueur")).strip() or "Joueur"
    opponent_name = str(body.get("opponent_name", "Adversaire")).strip() or "Adversaire"
    try:
        clock_cfg = _parse_clock(body.get("clock"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    engine_color = None
    if against_engine:
        engine_color = "black" if your_color == "white" else "white"
        opponent_name = "Moteur"

    if your_color == "white":
        white_name, black_name = your_name, opponent_name
    else:
        white_name, black_name = opponent_name, your_name

    game_id = _create_game(white_name, black_name, level, engine_color,
                           clock_cfg=clock_cfg)
    game = GAMES[game_id]
    # le créateur occupe son camp (verrouillé à son nom, comme join_game)
    game[f"{your_color}_seat"] = your_name
    _maybe_start_clock(game)
    if engine_color == "white":
        await run_in_threadpool(_engine_reply, game)
    return JSONResponse(_web_state(game_id, game, your_color))


@mcp.custom_route("/web/api/games/{game_id}", methods=["GET"])
async def web_get_game(request: Request) -> JSONResponse:
    if not _web_authorized(request):
        return JSONResponse({"error": "non autorisé"}, status_code=401)
    game_id = request.path_params["game_id"]
    if game_id not in GAMES:
        return JSONResponse({"error": "partie inconnue"}, status_code=404)
    game = GAMES[game_id]
    your_color = request.query_params.get("your_color")
    if your_color not in ("white", "black"):
        your_color = None
    return JSONResponse(_web_state(game_id, game, your_color))


@mcp.custom_route("/web/api/games/{game_id}/join", methods=["POST"])
async def web_join_game(request: Request) -> JSONResponse:
    if not _web_authorized(request):
        return JSONResponse({"error": "non autorisé"}, status_code=401)
    game_id = request.path_params["game_id"]
    if game_id not in GAMES:
        return JSONResponse({"error": "partie inconnue"}, status_code=404)
    game = GAMES[game_id]
    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "name doit être un nom non vide"}, status_code=400)
    spectate = bool(body.get("spectate", False))
    color = body.get("color") if not spectate else None
    if color is not None:
        color = str(color).lower()
        if color not in ("white", "black"):
            return JSONResponse({"error": "color doit valoir 'white' ou 'black'"}, status_code=400)
        if game["engine_color"] == color:
            return JSONResponse({"error": f"le camp {color} est joué par le moteur"}, status_code=400)
        seat = game[f"{color}_seat"]
        if seat is not None and seat != name:
            return JSONResponse({"error": f"le camp {color} est déjà occupé par {seat}"}, status_code=400)
    try:
        result = join_game(game_id, name, color, spectate=spectate)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    your_color = result.get("your_color")
    state = _web_state(game_id, game, your_color)
    state["joined"] = True
    return JSONResponse(state)


@mcp.custom_route("/web/api/games/{game_id}/move", methods=["POST"])
async def web_play_move(request: Request) -> JSONResponse:
    if not _web_authorized(request):
        return JSONResponse({"error": "non autorisé"}, status_code=401)
    game_id = request.path_params["game_id"]
    if game_id not in GAMES:
        return JSONResponse({"error": "partie inconnue"}, status_code=404)
    game = GAMES[game_id]
    board = game["board"]
    body = await request.json()
    your_color = str(body.get("your_color", "white")).lower()
    if your_color not in ("white", "black"):
        return JSONResponse({"error": "your_color doit valoir 'white' ou 'black'"}, status_code=400)
    _check_flag(game)
    if game["flagged"] is not None:
        return JSONResponse({"error": f"partie terminée : {_result_text(game)}"}, status_code=400)
    if game["resigned"]:
        return JSONResponse({"error": f"partie terminée : {_result_text(game)}"}, status_code=400)
    if board.is_game_over():
        return JSONResponse({"error": f"partie déjà terminée : {_result_text(game)}"}, status_code=400)
    if game["engine_color"] == your_color:
        return JSONResponse({"error": "ce camp est joué par le moteur"}, status_code=400)
    if board.turn != _color_side(your_color):
        return JSONResponse({"error": "ce n'est pas le tour de ce camp"}, status_code=400)
    try:
        _check_first_move_allowed(game, your_color)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    your_name = str(body.get("your_name", "")).strip()
    seat = game[f"{your_color}_seat"]
    other = "black" if your_color == "white" else "white"
    if seat is not None and your_name != seat:
        return JSONResponse({"error": f"le camp {your_color} est occupé par {seat}"}, status_code=400)
    if seat is None and your_name:
        if game[f"{other}_seat"] == your_name:
            return JSONResponse({"error": "un joueur ne peut pas jouer les deux camps"}, status_code=400)
        game[f"{your_color}_seat"] = your_name
        game[f"{your_color}_name"] = your_name
        _maybe_start_clock(game)
    move = str(body.get("move", ""))
    try:
        parsed = _parse_move(board, move)  # lève ValueError si le SAN est ambigu
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if parsed is None or parsed not in board.legal_moves:
        return JSONResponse({"error": f"coup illégal ou invalide : {move}"}, status_code=400)
    _ensure_clock_started(game)
    board.push(parsed)
    _apply_clock_move(game, your_color)
    if game["engine_color"] and not board.is_game_over():
        await run_in_threadpool(_engine_reply, game)
    return JSONResponse(_web_state(game_id, game, your_color))


@mcp.custom_route("/web/api/games/{game_id}/resign", methods=["POST"])
async def web_resign_game(request: Request) -> JSONResponse:
    """Abandon via l'interface web : réservé aux joueurs (pas aux spectateurs)."""
    if not _web_authorized(request):
        return JSONResponse({"error": "non autorisé"}, status_code=401)
    game_id = request.path_params["game_id"]
    if game_id not in GAMES:
        return JSONResponse({"error": "partie inconnue"}, status_code=404)
    game = GAMES[game_id]
    body = await request.json()
    your_color = str(body.get("your_color", "")).lower()
    if your_color not in ("white", "black"):
        return JSONResponse(
            {"error": "your_color doit valoir 'white' ou 'black' (les spectateurs n'abandonnent pas)"},
            status_code=400,
        )
    if game["engine_color"] == your_color:
        return JSONResponse({"error": "ce camp est joué par le moteur"}, status_code=400)
    your_name = str(body.get("your_name", "")).strip()
    seat = game[f"{your_color}_seat"]
    if seat is not None and your_name != seat:
        return JSONResponse({"error": f"le camp {your_color} est occupé par {seat}"}, status_code=400)
    if _is_over(game):
        return JSONResponse(
            {"error": f"la partie est déjà terminée : {_result_text(game)}"}, status_code=400
        )
    game["resigned"] = True
    game["resigned_by"] = your_color
    return JSONResponse(_web_state(game_id, game, your_color))


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
