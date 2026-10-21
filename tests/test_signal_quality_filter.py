import pandas as pd

from megalord.filters.signal_quality_filter import SignalQualityFilter
from megalord.types import MathFamily, StrategyRegime, TradeSignal


def make_signal(**overrides):
    values = dict(
        signal_id="test-1", engine_name="Begonya00", math_family=MathFamily.FAMILY_SMC,
        strategy_regime=StrategyRegime.TREND_MOMENTUM, symbol="EURUSD", direction="LONG",
        entry_time=pd.Timestamp.now(tz="UTC"), exit_time=None, entry_price=1.1000,
        sl_price=1.0990, tp1_price=1.1010, tp2_price=1.1030,
        metadata={"quality_context": "live"},
    )
    values.update(overrides)
    return TradeSignal(**values)


def test_quality_filter_accepts_valid_signal():
    result = SignalQualityFilter({"enabled": True}).evaluate(make_signal())
    assert result.allowed is True
    assert result.action == "QUALITY_PASS"
    assert result.metrics["tp2_rr"] == 3.0


def test_quality_filter_fails_closed_when_stop_is_missing():
    result = SignalQualityFilter({"enabled": True}).evaluate(make_signal(sl_price=0.0))
    assert result.allowed is False
    assert result.action == "QUALITY_MISSING_SL"


def test_quality_filter_rejects_stop_on_wrong_side():
    result = SignalQualityFilter({"enabled": True}).evaluate(make_signal(sl_price=1.1010))
    assert result.allowed is False
    assert result.action == "QUALITY_INVALID_SL_SIDE"


def test_quality_filter_rejects_low_reward_risk():
    result = SignalQualityFilter({"enabled": True, "min_tp2_rr": 2.0}).evaluate(make_signal(tp2_price=1.1010))
    assert result.allowed is False
    assert result.action == "QUALITY_LOW_TP2_RR"


def test_quality_filter_rejects_excessive_spread_to_stop():
    signal = make_signal(metadata={"quality_context": "live", "spread_price": 0.0002})
    result = SignalQualityFilter({"enabled": True, "max_spread_to_sl_ratio": 0.1}).evaluate(signal)
    assert result.allowed is False
    assert result.action == "QUALITY_SPREAD_TOO_HIGH"


def test_quality_filter_rejects_large_entry_drift():
    signal = make_signal(metadata={"quality_context": "live", "current_price": 1.1005})
    result = SignalQualityFilter({"enabled": True, "max_entry_drift_r": 0.25}).evaluate(signal)
    assert result.allowed is False
    assert result.action == "QUALITY_ENTRY_DRIFT"


def test_quality_filter_rejects_trend_signal_against_supplied_htf_bias():
    signal = make_signal(metadata={"quality_context": "live", "htf_bias": "SHORT"})
    result = SignalQualityFilter({"enabled": True}).evaluate(signal)
    assert result.allowed is False
    assert result.action == "QUALITY_HTF_CONFLICT"


def test_completed_historical_trade_is_not_reclassified_as_new_entry():
    signal = make_signal(exit_time=pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=2), sl_price=0.0)
    result = SignalQualityFilter({"enabled": True}).evaluate(signal)
    assert result.allowed is True
    assert result.action == "HISTORICAL_TRADE_BYPASS"
