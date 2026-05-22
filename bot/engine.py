import chess
import random
import shutil
import torch
from typing import Optional

from model import ChessNet
from dataset import board_to_tensor, move_to_index, flip_move, NUM_ACTIONS
from .time_manager import TimeManager
from .opening_book import OpeningBook
from .time_pressure import TimePressureHandler
from .tactics import find_rescue_move, find_pawn_rescue, find_recapture, find_winning_capture, find_tactical_move, find_strategic_move
from .stockfish_filter import StockfishFilter
from . import tactic_weights


def _default_device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


_MATERIAL_VALUE = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}

def _material_diff(board: chess.Board) -> int:
    """Absolute material difference in pawn units (always ≥ 0)."""
    diff = 0
    for piece in board.piece_map().values():
        val = _MATERIAL_VALUE[piece.piece_type]
        diff += val if piece.color == board.turn else -val
    return abs(diff)


def _is_dragon_structure(board: chess.Board) -> bool:
    """Black has fianchettoed kingside bishop + d6 pawn — Sicilian Dragon."""
    g6 = board.piece_at(chess.G6)
    g7 = board.piece_at(chess.G7)
    d6 = board.piece_at(chess.D6)
    return (
        g6 is not None and g6.piece_type == chess.PAWN   and g6.color == chess.BLACK
        and g7 is not None and g7.piece_type == chess.BISHOP and g7.color == chess.BLACK
        and d6 is not None and d6.piece_type == chess.PAWN   and d6.color == chess.BLACK
    )


def _is_scandinavian_structure(board: chess.Board) -> bool:
    """
    White's e-pawn AND Black's d-pawn are both gone — the hallmark of the Scandinavian
    exchange (1.e4 d5 2.exd5). Only checked as White past move 5 and outside endgames
    (endgame temperature is handled separately by piece count).
    """
    if board.turn != chess.WHITE or board.fullmove_number < 5:
        return False
    for rank in range(8):
        p = board.piece_at(chess.square(4, rank))  # e-file
        if p and p.piece_type == chess.PAWN and p.color == chess.WHITE:
            return False
    for rank in range(8):
        p = board.piece_at(chess.square(3, rank))  # d-file
        if p and p.piece_type == chess.PAWN and p.color == chess.BLACK:
            return False
    return True


