from dataclasses import replace

import pandas as pd
import pytest

from megalord.research.trade_contract import ENGINES, MeasuredTrade, utc_time


def measured():
    return MeasuredTrade("Begonya", "EURUSD", "LONG", pd.Timestamp("2026-10-19T10:00Z"),
                         pd.Timestamp("2026-10-19T11:00Z"), 1.1, 1.09, 0, 0.1,
                         "a" * 64, "Gross from midpoint marks; commission and spread measured separately")


def test_all_six_engines_have_equal_research_contract():
    for engine in ENGINES:
        assert replace(measured(), engine=engine).net_r == -0.1


def test_zero_outcome_is_not_replaced_by_a_winning_default():
    assert measured().gross_r == 0


def test_epoch_milliseconds_are_explicit():
    with pytest.raises(ValueError, match="epoch unit"):
        utc_time(1787784300000)
    assert utc_time(1787784300000, unit="ms").year == 2026


@pytest.mark.parametrize("changes", [
    {"exit_time": None}, {"exit_time": pd.Timestamp("2026-10-19T09:00Z")},
    {"entry_time": pd.Timestamp("2026-10-19 10:00")}, {"cost_r": float("nan")},
    {"cost_basis": ""}, {"source_sha256": "missing"}, {"initial_stop": 1.11},
])
def test_incomplete_or_invalid_evidence_is_rejected(changes):
    with pytest.raises(ValueError):
        replace(measured(), **changes)
