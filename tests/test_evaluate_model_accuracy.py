from scripts.evaluate_model_accuracy import accuracy_summary, is_one_minute


def test_one_minute_filter_uses_base_seconds():
    assert is_one_minute("60")
    assert is_one_minute("60+1")
    assert not is_one_minute("180")
    assert not is_one_minute("-")


def test_accuracy_summary_counts_exact_matches():
    rows = [
        {"matchesActual": False, "timeControl": "600", "userColor": "black", "fullmove": 1},
        {"matchesActual": True, "timeControl": "180", "userColor": "white", "fullmove": 2},
        {"matchesActual": True, "timeControl": "180", "userColor": "white", "fullmove": 6},
        {"matchesActual": False, "timeControl": "180", "userColor": "black", "fullmove": 16},
        {"matchesActual": True, "timeControl": "600", "userColor": "white", "fullmove": 31},
    ]

    summary = accuracy_summary(rows)

    assert summary["positions"] == 5
    assert summary["correct"] == 3
    assert summary["accuracy"] == 0.6
    assert summary["breakdowns"]["movePhase"]["1"]["accuracy"] == 0.0
    assert summary["breakdowns"]["movePhase"]["2-5"]["accuracy"] == 1.0
    assert summary["breakdowns"]["movePhase"]["6-15"]["accuracy"] == 1.0


def test_uniform_legal_baseline_uses_same_legal_positions():
    import chess
    import pytest
    from scripts.evaluate_model_accuracy import uniform_legal_expected_agreement

    position = {"fen": chess.STARTING_FEN, "playedMoveUci": "e2e4"}
    assert uniform_legal_expected_agreement([position]) == pytest.approx(1 / 20)
    with pytest.raises(ValueError, match="not legal"):
        uniform_legal_expected_agreement([{**position, "playedMoveUci": "e2e5"}])
    with pytest.raises(ValueError, match="No eligible"):
        uniform_legal_expected_agreement([])