class ChessBotEngine:
    """
    Main orchestrator. Decision order per move:

      1. Panic mode   → TimePressureHandler  (clock below threshold)
      2. Opening      → OpeningBook          (weighted from user's PGN + stated prefs)
      3. Tactic       → tactics module       (mate-in-1, forks, pins, back-rank)
      4. Strategy     → tactics module       (knight outpost, rook to open file)
      5. Neural net   → ChessNet             (behavioral clone, situational temperature)

    Temperature is scaled up in positions where yuandan historically struggles:
      - Endgame (≤14 pieces): 1.8× — win rate drops from 55% → 38% in these positions
      - Endgame (≤10 pieces): 3.0× — win rate drops to 25.8% at 60+ moves
      - Sicilian Dragon as White: 2.5× — 75% loss rate in rapid games
      - Scandinavian as White: 1.8× — 61% loss rate in rapid games
    """

    def __init__(
        self,
        time_control: str,
        model_path: str,
        username: str = "yuandan",
        opening_book_path: str = "opening_book.json",
        device: Optional[torch.device] = None,
        stockfish_path: str = "",
    ):
        self.device = device or _default_device()
        torch.set_num_threads(1)  # prevent PyTorch spawning extra threads on single-core containers

        self.time_manager = TimeManager(time_control)
        self.opening_book = OpeningBook(opening_book_path, username)
        self.time_pressure = TimePressureHandler()

        self.model = ChessNet(num_actions=NUM_ACTIONS).to(self.device)
        self.model.load_state_dict(torch.load(model_path, map_location=self.device, weights_only=True))
        self.model.eval()

        # Exposed after each get_move() call so the UI can compute think delays.
        self.last_gap_cp: Optional[int] = None
        self.last_from_book: bool = False

        resolved = stockfish_path or shutil.which("stockfish") or "/usr/games/stockfish"
        try:
            self.stockfish = StockfishFilter(resolved, threshold_cp=400)
            print(f"[ChessBotEngine] Stockfish enabled (threshold=450cp, time=20ms)")
        except Exception:
            self.stockfish = None
            print(f"[ChessBotEngine] Stockfish not found — obvious-move filter disabled")

        print(f"[ChessBotEngine] time_control={time_control}  device={self.device}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_move(
        self,
        board: chess.Board,
        clock_remaining: float,
        is_rematch: bool = False,
    ) -> chess.Move:
        self.last_gap_cp   = None
        self.last_from_book = False

        # 1. Time pressure
        if clock_remaining < self.time_manager.profile.panic_threshold:
            return self.time_pressure.get_move(board, clock_remaining, self._neural_move)

        # 2. Opening book
        book_move = self.opening_book.get_move(board, is_rematch=is_rematch)
        if book_move is not None:
            self.last_from_book = True
            return book_move

        # 3. Run Stockfish once — reuse the analysis for all downstream decisions.
        #    gap_to_probability reflects how "obvious" the single best move is (best vs 2nd-best).
        #    When multiple recaptures are equally good the gap is near 0 — but the bot should
        #    still recapture. So sf_p acts as a ceiling boost only: we take max(sf_p, profile),
        #    never letting sf_p reduce below the profile's own miss rates.
        #    top_moves (moves within 75cp of best) are passed to the neural net so it can only
        #    pick among Stockfish-approved options, preserving style without blundering.
        sf_p = None
        sf_analysis = None
        if self.stockfish is not None:
            sf_analysis = self.stockfish.analyse(board)
            if sf_analysis is not None:
                self.last_gap_cp = sf_analysis.gap_cp
                sf_move = self.stockfish.obvious_move(sf_analysis)
                if sf_move is not None:
                    return sf_move
                sf_p = self.stockfish.gap_to_probability(
                    sf_analysis.gap_cp, self.time_manager.time_control
                )

        profile = self.time_manager.profile

        # Pre-compute the set of Stockfish-approved moves once for O(1) lookups below.
        # Any rule-based move NOT in this set would be worse than best by >75cp — i.e.,
        # it likely allows a mate or drops a piece — so we skip it and let the neural net
        # pick safely from the approved pool.
        sf_ok = set(sf_analysis.top_moves) if sf_analysis is not None else None

        def approved(move: chess.Move) -> bool:
            return sf_ok is None or move in sf_ok

        # 5. Save a currently-hanging friendly piece.
        rescue = find_rescue_move(board)
        if rescue is not None and approved(rescue):
            p = max(sf_p, profile.rescue_probability) if sf_p is not None else profile.rescue_probability
            if random.random() < p:
                return rescue

        # 5b. Push a hanging undefended pawn to safety.
        pawn_rescue = find_pawn_rescue(board)
        if pawn_rescue is not None and approved(pawn_rescue):
            p = max(sf_p, profile.pawn_rescue_probability) if sf_p is not None else profile.pawn_rescue_probability
            if random.random() < p:
                return pawn_rescue

        # 5c. Recapture if the opponent just took one of our pieces.
        #     Equal exchanges (Nxe5 Nxe5) are not "winning" in material terms but the bot
        #     should always take back. Uses same probability floor as winning captures.
        recapture = find_recapture(board)
        if recapture is not None and approved(recapture):
            p = max(sf_p, profile.winning_capture_probability) if sf_p is not None else profile.winning_capture_probability
            if random.random() < p:
                return recapture

        # 6. Take free or clearly-winning material.
        #    max() ensures multiple equal recaptures (small gap -> low sf_p) still fire at
        #    the profile rate — the bot knows to recapture, just not which piece is "best".
        winning_capture = find_winning_capture(board)
        if winning_capture is not None and approved(winning_capture):
            p = max(sf_p, profile.winning_capture_probability) if sf_p is not None else profile.winning_capture_probability
            if random.random() < p:
                return winning_capture

        # 7. Tactical patterns — each tactic has its own per-tactic weight from the
        #    weights table (calibrated to yuandan's historical noticing rate).
        #    Low-time fallback uses a reduced version of the same weight.
        tactic_result = find_tactical_move(board)
        if tactic_result is not None:
            tactic_move, tactic_name = tactic_result
            if approved(tactic_move):
                p = tactic_weights.get_weight(tactic_name, self.time_manager.time_control)
                if profile.low_time_threshold > 0 and clock_remaining < profile.low_time_threshold:
                    p *= 0.80  # additional miss rate under time pressure
                if random.random() < p:
                    return tactic_move

        # 8. Positional strategy — per-tactic weight replaces the broad strategic_probability.
        strategic_result = find_strategic_move(board)
        if strategic_result is not None:
            strategic_move, strategic_name = strategic_result
            if approved(strategic_move):
                p = tactic_weights.get_weight(strategic_name, self.time_manager.time_control)
                if random.random() < p:
                    return strategic_move

        # 9. Neural net — restricted to Stockfish-approved moves when available,
        #    so it picks yuandan's preferred style among only the good options.
        temp = self._situational_temperature(board, self.time_manager.profile.temperature)
        allowed = sf_analysis.top_moves if sf_analysis is not None else None
        return self._neural_move(board, temp, allowed_moves=allowed)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _situational_temperature(self, board: chess.Board, base_temp: float) -> float:
        """Scale temperature up for positions where yuandan historically underperforms."""
        total_pieces = len(board.piece_map())

        # Endgame boost only applies when the position is balanced (material diff ≤ 2 pawns).
        # Being up a rook is still a technical endgame, but the outcome is not in doubt —
        # boosting temperature there would cause the bot to lose won games, not mimic struggle.
        if total_pieces <= 14 and _material_diff(board) <= 2:
            if total_pieces <= 10:
                return base_temp * 3.0
            return base_temp * 1.8

        # Sicilian Dragon as White: 75% loss rate in rapid
        if board.turn == chess.WHITE and _is_dragon_structure(board):
            return base_temp * 2.5

        # Scandinavian as White: 61% loss rate in rapid
        if _is_scandinavian_structure(board):
            return base_temp * 1.8

        return base_temp

    def _neural_move(
        self,
        board: chess.Board,
        temperature: float = 1.0,
        allowed_moves=None,
    ) -> chess.Move:
        # Canonicalize to user's-pieces-at-bottom perspective, matching training.
        canonical_flip = (board.turn == chess.BLACK)
        tensor = board_to_tensor(board, flip=canonical_flip).unsqueeze(0).float()
        tensor[0, 20] /= 100.0  # normalize 50-move clock to [0, 1] (matches training)
        tensor = tensor.to(self.device)
        with torch.no_grad():
            policy_logits, _ = self.model(tensor)
        logits = policy_logits[0]

        legal_moves = list(board.legal_moves)

        # Restrict to Stockfish-approved moves when provided. This keeps yuandan's
        # stylistic choices but prevents the net from wandering into blunder territory.
        if allowed_moves:
            allowed_set = set(allowed_moves)
            filtered = [m for m in legal_moves if m in allowed_set]
            if filtered:
                legal_moves = filtered

        if canonical_flip:
            indices = torch.tensor([flip_move(m) for m in legal_moves], device=self.device)
        else:
            indices = torch.tensor([move_to_index(m) for m in legal_moves], device=self.device)
        legal_logits = logits[indices]

        probs = torch.softmax(legal_logits / temperature, dim=0)

        # Blunder filter: reject candidates that leave any piece worth ≥3 en prise.
        # Samples up to 8 times; if all samples hang a piece, falls back to a greedy
        # scan of the full sorted logit list so we never return a hanging move.
        best = int(torch.argmax(probs).item())  # greedy fallback if all samples fail
        for _ in range(8):
            candidate = int(torch.multinomial(probs, 1).item())
            if not self._hangs_piece(board, legal_moves[candidate]):
                best = candidate
                break
            probs[candidate] = 0.0
            total = probs.sum()
            if total <= 0:
                break
            probs = probs / total
        else:
            # 8 samples all hung pieces — walk sorted logits to find the first safe move
            for idx in torch.argsort(legal_logits, descending=True).tolist():
                if not self._hangs_piece(board, legal_moves[idx]):
                    best = idx
                    break

        return legal_moves[best]

    def _hangs_piece(self, board: chess.Board, move: chess.Move) -> bool:
        """True if making this move leaves any of our pieces worth ≥3 en prise.

        Catches two cases:
        - Completely undefended (attacked, no defenders).
        - Defended but by a more expensive piece (losing exchange).
        """
        board.push(move)
        us = not board.turn
        opponent = board.turn
        result = False
        for sq, piece in board.piece_map().items():
            if piece.color != us:
                continue
            if piece.piece_type not in (chess.QUEEN, chess.ROOK, chess.KNIGHT, chess.BISHOP):
                continue
            if not board.is_attacked_by(opponent, sq):
                continue
            pval = _MATERIAL_VALUE[piece.piece_type]
            # Undefended → always bad
            if not board.is_attacked_by(us, sq):
                result = True
                break
            # Defended, but cheapest attacker wins the exchange
            min_attacker = min(
                _MATERIAL_VALUE[board.piece_at(a).piece_type]
                for a in board.attackers(opponent, sq)
                if board.piece_at(a)
            )
            if min_attacker < pval:
                result = True
                break
        board.pop()
        return result
