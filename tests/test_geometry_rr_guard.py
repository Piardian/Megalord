from datetime import datetime, timezone
import pandas as pd
import pytest

from megalord.live.mt5_executor import MegalordMT5Executor
from megalord.adapters.stream_loader import _parse_signal_time
from megalord.filters.signal_quality_filter import SignalQualityFilter
from megalord.firewall.risk_firewall import MegalordRiskFirewall
from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.types import TradeSignal, MathFamily, StrategyRegime


def test_valid_order_geometry_rejects_collapsed_rr():
    executor = MegalordMT5Executor(target_account=26735345, base_risk_pct=0.50)

    is_valid = executor._valid_order_geometry(
        direction="LONG",
        entry=191.674,
        sl=191.430,
        tp=191.680,
        min_rr=0.60,
    )
    assert not is_valid, "Executor must reject collapsed R:R geometry (50 cent TP)!"


def test_valid_order_geometry_accepts_healthy_rr():
    executor = MegalordMT5Executor(target_account=26735345, base_risk_pct=0.50)

    is_valid_tp1 = executor._valid_order_geometry(
        direction="LONG",
        entry=191.674,
        sl=191.430,
        tp=191.918,
        min_rr=0.60,
    )
    assert is_valid_tp1, "Executor must accept healthy 1.0R TP1 geometry."

    is_valid_tp2 = executor._valid_order_geometry(
        direction="LONG",
        entry=191.674,
        sl=191.430,
        tp=192.406,
        min_rr=1.20,
    )
    assert is_valid_tp2, "Executor must accept healthy 3.0R TP2 geometry."


def test_short_order_geometry_rejects_collapsed_rr():
    executor = MegalordMT5Executor(target_account=26735345, base_risk_pct=0.50)

    is_valid = executor._valid_order_geometry(
        direction="SHORT",
        entry=1.1000,
        sl=1.1020,
        tp=1.0999,
        min_rr=0.60,
    )
    assert not is_valid, "Short collapsed R:R must be rejected."


def test_parse_signal_time_handles_naive_local_timestamps():
    # Naive local timestamp should be converted from local system timezone to UTC when assume_local_if_naive=True
    now_local_naive = datetime.now().isoformat()
    parsed_utc = _parse_signal_time(now_local_naive, assume_local_if_naive=True)
    diff_sec = abs((datetime.now(timezone.utc) - parsed_utc.to_pydatetime()).total_seconds())
    assert diff_sec < 5.0, f"Naive local timestamp should map within 5s of UTC now, got diff={diff_sec}s"


def test_quality_filter_rejects_stale_asian_pool_in_ny_session():
    sqf = SignalQualityFilter()
    ny_time = pd.Timestamp("2026-10-21T14:30:00Z")
    sig = TradeSignal(
        signal_id="TEST_NY_ASIAN",
        engine_name="Begonya00",
        math_family=MathFamily.FAMILY_SMC,
        strategy_regime=StrategyRegime.MEAN_REVERSION,
        symbol="GBPUSD",
        direction="LONG",
        entry_time=ny_time,
        exit_time=None,
        entry_price=1.3000,
        sl_price=1.2980,
        tp1_price=1.3020,
        tp2_price=1.3040,
        metadata={
            "quality_context": "live",
            "pool_type": "ASIAN_LOW",
            "current_price": 1.3000,
            "spread_price": 0.0001,
        },
    )
    decision = sqf.evaluate(sig, now_utc=ny_time)
    assert not decision.allowed
    assert decision.action == "QUALITY_STALE_ASIAN_POOL_NY"


def test_risk_firewall_exempts_be_locked_positions_from_cluster_cap():
    fw = MegalordRiskFirewall()
    candidate = TradeSignal(
        signal_id="TEST_FW_BE",
        engine_name="Begonya00",
        math_family=MathFamily.FAMILY_SMC,
        strategy_regime=StrategyRegime.MEAN_REVERSION,
        symbol="EURUSD",
        direction="LONG",
        entry_time=pd.Timestamp.now(tz="UTC"),
        exit_time=None,
    )
    # 2 existing USD positions totaling 1.80R, but one is locked at Break-Even (be_moved=True)
    open_positions = [
        {"symbol": "GBPUSD", "allocated_risk_r": 1.0, "be_moved": True, "trailing_level": 0.0},
        {"symbol": "AUDUSD", "allocated_risk_r": 0.80, "be_moved": False, "trailing_level": 0.0},
    ]
    allowed, reason = fw.evaluate_entry(
        candidate_signal=candidate,
        allocated_risk_r=0.50,
        daily_realized_loss_r=0.0,
        open_positions=open_positions,
    )
    assert allowed, f"BE-locked position must contribute 0R to cluster cap, got: {reason}"


def test_arbiter_applies_grade_scaling_for_begonya00_aplus():
    arb = MegalordSignalArbiter()
    now_utc = pd.Timestamp("2026-10-21T10:00:00Z")
    sig_aplus = TradeSignal(
        signal_id="TEST_APLUS",
        engine_name="Begonya00",
        math_family=MathFamily.FAMILY_SMC,
        strategy_regime=StrategyRegime.MEAN_REVERSION,
        symbol="EURUSD",
        direction="LONG",
        entry_time=now_utc,
        exit_time=None,
        entry_price=1.0800,
        sl_price=1.0780,
        tp1_price=1.0820,
        tp2_price=1.0850,
        metadata={"grade": "A+ (2/3)", "pool_type": "PDL"},
    )
    dec = arb.evaluate_signal(sig_aplus, active_positions=[], allow_historical=True)
    assert dec.allowed
    assert dec.final_risk_mult == 0.80
