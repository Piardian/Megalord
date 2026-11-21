from types import SimpleNamespace

import pandas as pd
import pytest

from megalord.research.native_observers import NativeTrace, observe_stigma, instrument_inline_function
from megalord.research.portfolio_replay import ReplayTrade, replay
from megalord.research.trade_contract import MeasuredTrade


def test_inline_observer_refuses_an_unrecognized_native_layout():
    def unexpected(frame):
        return frame
    with pytest.raises(ValueError, match="refuses incomplete"):
        instrument_inline_function(unexpected, object())


def test_native_trace_checks_cash_identity_and_source_units():
    trace = NativeTrace("Trawma", "EURUSD", "a" * 64, "case", 15)
    treatment = {component: "not_applicable" for component in ("spread", "slippage", "carry", "commission", "conversion")}
    trace.start("LONG", 100, 99, 1, "2026-10-01T01:00Z", treatment, "Synthetic fixture")
    trace.exit("2026-10-01T02:00Z", 102, 1)
    with pytest.raises(ValueError, match="mismatch"):
        trace.finish(3, "2026-10-01T02:00Z")
    trace.finish(2, "2026-10-01T02:00Z")
    assert trace.document()["trades"][0]["account_currency"] == "NATIVE_MODEL_PRICE_UNIT"


def test_embedded_costs_cannot_silently_skip_a_stress_multiplier():
    trade = MeasuredTrade("Pragma", "EURUSD", "LONG", pd.Timestamp("2026-10-01T01:00Z"),
                          pd.Timestamp("2026-10-01T02:00Z"), 100, 99, 1, 0, "a" * 64,
                          "Synthetic fixture; all friction embedded in fills", True)
    assert replay([ReplayTrade("one", trade)])["net_pnl"] == 50
    with pytest.raises(ValueError, match="native producer re-execution"):
        replay([ReplayTrade("one", trade)], cost_multiplier=1.5)


def test_instant_native_settlements_cannot_finance_other_simultaneous_entries():
    instant = MeasuredTrade("Trawma", "EURUSD", "LONG", pd.Timestamp("2026-10-01T01:00Z"),
                            pd.Timestamp("2026-10-01T01:00Z"), 100, 99, 100, 0, "a" * 64,
                            "Synthetic instantaneous native event fixture")
    entries = [ReplayTrade(str(index), instant) for index in range(3)]
    with pytest.raises(ValueError, match="holding time"):
        replay(entries)
    result = replay(entries, allow_same_instant=True)
    assert result["accepted_ids"] == ["0", "1"]
    assert len(result["closes"]) == 2
    assert result["rejected"][0]["trade_id"] == "2"
