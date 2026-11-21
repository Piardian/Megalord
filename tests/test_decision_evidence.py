"""Causal entry checks must use the actual gates, without outcome bypasses."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pandas as pd
import pytest

from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.firewall.risk_firewall import MegalordRiskFirewall
from megalord.research.decision_evidence import capture_entry_context, finish_entry_context, replay_entry_context, audit_entry_archive
from megalord.research.trade_contract import ENGINES
from megalord.types import MathFamily, StrategyRegime, TradeSignal

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp("2026-10-22T10:00:00Z")
FAMILIES = {"Trawma": MathFamily.FAMILY_ORDERFLOW, "Pragma": MathFamily.FAMILY_AMT_VWAP,
            "Stigma": MathFamily.FAMILY_STAT_QUANT, "Begonya00": MathFamily.FAMILY_SMC,
            "ChecklistTrigger": MathFamily.FAMILY_SMC, "Begonya": MathFamily.FAMILY_SMC}


def config():
    return json.loads((ROOT / "config/megalord_config.json").read_text(encoding="utf-8"))


def signal(engine="Trawma", **changes):
    s = TradeSignal(engine, engine, FAMILIES[engine], StrategyRegime.TREND_MOMENTUM,
        "EURUSD", "LONG", NOW, None, entry_price=1.1, sl_price=1.09,
        tp1_price=1.11, tp2_price=1.125,
        metadata={"live_active": True, "current_price": 1.1, "spread_price": .0001})
    return replace(s, **changes)


def context(candidate=None, positions=None, cfg=None, **overrides):
    options = dict(decided_at=NOW, active_positions=positions or [], daily_realized_loss_r=0.,
                   loss_streaks={}, macro_bias="LONG_ONLY")
    options.update(overrides)
    return capture_entry_context(cfg or config(), candidate or signal(), **options)


def finish(row, news=False):
    # Independent direct production calls, not the replay helper.
    cfg = row["config"]
    fields = deepcopy(row["signal"])
    fields["entry_time"] = pd.Timestamp(fields["entry_time"])
    fields["math_family"] = MathFamily(fields["math_family"])
    fields["strategy_regime"] = StrategyRegime(fields["strategy_regime"])
    candidate = TradeSignal(**fields, exit_time=None)
    arbiter = MegalordSignalArbiter(cfg)
    arbiter.engine_loss_streaks = deepcopy(row["engine_loss_streaks"])
    dec = arbiter.evaluate_signal(candidate, row["active_positions"], macro_htf_bias=row["macro_bias"], now_utc=NOW)
    if not dec.allowed:
        return finish_entry_context(row, decision=dec)
    fw = MegalordRiskFirewall(cfg).evaluate_entry(candidate, dec.final_risk_mult,
         row["daily_realized_loss_r"], row["active_positions"])
    return finish_entry_context(row, decision=dec, news_check={"is_frozen": news, "reason": "fixture"},
        news_available_at=NOW, firewall_result=fw)


@pytest.mark.parametrize("engine", ENGINES)
def test_all_six_use_actual_configuration_and_gates(engine):
    row = finish(context(signal(engine)))
    assert row["gate_parity_equal"]
    settings = config()["allocator"]
    enabled = settings["engine_execution_enabled"][engine] and settings["base_engine_weights"][engine] > 0
    assert row["observed_allowed"] is enabled
    if not enabled:
        assert row["replayed"]["action"] == "ENGINE_DISABLED"


def test_completed_trade_and_future_origin_cannot_become_entry_evidence():
    with pytest.raises(ValueError, match="Completed"):
        context(signal(exit_time=NOW + pd.Timedelta(hours=1)))
    with pytest.raises(ValueError, match="later"):
        context(signal(entry_time=NOW + pd.Timedelta(minutes=1)))


def test_wrong_stop_is_rejected_even_if_supplied_trade_would_have_won():
    row = finish(context(signal(sl_price=1.12, raw_r_result=100)))
    assert row["replayed"]["action"] == "QUALITY_INVALID_SL_SIDE"
    assert row["gate_parity_equal"]


def test_decision_clock_enforces_age_instead_of_using_today_or_historical_bypass():
    row = finish(context(signal(entry_time=NOW - pd.Timedelta(hours=1))))
    assert row["replayed"]["action"] == "QUALITY_STALE_SIGNAL"


def test_outcomes_and_credentials_are_absent_but_all_gate_inputs_survive():
    cfg = config(); cfg["telegram"] = {"bot_token": "do-not-archive"}
    meta = {**signal().metadata, "hurst": .45, "r_result": 999, "closed_at": "future"}
    a = context(signal(metadata=meta, raw_r_result=999), cfg=cfg)
    b = context(signal(metadata={**meta, "r_result": -999}, raw_r_result=-999), cfg=cfg)
    assert a == b
    assert "do-not-archive" not in json.dumps(a)
    assert a["signal"]["metadata"]["hurst"] == .45
    assert "raw_r_result" not in a["signal"]


def test_hurst_natural_band_cooldown_and_clone_risk_are_preserved():
    s = signal("Stigma", strategy_regime=StrategyRegime.MEAN_REVERSION,
               metadata={**signal().metadata, "hurst": .45})
    row = finish(context(s, macro_bias="NATURAL_BAND", loss_streaks={"Stigma": 3}))
    assert row["gate_parity_equal"] and row["observed_allowed"]
    assert row["observed_arbiter"]["final_risk_mult"] == pytest.approx(.225)
    p = {"symbol": "EURUSD", "direction": "LONG", "engine_name": "Begonya00",
         "math_family": MathFamily.FAMILY_SMC.value, "risk_mult": .6}
    row = finish(context(signal("ChecklistTrigger"), positions=[p]))
    assert row["replayed"]["action"] == "CLONE_CAPPED"
    assert not row["observed_allowed"] and row["gate_parity_equal"]


def test_news_missing_is_not_unfrozen_and_news_veto_is_replayed():
    with pytest.raises(ValueError, match="news result"):
        replay_entry_context(context())
    row = finish(context(), news=True)
    assert row["replayed"]["stage"] == "NEWS" and not row["observed_allowed"]


def test_actual_firewall_consumes_observed_daily_losses():
    row = finish(context(daily_realized_loss_r=2.4))
    assert row["gate_parity_equal"]
    assert row["replayed"]["action"] == "FIREWALL_BLOCKED"


def test_clone_cap_uses_actual_daemon_allocation_instead_of_default_one_r():
    pos = {"symbol": "EURUSD", "direction": "LONG", "engine_name": "Begonya00",
           "math_family": MathFamily.FAMILY_SMC.value, "allocated_risk_r": .5}
    row = finish(context(signal("ChecklistTrigger"), positions=[pos]))
    assert row["gate_parity_equal"] and row["observed_allowed"]
    assert row["observed_arbiter"]["final_risk_mult"] == pytest.approx(.1)
    # Stale legacy aliases must not override the actual canonical allocation.
    row2 = finish(context(signal("ChecklistTrigger"), positions=[{**pos, "risk_mult": 99, "risk_r": 99}]))
    assert row2["observed_arbiter"] == row["observed_arbiter"]


@pytest.mark.parametrize("alias", ["NAS100", "US100", "SPX500", "SPX"])
def test_index_aliases_share_the_same_actual_currency_and_asset_risk_cap(alias):
    pos = {"symbol": alias, "direction": "LONG", "engine_name": "Trawma",
           "math_family": MathFamily.FAMILY_ORDERFLOW.value, "allocated_risk_r": .5}
    cfg = config(); cfg["firewall"]["currency_cluster_caps"]["INDEX"] = .6
    # USD has ample room, so failure must come from the common INDEX cluster.
    cfg["firewall"]["currency_cluster_caps"]["USD"] = 99
    row = finish(context(signal("Pragma", symbol="NAS100"), positions=[pos], cfg=cfg))
    assert MegalordRiskFirewall.resolve_cluster_legs(alias) == ["INDEX", "USD"]
    assert row["replayed"]["action"] == "FIREWALL_BLOCKED"
    assert "INDEX" in row["replayed"]["reason"]
    assert row["gate_parity_equal"]


def test_mutated_code_config_and_injected_outcomes_refuse_replay():
    row = finish(context())
    bad = deepcopy(row); bad["config"]["allocator"]["clone_confluence_cap"] = 99
    with pytest.raises(ValueError, match="fingerprint"):
        replay_entry_context(bad)
    bad = deepcopy(row); bad["gate_code_sha256"]["types.py"] = "0" * 64
    with pytest.raises(ValueError, match="source changed"):
        replay_entry_context(bad)
    bad = deepcopy(row); bad["signal"]["raw_r_result"] = 99
    with pytest.raises(ValueError, match="future outcomes"):
        replay_entry_context(bad)


def test_daemon_archives_six_engine_decisions_without_broker_or_network(tmp_path, monkeypatch):
    from datetime import datetime
    from megalord import megalord_daemon as module
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.to_pydatetime() if tz else NOW.to_pydatetime().replace(tzinfo=None)
    monkeypatch.setattr(module, "datetime", Frozen)
    cfg = tmp_path / "config.json"; cfg.write_text(json.dumps(config()), encoding="utf-8")
    daemon = module.MegalordDaemon(cfg, tmp_path / "state.json", notifier=type("Silent", (), {"send_alert": lambda *a: False})())
    daemon.poll_new_incoming_signals = lambda: [signal(e) for e in ENGINES]
    daemon.fetch_htf_macro_gates = lambda: {"EURUSD": "LONG_ONLY"}
    daemon.news_guard.check_news_freeze = lambda symbol: {"is_frozen": False, "reason": "fixture only"}
    daemon._dispatch_alert = lambda *a, **k: None
    daemon.mt5_executor.ensure_connected = lambda: pytest.fail("Broker operation")
    result = daemon.execute_cycle()
    rows = [json.loads(line) for line in daemon.entry_evidence_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 6 and result["entry_evidence"] == {"captured": 6, "errors": 0, "mismatches": 0, "compass_excluded": 0}
    assert all(row["gate_parity_equal"] for row in rows)
    assert {row["signal"]["engine_name"] for row in rows} == set(ENGINES)
    assert not any(row["live_approved"] for row in rows)


def test_archive_io_failure_does_not_change_gate_decision(tmp_path, monkeypatch):
    from megalord import megalord_daemon as module
    cfg = tmp_path / "config.json"; cfg.write_text(json.dumps(config()), encoding="utf-8")
    daemon = module.MegalordDaemon(cfg, tmp_path / "state.json")
    row = context()
    dec = MegalordSignalArbiter(row["config"]).evaluate_signal(signal(), [], macro_htf_bias="LONG_ONLY", now_utc=NOW)
    original = deepcopy(dec)
    def broken(*args):
        raise OSError("fixture disk full")
    monkeypatch.setattr(module, "append_entry_evidence", broken)
    daemon._archive_entry_context(row, dec, news_check={"is_frozen": False}, news_available_at=NOW,
        firewall_result=(True, "FIREWALL_PASS"))
    assert dec == original
    assert daemon.entry_evidence_counts == {"captured": 0, "errors": 1, "mismatches": 0, "compass_excluded": 0}


def test_archive_recomputes_parity_and_preserves_retries_and_bad_lines(tmp_path):
    row = finish(context())
    tampered = deepcopy(row)
    tampered["observed_allowed"] = False
    tampered["gate_parity_equal"] = True  # A success label cannot certify parity.
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join([json.dumps(row), json.dumps(row), json.dumps(tampered), '{"partial":']), encoding="utf-8")
    report = audit_entry_archive(path)
    assert report["records"] == 4 and report["parity_equal"] == 2
    assert len(report["mismatches"]) == len(report["invalid"]) == 1
    assert report["engines"]["Trawma"]["unique_signals"] == 1
    assert not report["performance_evaluated"]


@pytest.mark.parametrize("scenario", ["all_six", "wrong_stop", "news", "budget"])
def test_passive_archive_preserves_entire_daemon_decision_and_state(tmp_path, monkeypatch, scenario):
    from datetime import datetime
    from megalord import megalord_daemon as module
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.to_pydatetime() if tz else NOW.to_pydatetime().replace(tzinfo=None)
    monkeypatch.setattr(module, "datetime", Frozen)
    cfg = tmp_path / "config.json"; cfg.write_text(json.dumps(config()), encoding="utf-8")
    results, states = [], []
    for enabled in (False, True):
        daemon = module.MegalordDaemon(cfg, tmp_path / f"state-{enabled}.json")
        candidates = [signal(e) for e in ENGINES] if scenario == "all_six" else [signal(sl_price=1.12) if scenario == "wrong_stop" else signal()]
        daemon.poll_new_incoming_signals = lambda: candidates
        daemon.fetch_htf_macro_gates = lambda: {"EURUSD": "LONG_ONLY"}
        daemon.news_guard.check_news_freeze = lambda symbol: {"is_frozen": scenario == "news", "reason": "fixture only"}
        daemon._dispatch_alert = lambda *a, **k: None
        daemon.mt5_executor.ensure_connected = lambda: pytest.fail("Broker operation")
        if scenario == "budget":
            daemon.state["daily_realized_loss_r"] = 2.4
        if not enabled:
            daemon._entry_context = lambda *a, **k: None
        result = daemon.execute_cycle()
        result.pop("entry_evidence")
        results.append(result); states.append(deepcopy(daemon.state))
    assert results[0] == results[1]
    assert states[0] == states[1]
