from unittest.mock import Mock, patch

from bot.stockfish_filter import StockfishFilter


@patch("bot.stockfish_filter.chess.engine.SimpleEngine.popen_uci")
def test_stockfish_uses_slightly_reduced_skill(mock_popen):
    engine = Mock()
    mock_popen.return_value = engine

    stockfish = StockfishFilter("stockfish")

    engine.configure.assert_called_once_with({
        "Threads": 1,
        "Hash": 4,
        "Skill Level": 18,
    })
    stockfish.close()


@patch("bot.stockfish_filter.chess.engine.SimpleEngine.popen_uci")
def test_stockfish_skill_level_is_clamped(mock_popen):
    engine = Mock()
    mock_popen.return_value = engine

    stockfish = StockfishFilter("stockfish", skill_level=25)

    assert stockfish.skill_level == 20
    stockfish.close()
