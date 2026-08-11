import chess
import chess.pgn

from scripts.build_model_review import sample_positions


def test_sample_positions_are_user_turn_and_from_distinct_games(tmp_path):
    games = []
    for index in range(8):
        game = chess.pgn.Game.from_board(chess.Board())
        game.headers["White"] = "yuandan" if index % 2 == 0 else "opponent"
        game.headers["Black"] = "opponent" if index % 2 == 0 else "yuandan"
        board = chess.Board()
        node = game
        for move in (chess.Move.from_uci("e2e4"), chess.Move.from_uci("e7e5")):
            node = node.add_variation(move)
            board.push(move)
        games.append(str(game))
    path = tmp_path / "games.pgn"
    path.write_text("\n\n".join(games), encoding="utf-8")

    positions, eligible = sample_positions(path, "yuandan", 5, seed=7)

    assert eligible == 8
    assert len({position["gameIndex"] for position in positions}) == 5
    assert all(
        chess.Board(position["fen"]).turn
        == (position["userColor"] == "white")
        for position in positions
    )


def test_sampling_is_deterministic(tmp_path):
    game = """[White \"yuandan\"]\n[Black \"opponent\"]\n\n1. e4 e5 2. Nf3 Nc6 *\n"""
    path = tmp_path / "games.pgn"
    path.write_text("\n".join([game] * 4), encoding="utf-8")

    first, _ = sample_positions(path, "yuandan", 3, seed=11)
    second, _ = sample_positions(path, "yuandan", 3, seed=11)

    assert first == second


def test_non_standard_variants_are_excluded(tmp_path):
    standard = """[White \"yuandan\"]\n[Black \"opponent\"]\n\n1. e4 e5 *\n"""
    crazyhouse = """[Variant \"Crazyhouse\"]\n[White \"yuandan\"]\n[Black \"opponent\"]\n\n1. e4 e5 *\n"""
    path = tmp_path / "games.pgn"
    path.write_text(f"{standard}\n{crazyhouse}", encoding="utf-8")

    positions, eligible = sample_positions(path, "yuandan", 1, seed=3)

    assert eligible == 1
    assert positions[0]["gameIndex"] == 1
