import os
import unittest

import chess

from bot.engine import ChessBotEngine, load_model


MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "best_model.onnx")


class ModelSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session, cls.contract = load_model(MODEL_PATH)

    def test_model_contract(self):
        self.assertEqual(self.contract["input"], "input")
        self.assertEqual(list(self.contract["input_shape"][1:]), [21, 8, 8])
        self.assertEqual(len(self.contract["outputs"]), 2)
        self.assertEqual(self.contract["manifest"]["feature_schema_version"], 1)

    def test_legal_move_for_both_colors(self):
        engine = ChessBotEngine(
            "blitz", MODEL_PATH, opening_book_path="/nonexistent",
            stockfish_path="/nonexistent", inference_session=self.session,
        )
        try:
            board = chess.Board()
            white_move = engine.get_move(board, 180)
            self.assertIn(white_move, board.legal_moves)
            board.push(white_move)
            black_move = engine.get_move(board, 180)
            self.assertIn(black_move, board.legal_moves)
        finally:
            engine.close()


if __name__ == "__main__":
    unittest.main()
