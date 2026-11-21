"""Exercise native source trackers offline; observations must not change them."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest

from megalord.research.event_import import import_event_document
from megalord.research.producer_events import PaperEventObserver, checklist_events

SOURCES = {
    "Begonya00": Path(r"C:\Users\piard\Desktop\Begonya00\sfp_orb_engine"),
    "Begonya": Path(r"C:\Users\piard\.gemini\antigravity\scratch\Begonya\sfp_orb_engine"),
}
NOW = pd.Timestamp("2026-10-23T10:00Z")


def native_module(engine):
    path = SOURCES[engine]
    if not (path / "outcome_tracker.py").exists():
        pytest.skip("Native sibling source not present")
    package_name = "_megalord_native_" + engine.lower()
    if package_name not in sys.modules:
        package = ModuleType(package_name); package.__path__ = [str(path)]
        sys.modules[package_name] = package
    return importlib.import_module(package_name + ".outcome_tracker")


def inert_tracker(module):
    tracker = module.OutcomeTracker.__new__(module.OutcomeTracker)
    tracker.active_trades, tracker.closed_trades = [], []
    tracker._save_json = lambda *a: None
    tracker._notify_lifecycle = lambda *a: None
    return tracker


@pytest.mark.parametrize("engine", ["Begonya", "Begonya00"])
@pytest.mark.parametrize("path", ["stop", "target", "partial_be", "trailing"])
def test_native_paper_observer_preserves_state_and_reconciles_events(engine, path, monkeypatch):
    module = native_module(engine)
    clock = {"now": NOW}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].to_pydatetime() if tz else clock["now"].to_pydatetime().replace(tzinfo=None)
    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock["now"].timestamp()))
    monkeypatch.setattr(module, "is_market_open", lambda *a: True)
    # A deliberate lifecycle fixture (no signal discovery claim): bypass the
    # separate entry-reaction rule identically in control and observed runs.
    if engine == "Begonya00":
        monkeypatch.setattr(module.config, "REQUIRE_ENTRY_REACTION", False)
    native, observed = inert_tracker(module), inert_tracker(module)
    digest = sha256((SOURCES[engine] / "outcome_tracker.py").read_bytes()).hexdigest()
    observer = PaperEventObserver(observed, engine, digest)
    signal = {"symbol": "EUR/USD", "strategy": "FIXTURE_ONLY", "direction": "LONG",
              "entry": 1.10, "stop_loss": 1.09, "tp1": 1.11, "tp2": 1.13,
              "risk_pips": .01, "rr_ratio": 3., "tp1_r": 1., "datetime": NOW.isoformat()}
    native.register_trade(deepcopy(signal))
    observer.register(deepcopy(signal), NOW)
    bars = [(1.1005, 1.101, 1.099, 1.1005)]  # first passive penetration
    bars += {
        "stop": [(1.10, 1.101, 1.089, 1.095)],
        "target": [(1.10, 1.132, 1.10, 1.131)],
        "partial_be": [(1.10, 1.112, 1.10, 1.111), (1.111, 1.112, 1.099, 1.101)],
        "trailing": [(1.10, 1.112, 1.10, 1.111), (1.111, 1.117, 1.111, 1.116),
                     (1.116, 1.117, 1.099, 1.101)],
    }[path]
    for index, values in enumerate(bars):
        start = NOW + pd.Timedelta(minutes=index)
        clock["now"] = start + pd.Timedelta(minutes=1)
        bar = dict(zip(("open", "high", "low", "close"), values), datetime=start.isoformat())
        native.check_trade_progress(signal["symbol"], deepcopy(bar))
        observer.step(signal["symbol"], deepcopy(bar), clock["now"])
        assert native.active_trades == observed.active_trades
        assert native.closed_trades == observed.closed_trades
    document = observer.document()
    assert len(document["trades"]) == 1 and document["native_reconciliation"][0]["reconciled"]
    events = document["trades"][0]["events"]
    assert sum(r["quantity"] for r in events if r["kind"] == "EXIT") == 1
    imported = import_event_document(json.dumps(document).encode())
    # Correct model identity does not certify unknown venue carry/spread.
    assert not imported["closed"] and len(imported["rejected"]) == 1
    assert "cost coverage" in imported["rejected"][0]["reason"]


def test_clt_bridge_uses_actual_exit_price_including_gap_loss():
    candidate = {"signalId": "fixture", "symbol": "EURUSD", "tradeDirection": "long"}
    outcome = {"status": "COMPLETED", "entryTriggeredAt": 1792749600000,
        "entryPrice": 1.1, "exitPrice": 1.085, "rrAchieved": -1.5,
        "outcome": {"timestamp": 1792750500000}, "plan": {"stopPrice": 1.09, "riskDistance": .01}}
    row = checklist_events(candidate, outcome, "a" * 64, bar_minutes=15)
    assert row["events"][1]["gross_cash"] == pytest.approx(-.015)
    raw = {"schema_version": 1, "source_kind": "native_replay", "trades": [row]}
    result = import_event_document(json.dumps(raw).encode())
    assert len(result["rejected"]) == 1
    assert "cost coverage" in result["rejected"][0]["reason"]
    outcome["rrAchieved"] = 2
    with pytest.raises(ValueError, match="price outcome"):
        checklist_events(candidate, outcome, "a" * 64, bar_minutes=15)


def test_closed_snapshot_with_old_fill_time_cannot_create_a_missing_entry():
    tracker = SimpleNamespace(active_trades=[{"id": "old", "symbol": "EURUSD", "status": "OPEN",
        "filled_at": "2026-10-01T10:00Z"}], closed_trades=[], check_trade_progress=lambda *a: None)
    observer = PaperEventObserver(tracker, "Begonya00", "a" * 64)
    with pytest.raises(ValueError, match="attached after a fill"):
        observer.step("EURUSD", {}, NOW)
