import unittest

import chess
import numpy as np

from bot.board_features import (
    board_to_tensor,
    flip_move,
    index_to_move,
    move_to_index,
)
from bot import tactics


class BoardFeatureTests(unittest.TestCase):
    def test_black_canonicalization_matches_color_swapped_vertical_flip(self):
        board = chess.Board()
        board.push_uci("e2e4")
        encoded = board_to_tensor(board, flip=True)
        self.assertEqual(encoded.shape, (21, 8, 8))
        self.assertEqual(encoded.dtype, np.uint8)
        self.assertEqual(encoded[0, 1, 4], 1)  # original black e7 pawn becomes white e2
        self.assertEqual(encoded[6, 4, 4], 1)  # original white e4 pawn becomes black e5
        self.assertEqual(encoded[12, 0, 0], 1)
        self.assertEqual(flip_move(chess.Move.from_uci("e7e5")),
                         move_to_index(chess.Move.from_uci("e2e4")))

    def test_promotion_indices_round_trip(self):
        board = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1")
        for suffix in ("q", "r", "b", "n"):
            move = chess.Move.from_uci("a7a8" + suffix)
            self.assertEqual(index_to_move(move_to_index(move), board), move)

    def test_two_capture_underpromotions_have_distinct_v2_indices(self):
        board = chess.Board("1r5k/P1P5/8/8/8/8/8/7K w - - 0 1")
        left = chess.Move.from_uci("a7b8n")
        right = chess.Move.from_uci("c7b8n")
        self.assertIn(left, board.legal_moves)
        self.assertIn(right, board.legal_moves)
        self.assertNotEqual(move_to_index(left), move_to_index(right))
        self.assertEqual(index_to_move(move_to_index(left), board), left)
        self.assertEqual(index_to_move(move_to_index(right), board), right)

    def test_tactical_scans_restore_board(self):
        board = chess.Board()
        board.push_uci("e2e4")
        board.push_uci("d7d5")
        original_fen = board.fen()
        original_stack = list(board.move_stack)
        for finder in (
            tactics.find_strategic_move,
            tactics.find_rescue_move,
            tactics.find_pawn_rescue,
            tactics.find_recapture,
            tactics.find_winning_capture,
            tactics.find_tactical_move,
        ):
            finder(board)
            self.assertEqual(board.fen(), original_fen, finder.__name__)
            self.assertEqual(board.move_stack, original_stack, finder.__name__)


if __name__ == "__main__":
    unittest.main()
