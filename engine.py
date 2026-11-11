"""Moteur d'échecs local : negamax alpha-beta avec évaluation matérielle.

Si un binaire Stockfish est disponible sur la machine, il est utilisé
automatiquement en priorité ; sinon on retombe sur le moteur intégré.
"""

from __future__ import annotations

import random
import shutil

import chess

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000,
}

CENTER_BONUS = {
    chess.D4: 10, chess.E4: 10, chess.D5: 10, chess.E5: 10,
    chess.C4: 5, chess.F4: 5, chess.C5: 5, chess.F5: 5,
    chess.D3: 5, chess.E3: 5, chess.D6: 5, chess.E6: 5,
}

LEVEL_DEPTH = {"easy": 1, "medium": 2, "hard": 3}


def _stockfish_path() -> str | None:
    return shutil.which("stockfish")


class Engine:
    def __init__(self, level: str = "medium"):
        self.level = level if level in LEVEL_DEPTH else "medium"
        self._stockfish = _stockfish_path()

    def choose_move(self, board: chess.Board) -> chess.Move | None:
        moves = list(board.legal_moves)
        if not moves:
            return None
        if self._stockfish:
            return self._stockfish_move(board, moves)
        return self._builtin_move(board, moves)

    def _stockfish_move(self, board: chess.Board, moves: list[chess.Move]) -> chess.Move:
        import chess.engine

        depth = {"easy": 4, "medium": 8, "hard": 12}[self.level]
        with chess.engine.SimpleEngine.popen_uci(self._stockfish) as eng:
            result = eng.play(board, chess.engine.Limit(depth=depth))
        if result.move in moves:
            return result.move
        return random.choice(moves)

    def _builtin_move(self, board: chess.Board, moves: list[chess.Move]) -> chess.Move:
        if self.level == "easy" and random.random() < 0.35:
            return random.choice(moves)
        depth = LEVEL_DEPTH[self.level]
        best_score = -10**9
        best = moves[0]
        random.shuffle(moves)
        for move in moves:
            board.push(move)
            score = -self._negamax(board, depth - 1, -10**9, 10**9)
            board.pop()
            if score > best_score:
                best_score = score
                best = move
        return best

    def _negamax(self, board: chess.Board, depth: int, alpha: int, beta: int) -> int:
        if board.is_checkmate():
            return -100000 + (10 - depth)
        if board.is_game_over():
            return 0
        if depth == 0:
            return self._evaluate(board)
        best = -10**9
        for move in board.legal_moves:
            board.push(move)
            score = -self._negamax(board, depth - 1, -beta, -alpha)
            board.pop()
            if score > best:
                best = score
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break
        return best

    def _evaluate(self, board: chess.Board) -> int:
        score = 0
        for square, piece in board.piece_map().items():
            value = PIECE_VALUES[piece.piece_type]
            if square in CENTER_BONUS:
                value += CENTER_BONUS[square]
            score += value if piece.color == board.turn else -value
        return score
