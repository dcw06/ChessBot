"""Production smoke test: model load, both colors, and Flask health."""

import os
import chess

from bot.engine import ChessBotEngine, load_model
from web_app import BOOK_PATH, MODEL_PATH, STOCKFISH_PATH, app


def main():
    session, contract = load_model(MODEL_PATH)
    engine = ChessBotEngine(
        "blitz",
        MODEL_PATH,
        opening_book_path=BOOK_PATH,
        stockfish_path=STOCKFISH_PATH,
        inference_session=session,
    )
    try:
        board = chess.Board()
        for _ in range(2):
            move = engine.get_move(board, 180)
            if move not in board.legal_moves:
                raise RuntimeError(f"Engine returned illegal move: {move}")
            board.push(move)
        with app.test_client() as client:
            response = client.get("/health/ready")
            require_stockfish = os.environ.get("REQUIRE_STOCKFISH", "0") == "1"
            expected = 200 if require_stockfish else (200, 503)
            if response.status_code not in (
                (expected,) if isinstance(expected, int) else expected
            ):
                raise RuntimeError(f"Readiness failed: {response.get_json()}")
            if response.get_json().get("model") != "ready":
                raise RuntimeError(f"Model readiness failed: {response.get_json()}")
        print(f"Smoke test passed: input={contract['input_shape']}, moves={board.ply()}")
    finally:
        engine.close()


if __name__ == "__main__":
    main()
