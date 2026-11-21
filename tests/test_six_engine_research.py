from dataclasses import replace
import json

import pandas as pd
import pytest

from megalord.adapters.stream_loader import ProjectStreamLoader
from megalord.research.portfolio_replay import ReplayTrade, comparison_family, replay
from megalord.research.source_book import import_sources, observation
from megalord.research.trade_contract import ENGINES, MeasuredTrade


def item(name, engine="Trawma", entry="2026-10-01T01:00Z", exit="2026-10-01T02:00Z", gross=1., cost=.1):
    return ReplayTrade(name, MeasuredTrade(engine, "EURUSD", "LONG", pd.Timestamp(entry),
                                          pd.Timestamp(exit), 1.1, 1.09, gross, cost,
                                          "a" * 64, "Explicit synthetic test cost, not market evidence"))


def test_shared_budget_does_not_scale_with_number_of_engines():
    trades = [item(str(index), engine) for index, engine in enumerate(ENGINES)]
    result = replay(trades, risk_fraction=.005, max_open_risk_fraction=.01)
    assert len(result["accepted_ids"]) == 2
    assert len(result["rejected"]) == 4
    assert result["net_pnl"] == pytest.approx(90)


def test_future_outcome_does_not_change_simultaneous_entry_selection():
    original = [item("a"), item("b"), item("c")]
    altered = [replace(trade, evidence=replace(trade.evidence, gross_r=-100)) for trade in original]
    assert replay(original)["accepted_ids"] == replay(altered)["accepted_ids"]


def test_cost_stress_is_not_double_charged_and_cash_risk_is_fixed_at_entry():
    result = replay([item("a", gross=2, cost=.2)], cost_multiplier=1.5)
    assert result["net_pnl"] == pytest.approx(85)
    assert result["closes"][0]["capital_risk"] == 50


def test_close_releases_budget_before_next_entry_at_same_timestamp():
    result = replay([item("a"), item("b", entry="2026-10-01T02:00Z", exit="2026-10-01T03:00Z")],
                    max_open_risk_fraction=.005)
    assert result["accepted_ids"] == ["a", "b"]
    assert result["closes"][1]["capital_risk"] == pytest.approx(50.225)


def test_duplicate_ids_and_zero_duration_cannot_manipulate_replay():
    with pytest.raises(ValueError, match="Duplicate"):
        replay([item("same"), item("same")])
    with pytest.raises(ValueError, match="holding time"):
        replay([item("instant", exit="2026-10-01T01:00Z")])


def test_thirteen_comparisons_require_all_six_and_one_declared_window():
    options = {"window_start": "2026-10-01T00:00Z", "window_end": "2026-10-02T00:00Z"}
    with pytest.raises(ValueError, match="missing measured"):
        comparison_family([item("only")], **options)
    trades = [item(engine, engine) for engine in ENGINES]
    results = comparison_family(trades, **options)
    assert len(results) == 13
    assert all(result["live_approved"] is False for result in results.values())
    with pytest.raises(ValueError, match="common comparison window"):
        comparison_family(trades, window_start="2026-10-02T00:00Z", window_end="2026-10-03T00:00Z")


def test_observation_preserves_zero_and_refuses_unknown_net_costs():
    row = {"symbol": "EUR/USD", "direction": "LONG", "timestamp": 1787784300000,
           "realizedR": 0, "actualOutcome": "TP"}
    parsed = observation("ChecklistTrigger", row, "a" * 64, 0)
    assert parsed["native_r"] == 0
    assert pd.Timestamp(parsed["entry_time"]).year == 2026
    assert parsed["exit_time"] is None
    assert parsed["net_performance_eligible"] is False


def test_legacy_timezone_must_be_explicit_and_cost_identity_checked():
    row = {"symbol": "EURUSD", "direction": "LONG", "opened_at": "2026-10-01 10:00",
           "closed_at": "2026-10-01 11:00", "r_result": 1, "entry": 1.1, "original_sl": 1.09,
           "gross_r": 1, "net_r": 1, "fee_r": .1}
    unresolved = observation("Begonya", row, "a" * 64, 0)
    assert "invalid_or_ambiguous_entry_time" in unresolved["issues"]
    resolved = observation("Begonya00", row, "a" * 64, 0, legacy_timezone="Etc/GMT-3")
    assert resolved["entry_time"] == "2026-10-01T07:00:00+00:00"
    assert "inconsistent_cost_identity" in resolved["issues"]


def test_begonya_imports_only_one_year_not_overlapping_windows(tmp_path):
    path = tmp_path / "begonya.json"
    path.write_text(json.dumps({"1. SON 1 AY": {"trades": [{}]},
                                "3. SON 1 YIL": {"trades": [{"r_result": 0}]}}), encoding="utf-8")
    report = import_sources({"Begonya": [(path, "one_year")]})
    assert report["coverage"]["Begonya"]["records"] == 1
    assert set(report["coverage"]) == set(ENGINES)
    assert "entry_fill_time_unrecorded" in report["observations"][0]["issues"]


def test_checklist_bridge_cannot_create_exit_or_winning_result(tmp_path):
    path = tmp_path / "checklist.json"
    rows = [
        {"symbol": "EURUSD", "timestamp": 1787784300000, "actualOutcome": "TP"},
        {"symbol": "EURUSD", "timestamp": 1787784300000, "exitTimestamp": 1787787900000,
         "realizedR": 0, "direction": "LONG", "actualOutcome": "TP"},
        {"symbol": "EURUSD", "timestamp": 1787784300000, "exitTimestamp": 1787787900000,
         "realizedR": None, "actualOutcome": "SL"},
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    loader = ProjectStreamLoader()
    loader.checklist_path = path
    result = loader.load_checklist_trigger()
    assert len(result) == 1
    assert result[0].raw_r_result == 0
    assert result[0].entry_time.year == 2026
    assert result[0].exit_time - result[0].entry_time == pd.Timedelta(hours=1)


def test_begonya_bridge_handles_actual_archive_fields_without_guessing_timezone(tmp_path):
    path = tmp_path / "begonya.json"
    path.write_text(json.dumps({"3. SON 1 YIL": {"trades": [{
        "symbol": "EURUSD", "opened_at": "2026-10-01 10:00", "closed_at": "2026-10-01 11:00",
        "direction": "LONG", "r_result": 0,
    }]}}), encoding="utf-8")
    loader = ProjectStreamLoader()
    loader.begonya_path = path
    assert loader.load_begonya(source_timezone=None) == []
    result = loader.load_begonya(source_timezone="UTC")
    assert len(result) == 1
    assert result[0].raw_r_result == 0
    assert result[0].exit_time is not None
