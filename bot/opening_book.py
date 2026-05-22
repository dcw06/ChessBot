import json
import os
import chess
import chess.pgn
import random
from collections import defaultdict
from typing import Optional

BOOK_DEPTH = 15   # half-moves into opening to record


def _fen_key(fen: str) -> str:
    """Position key ignoring move counters so transpositions match."""
    return " ".join(fen.split()[:4])


class OpeningBook:
    def __init__(self, book_path: str, username: str):
        self.username = username.lower()
        self._last_first_move: Optional[str] = None   # for rematch variety

        self.book: dict[str, dict[str, int]] = {}
        if os.path.exists(book_path):
            with open(book_path) as f:
                self.book = json.load(f)
        else:
            print(f"[OpeningBook] No book found at {book_path}. Run build_opening_book.py first.")

        self._inject_stated_preferences()

    # ------------------------------------------------------------------
    # Hard-coded preferences stated by the user
    # ------------------------------------------------------------------
    def _inject_stated_preferences(self):
        # Black responses: proportional boost scaled to position frequency so the boost
        # can't overwhelm a large game archive.
        b = chess.Board(); b.push_uci("d2d4")
        self._ensure_min_fraction(_fen_key(b.fen()), "d7d5", 0.70)  # d5 vs 1.d4

        b = chess.Board(); b.push_uci("c2c4")
        self._ensure_min_fraction(_fen_key(b.fen()), "c7c6", 0.70)  # Slav/Caro structure vs 1.c4 (72.1% of games)

        b = chess.Board(); b.push_uci("g1f3")
        self._ensure_min_fraction(_fen_key(b.fen()), "d7d5", 0.60)  # d5 vs 1.Nf3

    def _boost(self, key: str, move_uci: str, weight: int):
        counts = self.book.setdefault(key, {})
        counts[move_uci] = counts.get(move_uci, 0) + weight

    def _ensure_min_fraction(self, key: str, move_uci: str, target: float):
        """Boost move_uci until it holds at least `target` fraction of the total weight.

        Scales with dataset size: if the organic data already meets the target, no weight
        is added. For small datasets, adds just enough to reach the target fraction.
        """
        counts = self.book.setdefault(key, {})
        total = sum(counts.values())
        if total == 0:
            counts[move_uci] = 100  # cold start — no game data yet
            return
        current = counts.get(move_uci, 0)
        if target >= 1.0:
            raise ValueError(f"target must be < 1.0, got {target}")
        if current / total < target:
            # Solve (current + x) / (total + x) = target  →  x = (target·total - current) / (1 - target)
            needed = int((target * total - current) / (1 - target)) + 1
            counts[move_uci] = current + needed

    # ------------------------------------------------------------------
    # Look up a move for the current position
    # ------------------------------------------------------------------
    def get_move(self, board: chess.Board, is_rematch: bool = False) -> Optional[chess.Move]:
        if board.ply() >= BOOK_DEPTH:
            return None

        key = _fen_key(board.fen())
        candidates = dict(self.book.get(key, {}))
        if not candidates:
            return None

        # Filter to legal moves only
        legal_ucis = {m.uci() for m in board.legal_moves}
        candidates = {uci: w for uci, w in candidates.items() if uci in legal_ucis}
        if not candidates:
            return None

        # On a rematch, drop the first-move choice we played last time for variety
        if is_rematch and board.ply() == 0 and self._last_first_move in candidates and len(candidates) > 1:
            del candidates[self._last_first_move]

        moves, weights = zip(*candidates.items())
        # First two plies uniform: covers White's first move (ply 0) and Black's first
        # response (ply 1). Subsequent moves stay proportional to game frequency so each
        # opening line follows yuandan's actual style.
        if board.ply() <= 1:
            weights = [1] * len(moves)
        chosen = random.choices(moves, weights=weights, k=1)[0]

        if board.ply() == 0:
            self._last_first_move = chosen

        return chess.Move.from_uci(chosen)

    # ------------------------------------------------------------------
    # Build book from PGN (call once after downloading games)
    # ------------------------------------------------------------------
    @classmethod
    def build_from_pgn(cls, pgn_path: str, username: str, output_path: str):
        book: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        username = username.lower()

        print(f"Building opening book from {pgn_path}...")
        with open(pgn_path) as f:
            game_count = 0
            while True:
                game = chess.pgn.read_game(f)
                if game is None:
                    break

                white = game.headers.get("White", "").lower()
                black = game.headers.get("Black", "").lower()
                if username in white:
                    user_color = chess.WHITE
                elif username in black:
                    user_color = chess.BLACK
                else:
                    continue

                board = game.board()
                for move in game.mainline_moves():
                    if board.ply() >= BOOK_DEPTH:
                        break
                    if board.turn == user_color:
                        book[_fen_key(board.fen())][move.uci()] += 1
                    board.push(move)

                game_count += 1
                if game_count % 1000 == 0:
                    print(f"  {game_count} games processed...")

        out = {k: dict(v) for k, v in book.items()}
        with open(output_path, "w") as f:
            json.dump(out, f)
        print(f"Saved {len(out)} positions to {output_path}")
