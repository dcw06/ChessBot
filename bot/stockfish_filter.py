import chess
import chess.engine
from typing import Optional, List


DEFAULT_TIME_LIMIT = 0.04  # 40ms — deep enough to catch most one/two-move tactics

# Moves within this many centipawns of best are considered "acceptable" choices.
# The neural net then picks among them to preserve yuandan's style.
ACCEPTABLE_GAP_CP = 125

# Centipawn gap between best and 2nd-best at which each time control reaches its ceiling.
# Below this, probability scales linearly down to the floor.
_PARAMS = {
    #            floor   ceiling  scale_cp
    "bullet": (  0.20,   0.83,    600 ),  # gap ≥600cp → 83% (>80% as requested)
    "blitz":  (  0.28,   0.93,    500 ),  # gap ≥500cp → 93% (>90%)
    "rapid":  (  0.40,   0.97,    450 ),  # gap ≥450cp → 97% (>97%)
}


class StockfishAnalysis:
    """Result of a single Stockfish search, reused across multiple decisions."""
    __slots__ = ("best_move", "gap_cp", "forced_mate", "top_moves")

    def __init__(
        self,
        best_move: chess.Move,
        gap_cp: int,
        forced_mate: bool,
        top_moves: Optional[List[chess.Move]] = None,
    ):
        self.best_move   = best_move
        self.gap_cp      = gap_cp        # cp gap between best and 2nd-best
        self.forced_mate = forced_mate   # True when we have a forced mate available
        self.top_moves   = top_moves if top_moves is not None else [best_move]


class StockfishFilter:
    """
    Runs a brief Stockfish search once per position and exposes:

      • analyse(board) → StockfishAnalysis   (call once, reuse the result)
      • gap_to_probability(gap_cp, tc) → float

    analyse uses multipv=5 so top_moves captures all reasonable options.
    The neural net then chooses among top_moves to preserve yuandan's style while
    avoiding the blunders that live outside the acceptable range.
    """

    def __init__(
        self,
        stockfish_path: str,
        threshold_cp: int = 150,
        time_limit: float = DEFAULT_TIME_LIMIT,
    ):
        self.threshold_cp = threshold_cp
        self.time_limit   = time_limit
        self._engine = chess.engine.SimpleEngine.popen_uci(stockfish_path)
        self._engine.configure({"Threads": 1, "Hash": 16})

    # ------------------------------------------------------------------

    def analyse(self, board: chess.Board) -> Optional[StockfishAnalysis]:
        """
        Run Stockfish and return structured analysis.  Returns None on error.
        Call this ONCE per position; pass the result to gap_to_probability,
        obvious_move, and top_moves rather than calling the engine again.
        """
        try:
            result = self._engine.analyse(
                board,
                chess.engine.Limit(time=self.time_limit),
                multipv=5,
            )
        except Exception:
            return None

        if not result:
            return None

        best_entry = result[0]
        best_move  = best_entry.get("pv", [None])[0]
        if best_move is None:
            return None

        best_score = best_entry["score"].relative

        # Forced mate available → always obvious, only the mating move matters
        if best_score.is_mate() and best_score.mate() is not None and best_score.mate() > 0:
            return StockfishAnalysis(best_move, gap_cp=9999, forced_mate=True, top_moves=[best_move])

        # Only one legal move
        if len(result) < 2:
            return StockfishAnalysis(best_move, gap_cp=9999, forced_mate=False, top_moves=[best_move])

        second_score = result[1]["score"].relative

        # Can't compare numerically when mates are involved
        if best_score.is_mate() or second_score.is_mate():
            return StockfishAnalysis(best_move, gap_cp=0, forced_mate=False, top_moves=[best_move])

        best_cp = best_score.score()
        gap = max(0, best_cp - second_score.score())

        # Collect all multipv entries within ACCEPTABLE_GAP_CP of best.
        # These are the moves the neural net is allowed to choose from.
        top_moves = [best_move]
        for entry in result[1:]:
            m = entry.get("pv", [None])[0]
            if m is None:
                continue
            s = entry["score"].relative
            if s.is_mate():
                # Being mated from here — don't include these moves
                break
            sc = s.score()
            if sc is None:
                continue
            if best_cp - sc <= ACCEPTABLE_GAP_CP:
                top_moves.append(m)

        return StockfishAnalysis(best_move, gap_cp=gap, forced_mate=False, top_moves=top_moves)

    # ------------------------------------------------------------------

    def gap_to_probability(self, gap_cp: int, time_control: str) -> float:
        """
        Map the centipawn gap to a probability of playing the best move.

        Large gap  → position is straightforward, high probability.
        Small gap  → position is complex / multiple reasonable options, lower probability.

        Calibrated so that:
          bullet  gap ≥ 600cp  →  ≥ 83%  (hanging pieces always ≥ 80%)
          blitz   gap ≥ 500cp  →  ≥ 93%  (                      ≥ 90%)
          rapid   gap ≥ 450cp  →  ≥ 97%  (                      ≥ 97%)
        """
        floor, ceiling, scale = _PARAMS.get(time_control, _PARAMS["blitz"])
        t = min(1.0, gap_cp / scale)
        return floor + (ceiling - floor) * t

    # ------------------------------------------------------------------

    def obvious_move(self, analysis: StockfishAnalysis) -> Optional[chess.Move]:
        """
        Return the best move when the gap is large enough to be considered
        obvious (≥ threshold_cp) or when there is a forced mate.
        """
        if analysis.forced_mate or analysis.gap_cp >= self.threshold_cp:
            return analysis.best_move
        return None

    # ------------------------------------------------------------------

    def close(self):
        try:
            self._engine.quit()
        except Exception:
            pass
