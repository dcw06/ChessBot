import chess
import random
from typing import Callable, Optional


class TimePressureHandler:
    """
    Simulates yuandan's bullet time-pressure behavior:

      3-8s  : mostly coherent but elevated temperature, occasional random move
      1-3s  : frequent premoves, sometimes walks into attacked squares
      < 1s  : pure spam — just clicks legal moves in order

    The "premove into promotion" failure: when an opponent promotes, the user's
    queued premove lands on a square now covered by the new queen. We simulate
    this by occasionally picking a move whose destination is attacked.
    """

    def get_move(
        self,
        board: chess.Board,
        clock_remaining: float,
        neural_fn: Callable,
    ) -> chess.Move:
        roll = random.random()

        if clock_remaining < 1.0:
            # True spam: ~70% first-legal, ~30% fully random
            return list(board.legal_moves)[0] if roll < 0.7 else random.choice(list(board.legal_moves))

        if clock_remaining < 3.0:
            if roll < 0.25:
                blunder = self._premove_into_promotion(board)
                if blunder:
                    return blunder
            if roll < 0.50:
                return list(board.legal_moves)[0]   # premove: grab first queued move
            return neural_fn(board, temperature=2.5)

        # 3–8 seconds: panicked but mostly coherent
        if roll < 0.12:
            return random.choice(list(board.legal_moves))
        return neural_fn(board, temperature=1.8)

    def _premove_into_promotion(self, board: chess.Board) -> Optional[chess.Move]:
        """
        Pick a natural-looking move that walks into a square attacked by the opponent —
        especially squares covered by a recently promoted queen.
        Simulates the user premoving without seeing the promotion.
        """
        opponent = not board.turn

        # Prefer blunders into queen-covered squares (promotion simulation)
        def is_queen_covered(sq: int) -> bool:
            for attacker_sq in board.attackers(opponent, sq):
                if board.piece_type_at(attacker_sq) == chess.QUEEN:
                    return True
            return False

        queen_traps = [m for m in board.legal_moves if is_queen_covered(m.to_square)]
        if queen_traps:
            return random.choice(queen_traps)

        # Fallback: any move into an attacked square (simulates generic premove error)
        any_traps = [m for m in board.legal_moves if board.is_attacked_by(opponent, m.to_square)]
        return random.choice(any_traps) if any_traps else None
