import chess
import chess.pgn
import torch
import numpy as np
from torch.utils.data import Dataset

PIECE_PLANE = {
    (chess.PAWN,   chess.WHITE): 0,
    (chess.KNIGHT, chess.WHITE): 1,
    (chess.BISHOP, chess.WHITE): 2,
    (chess.ROOK,   chess.WHITE): 3,
    (chess.QUEEN,  chess.WHITE): 4,
    (chess.KING,   chess.WHITE): 5,
    (chess.PAWN,   chess.BLACK): 6,
    (chess.KNIGHT, chess.BLACK): 7,
    (chess.BISHOP, chess.BLACK): 8,
    (chess.ROOK,   chess.BLACK): 9,
    (chess.QUEEN,  chess.BLACK): 10,
    (chess.KING,   chess.BLACK): 11,
}

# Promotion pieces encoded as an offset beyond 4096 (the 64×64 from-to space).
# Each promotion type gets its own band of 64 destination squares.
# Index = 4096 + promo_offset[piece] * 64 + to_square
_PROMO_OFFSET = {chess.ROOK: 0, chess.BISHOP: 1, chess.KNIGHT: 2}
NUM_ACTIONS = 4096 + 3 * 64   # queen-promo reuses the base 4096 slot

# 12 piece planes + turn + 4 castling + en passant + 2 repetition + 50-move clock
NUM_PLANES = 21


def board_to_tensor(board: chess.Board, flip: bool = False) -> torch.Tensor:
    """
    21-plane uint8 tensor stored compactly (planes 0-17 are binary; plane 20 stores
    the raw halfmove clock 0-100 and must be divided by 100.0 before model input).
    flip=True mirrors the board vertically and swaps piece colors for augmentation.
    """
    planes = np.zeros((NUM_PLANES, 8, 8), dtype=np.uint8)

    for sq, piece in board.piece_map().items():
        r, c = divmod(sq, 8)
        if flip:
            r = 7 - r
            color = not piece.color
        else:
            color = piece.color
        planes[PIECE_PLANE[(piece.piece_type, color)], r, c] = 1

    # Plane 12: 1 if White to move, 0 if Black to move.
    # After color-flip: 0 when the original board had White to move (now presented as Black),
    # 1 when the original board had Black to move (now presented as White).
    planes[12] = 1 if (board.turn == chess.WHITE) != flip else 0

    if not flip:
        if board.has_kingside_castling_rights(chess.WHITE):  planes[13] = 1
        if board.has_queenside_castling_rights(chess.WHITE): planes[14] = 1
        if board.has_kingside_castling_rights(chess.BLACK):  planes[15] = 1
        if board.has_queenside_castling_rights(chess.BLACK): planes[16] = 1
    else:
        if board.has_kingside_castling_rights(chess.BLACK):  planes[13] = 1
        if board.has_queenside_castling_rights(chess.BLACK): planes[14] = 1
        if board.has_kingside_castling_rights(chess.WHITE):  planes[15] = 1
        if board.has_queenside_castling_rights(chess.WHITE): planes[16] = 1

    if board.ep_square is not None:
        r, c = divmod(board.ep_square, 8)
        planes[17, 7 - r if flip else r, c] = 1

    # Planes 18-19: repetition count (flipped positions are synthetic — no history, leave 0)
    if not flip:
        if board.is_repetition(2):
            planes[18] = 1
        if board.is_repetition(3):
            planes[19] = 1

    # Plane 20: 50-move clock. Stored as raw count 0–100 (uint8 fits).
    # Divide by 100.0 after casting to float32 (done in __getitem__ and engine inference).
    planes[20] = min(board.halfmove_clock, 100)

    return torch.from_numpy(planes)


def move_to_index(move: chess.Move) -> int:
    """
    Encode a move as an integer in [0, NUM_ACTIONS).
    Queen promotions use the standard 64×64 slot.
    Under-promotions (R, B, N) use slots above 4096 to avoid conflicting gradients.
    """
    if move.promotion and move.promotion != chess.QUEEN:
        return 4096 + _PROMO_OFFSET[move.promotion] * 64 + move.to_square
    return move.from_square * 64 + move.to_square


def _find_promoting_pawn(board: chess.Board, to_sq: int) -> int:
    """Return the from-square of the pawn that can promote to to_sq."""
    to_rank = chess.square_rank(to_sq)
    to_file = chess.square_file(to_sq)
    color = chess.WHITE if to_rank == 7 else chess.BLACK
    from_rank = to_rank - 1 if color == chess.WHITE else to_rank + 1
    for f in range(max(0, to_file - 1), min(8, to_file + 2)):
        sq = chess.square(f, from_rank)
        piece = board.piece_at(sq)
        if piece and piece.piece_type == chess.PAWN and piece.color == color:
            return sq
    raise ValueError(f"no promoting pawn found for to_sq={to_sq} on board: {board.fen()}")


