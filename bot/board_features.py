"""
Numpy-only board encoding for inference. Mirrors dataset.py's board_to_tensor,
move_to_index, and flip_move without the torch dependency.
"""
import chess
import numpy as np

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
NUM_ACTIONS = 4096 + 3 * 64


def board_to_tensor(board: chess.Board, flip: bool = False) -> np.ndarray:
    planes = np.zeros((21, 8, 8), dtype=np.uint8)

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


def move_to_index(move: chess.Move) -> int:
    if move.promotion and move.promotion != chess.QUEEN:
        return 4096 + _PROMO_OFFSET[move.promotion] * 64 + move.to_square
    return move.from_square * 64 + move.to_square


def flip_move(move: chess.Move) -> int:
    def flip_sq(sq):
        return chess.square(chess.square_file(sq), 7 - chess.square_rank(sq))
    flipped = chess.Move(flip_sq(move.from_square), flip_sq(move.to_square),
                         promotion=move.promotion)
    return move_to_index(flipped)
