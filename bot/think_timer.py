"""
Human-like think-time simulation.

Decision tree (evaluated in order):
  1. Opening (fullmove ≤ 8) or opening-book move  → quick glance, 1-5 s
  2. Recapture (bot captures on the square opponent just moved to) → instant, 0.5-4 s
  3. Forced / obvious (gap_cp > 350 or forced mate)             → easy, 1-6 s
  4. Moderate complexity (gap_cp 150-350)                        → think, 6-22 s
  5. Complex (gap_cp < 150, many nearly-equal moves)             → long think

For complex positions the first move in a consecutive sequence can take up to
60 s (rapid).  Each subsequent complex move is 10-20 s shorter, simulating
that the player is still within the same calculated line.  A non-complex
move in between resets the counter.

All delays are capped at 20 % of the remaining clock so the bot never
flags on time due to artificial think delays.
"""

import random
import chess
from typing import Optional


# (opening, recapture, simple, moderate, complex_first_max,
#  complex_reduction_lo, complex_reduction_hi, complex_min)
_TIMING: dict[str, dict] = {
    "rapid": dict(
        opening=(0.5, 2.0),
        recapture=(0.5, 2.5),
        simple=(1.0, 5.0),
        moderate=(5.0, 18.0),
        complex_first=(30.0, 50.0),
        complex_reduction=(10.0, 18.0),
        complex_min=2.0,
    ),
    "blitz": dict(
        opening=(0.3, 1.5),
        recapture=(0.3, 1.5),
        simple=(0.8, 4.0),
        moderate=(3.0, 9.0),
        complex_first=(12.0, 22.0),
        complex_reduction=(4.0, 8.0),
        complex_min=1.0,
    ),
    "bullet": dict(
        opening=(0.2, 1.0),
        recapture=(0.1, 0.6),
        simple=(0.2, 1.5),
        moderate=(1.0, 3.5),
        complex_first=(4.0, 8.0),
        complex_reduction=(1.5, 3.0),
        complex_min=0.3,
    ),
}

_OPENING_MOVES = 8   # fullmove number threshold for "opening" timing
_COMPLEX_GAP   = 150 # gap_cp below this → complex position
_MODERATE_GAP  = 350 # gap_cp below this (but ≥ _COMPLEX_GAP) → moderate


class ThinkTimer:
    def __init__(self, time_control: str):
        self._p = _TIMING.get(time_control, _TIMING["blitz"])
        self._last_complex_time: float = 0.0  # tracks consecutive complex thinks

    # ------------------------------------------------------------------

    def get_delay(
        self,
        board: chess.Board,
        gap_cp: Optional[int],
        bot_move: chess.Move,
        clock_remaining: float,
        from_book: bool = False,
    ) -> float:
        p = self._p

        # Classify the position
        is_opening   = from_book or board.fullmove_number <= _OPENING_MOVES
        is_recapture = self._is_recapture(board, bot_move)
        is_forced    = gap_cp is not None and gap_cp >= 9999  # mate / only move
        is_simple    = gap_cp is None or gap_cp > _MODERATE_GAP or is_forced
        is_moderate  = not is_simple and gap_cp > _COMPLEX_GAP
        is_complex   = not is_simple and not is_moderate

        if is_opening or is_recapture:
            key = "opening" if is_opening else "recapture"
            delay = random.uniform(*p[key])
            self._last_complex_time = 0.0

        elif is_simple:
            delay = random.uniform(*p["simple"])
            self._last_complex_time = 0.0

        elif is_moderate:
            delay = random.uniform(*p["moderate"])
            self._last_complex_time = 0.0

        else:  # complex
            if self._last_complex_time > 0:
                # Consecutive complex move — shorter than the previous one
                reduction = random.uniform(*p["complex_reduction"])
                hi = max(p["complex_min"], self._last_complex_time - reduction)
            else:
                hi = random.uniform(*p["complex_first"])
            lo = max(p["complex_min"], hi * 0.60)
            delay = random.uniform(lo, hi)
            self._last_complex_time = delay

        # Never use more than 20 % of remaining clock on a single think
        max_allowed = max(0.5, clock_remaining * 0.20)
        return min(delay, max_allowed)

    # ------------------------------------------------------------------

    @staticmethod
    def _is_recapture(board: chess.Board, bot_move: chess.Move) -> bool:
        """True if the bot is capturing on the square the opponent just moved to."""
        if not board.move_stack:
            return False
        last_opp_move = board.peek()
        return (
            board.is_capture(bot_move)
            and bot_move.to_square == last_opp_move.to_square
        )