def index_to_move(idx: int, board: chess.Board = None) -> chess.Move:
    """Decode a move index back to a chess.Move.

    Pass board to correctly handle all promotions:
    - Under-promotions (idx ≥ 4096): board is used to locate the promoting pawn's
      from-square (lost in encoding because only to_square is stored).
    - Queen promotions (idx < 4096): board is used to detect that a pawn is moving
      to the back rank (queen promotion flag otherwise absent from the encoding).
    """
    if idx >= 4096:
        promo_band = (idx - 4096) // 64
        to_sq = (idx - 4096) % 64
        promo_piece = [chess.ROOK, chess.BISHOP, chess.KNIGHT][promo_band]
        if board is None:
            raise ValueError("board is required to decode under-promotion index")
        from_sq = _find_promoting_pawn(board, to_sq)
        return chess.Move(from_sq, to_sq, promotion=promo_piece)
    from_sq, to_sq = idx // 64, idx % 64
    if board is not None:
        piece = board.piece_at(from_sq)
        if piece and piece.piece_type == chess.PAWN and chess.square_rank(to_sq) in (0, 7):
            return chess.Move(from_sq, to_sq, promotion=chess.QUEEN)
    return chess.Move(from_sq, to_sq)


def flip_move(move: chess.Move) -> int:
    """Mirror a move's squares vertically — used to canonicalize Black moves to
    the user's-pieces-at-bottom perspective, both during training and inference."""
    def flip_sq(sq):
        return chess.square(chess.square_file(sq), 7 - chess.square_rank(sq))
    flipped = chess.Move(flip_sq(move.from_square), flip_sq(move.to_square),
                         promotion=move.promotion)
    return move_to_index(flipped)


# ---------------------------------------------------------------------------
# Game-level loading — keeps all positions from one game together so the
# train/val split can be done at the game level (preventing data leakage).
# ---------------------------------------------------------------------------

GameData = list[tuple[list[torch.Tensor], list[int], list[float]]]


def load_game_positions(
    pgn_file: str,
    username: str,
    only_wins: bool = False,
) -> GameData:
    """
    Returns a list where each entry is one game: (positions, move_indices, outcomes).

    All positions are canonicalized to the user's perspective: the user's pieces are
    always presented at ranks 0-1. White games are stored as-is; Black games are
    rank-mirrored and color-swapped (flip=True). This matches the inference convention
    in _neural_move, which also flips when it's Black's turn, making the model
    effectively color-blind — it always plays as "the side whose pieces are at the bottom."

    Outcomes are discounted toward 0.5 for early moves (30% weight at move 1, 100%
    at move 40+) to reduce the noise from assigning a single game result to all positions.
    Positions are stored as uint8 tensors (~4× smaller than float32).
    """
    username = username.lower()
    games: GameData = []

    print(f"Loading games from {pgn_file}...")
    with open(pgn_file) as f:
        game_count    = 0
        total_positions = 0
        while True:
            game = chess.pgn.read_game(f)
            if game is None:
                break

            white = game.headers.get("White", "").lower()
            black = game.headers.get("Black", "").lower()
            result = game.headers.get("Result", "*")

            if username in white:
                user_color = chess.WHITE
            elif username in black:
                user_color = chess.BLACK
            else:
                continue

            if only_wins:
                if user_color == chess.WHITE and result != "1-0":
                    continue
                if user_color == chess.BLACK and result != "0-1":
                    continue

            if result == "1-0":
                outcome = 1.0 if user_color == chess.WHITE else 0.0
            elif result == "0-1":
                outcome = 1.0 if user_color == chess.BLACK else 0.0
            else:
                outcome = 0.5

            # Canonicalize Black games: flip so the user's pieces are always at ranks 0-1.
            # Inference does the same flip in _neural_move, so training and inference
            # see the exact same distribution.
            canonical_flip = (user_color == chess.BLACK)

            positions: list[torch.Tensor] = []
            move_indices: list[int] = []
            outcomes: list[float] = []

            board = game.board()
            for move in game.mainline_moves():
                if board.turn == user_color:
                    # Discount outcome toward neutral (0.5) for early moves: a move-5
                    # position could still go either way, while move 58 nearly determines
                    # the result. Ramp: 30% weight at move 1, 100% at move 40+.
                    ramp = min(1.0, 0.3 + 0.7 * board.fullmove_number / 40)
                    discounted = 0.5 + (outcome - 0.5) * ramp
                    positions.append(board_to_tensor(board, flip=canonical_flip))
                    if canonical_flip:
                        move_indices.append(flip_move(move))
                    else:
                        move_indices.append(move_to_index(move))
                    outcomes.append(discounted)
                board.push(move)

            if positions:
                games.append((positions, move_indices, outcomes))
                total_positions += len(positions)

            game_count += 1
            if game_count % 1000 == 0:
                print(f"  {game_count} games, {total_positions} positions...")

    print(f"Done: {game_count} games → {total_positions} positions")
    return games


class GamesDataset(Dataset):
    """Flat dataset built from a pre-split list of game data."""

    def __init__(self, game_data: GameData):
        self.positions: list[torch.Tensor] = [p for g in game_data for p in g[0]]
        self.moves: list[int] = [m for g in game_data for m in g[1]]
        self.outcomes: list[float] = [o for g in game_data for o in g[2]]

    def __len__(self):
        return len(self.positions)

    def __getitem__(self, idx):
        # Convert uint8 → float32 on the fly (cheap, avoids 4× RAM overhead)
        x = self.positions[idx].float()
        x[20] /= 100.0  # normalize 50-move clock to [0, 1]
        return x, self.moves[idx], self.outcomes[idx]
