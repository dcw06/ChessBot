from scripts.evaluate_model_accuracy import accuracy_summary, is_one_minute


def test_one_minute_filter_uses_base_seconds():
    assert is_one_minute("60")
    assert is_one_minute("60+1")
    assert not is_one_minute("180")
    assert not is_one_minute("-")


def test_accuracy_summary_counts_exact_matches():
    rows = [
        {"matchesActual": True, "timeControl": "180", "userColor": "white", "fullmove": 2},
        {"matchesActual": True, "timeControl": "180", "userColor": "white", "fullmove": 6},
        {"matchesActual": False, "timeControl": "180", "userColor": "black", "fullmove": 16},
        {"matchesActual": True, "timeControl": "600", "userColor": "white", "fullmove": 31},
    ]

    summary = accuracy_summary(rows)

    assert summary["positions"] == 4
    assert summary["correct"] == 3
    assert summary["accuracy"] == 0.75
    assert summary["breakdowns"]["movePhase"]["1-5"]["accuracy"] == 1.0
    assert summary["breakdowns"]["movePhase"]["6-15"]["accuracy"] == 1.0
