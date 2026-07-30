"""
Run once after download_games.py to build opening_book.json from your PGN archive.
"""
from bot.opening_book import OpeningBook

OpeningBook.build_from_pgn(
    pgn_path="pgns/all_games.pgn",
    username="yuandan",
    output_path="opening_book.json",
)
