import chess
import time

from bot import ChessBotEngine

USERNAME = "yuandan"
MODEL_PATH = "best_model.pt"
OPENING_BOOK_PATH = "opening_book.json"

TIME_CONTROLS = {
    "1":  ("bullet", 60),
    "3":  ("blitz",  180),
    "10": ("rapid",  600),
}


def play():
    print("=== ChessBot (yuandan clone) ===\n")

    print("Time control:")
    print("  1  →  1 min  bullet")
    print("  3  →  3 min  blitz")
    print("  10 → 10 min  rapid")
    tc_input = input("Choose (1 / 3 / 10): ").strip()
    if tc_input not in TIME_CONTROLS:
        print("Defaulting to blitz.")
        tc_input = "3"

    tc_name, total_seconds = TIME_CONTROLS[tc_input]

    bot = ChessBotEngine(
        time_control=tc_name,
        model_path=MODEL_PATH,
        username=USERNAME,
        opening_book_path=OPENING_BOOK_PATH,
    )

    color_input = input("\nBot plays as White or Black? (w/b): ").strip().lower()
    bot_color = chess.WHITE if color_input == "w" else chess.BLACK

    is_rematch = input("Is this a rematch? (y/n): ").strip().lower() == "y"

    board = chess.Board()
    bot_clock = float(total_seconds)
    human_clock = float(total_seconds)

    while not board.is_game_over(claim_draw=True):
        print()
        print(board)
        print(f"\n  Bot  clock : {bot_clock:6.1f}s")
        print(f"  Your clock : {human_clock:6.1f}s\n")

        if board.turn == bot_color:
            t0 = time.time()
            move = bot.get_move(board, bot_clock, is_rematch=is_rematch)
            elapsed = time.time() - t0
            bot_clock = max(0.0, bot_clock - elapsed)
            print(f"  Bot plays: {board.san(move)}  ({elapsed:.2f}s | {bot_clock:.1f}s left)")
            board.push(move)
        else:
            legal = {board.san(m): m for m in board.legal_moves}
            t0 = time.time()
            while True:
                san = input("  Your move: ").strip()
                if san in legal:
                    board.push(legal[san])
                    break
                print("  Illegal move — try again.")
            human_clock = max(0.0, human_clock - (time.time() - t0))

        if bot_clock <= 0:
            print("\nBot flagged — you win on time!")
            return
        if human_clock <= 0:
            print("\nYou flagged — bot wins on time!")
            return

    print()
    print(board)
    if board.is_checkmate():
        loser = "Bot" if board.turn == bot_color else "You"
        print(f"\nCheckmate — {loser} lost.")
    elif board.is_stalemate():
        print("\nDraw by stalemate.")
    elif board.is_insufficient_material():
        print("\nDraw by insufficient material.")
    elif board.is_fifty_moves():
        print("\nDraw by 50-move rule.")
    elif board.is_repetition(3):
        print("\nDraw by threefold repetition.")
    else:
        print(f"\nGame over: {board.result()}")


if __name__ == "__main__":
    play()
