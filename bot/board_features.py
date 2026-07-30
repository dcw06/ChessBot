"""
Numpy-only board encoding for inference. Mirrors dataset.py's board_to_tensor,
move_to_index, and flip_move without the torch dependency.
"""
import chess
import numpy as np

from .model_contract import (
    ACTION_ENCODING_VERSION,
    LEGACY_NUM_ACTIONS,
    NUM_ACTIONS,
    NUM_PLANES,
)

_PIECE_PLANE = {
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

_PROMO_OFFSET = {chess.ROOK: 0, chess.BISHOP: 1, chess.KNIGHT: 2}


def board_to_tensor(board: chess.Board, flip: bool = False) -> np.ndarray:
    planes = np.zeros((NUM_PLANES, 8, 8), dtype=np.uint8)

    for sq, piece in board.piece_map().items():
        r, c = divmod(sq, 8)
        if flip:
            r = 7 - r
            color = not piece.color
        else:
            color = piece.color
        planes[_PIECE_PLANE[(piece.piece_type, color)], r, c] = 1

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

    if not flip:
        if board.is_repetition(2):
            planes[18] = 1
        if board.is_repetition(3):
            planes[19] = 1

    planes[20] = min(board.halfmove_clock, 100)
    return planes


def move_to_index(move: chess.Move, version: int = ACTION_ENCODING_VERSION) -> int:
    if move.promotion and move.promotion != chess.QUEEN:
        if version == 1:
            return 4096 + _PROMO_OFFSET[move.promotion] * 64 + move.to_square
        direction = chess.square_file(move.from_square) - chess.square_file(move.to_square) + 1
        return 4096 + _PROMO_OFFSET[move.promotion] * 192 + direction * 64 + move.to_square
    return move.from_square * 64 + move.to_square


def index_to_move(
    index: int, board: chess.Board, version: int = ACTION_ENCODING_VERSION
) -> chess.Move:
    """Decode an action index, including queen and under-promotions."""
    limit = LEGACY_NUM_ACTIONS if version == 1 else NUM_ACTIONS
    if not 0 <= index < limit:
        raise ValueError(f"action index out of range: {index}")
    if index >= 4096:
        if version == 1:
            band, to_square = divmod(index - 4096, 64)
            direction = None
        else:
            band, remainder = divmod(index - 4096, 192)
            direction, to_square = divmod(remainder, 64)
        promotion = (chess.ROOK, chess.BISHOP, chess.KNIGHT)[band]
        candidates = [
            move for move in board.legal_moves
            if move.to_square == to_square and move.promotion == promotion
            and (
                direction is None
                or chess.square_file(move.from_square) - chess.square_file(move.to_square) + 1
                == direction
            )
        ]
    else:
        from_square, to_square = divmod(index, 64)
        candidates = [
            move for move in board.legal_moves
            if move.from_square == from_square and move.to_square == to_square
        ]
        queen = [move for move in candidates if move.promotion == chess.QUEEN]
        if queen:
            candidates = queen
    if len(candidates) != 1:
        raise ValueError(f"action index {index} is ambiguous or illegal for this board")
    return candidates[0]


def flip_move(move: chess.Move, version: int = ACTION_ENCODING_VERSION) -> int:
    def flip_sq(sq):
        return chess.square(chess.square_file(sq), 7 - chess.square_rank(sq))
    flipped = chess.Move(flip_sq(move.from_square), flip_sq(move.to_square),
                         promotion=move.promotion)
    return move_to_index(flipped, version=version)
