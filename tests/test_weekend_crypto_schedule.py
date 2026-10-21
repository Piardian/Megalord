from datetime import datetime, timezone
import pandas as pd
import pytest

from megalord.filters.signal_quality_filter import SignalQualityFilter
from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.firewall.risk_firewall import MegalordRiskFirewall
from megalord.adapters.stream_loader import norm_symbol, WEEKEND_CRYPTO_WHITELIST
from megalord.types import TradeSignal, MathFamily, StrategyRegime


ALL_8_CRYPTO_SYMBOLS = [
    "XRPUSD", "BTCUSD", "ETHUSD", "LTCUSD", "SOLUSD", "DOGUSD", "BTCEUR", "ETHEUR"
]


def test_weekend_session_timing():
    sqf = SignalQualityFilter({
        "weekend_trading": {
            "enabled": True,
            "friday_close_hour_utc": 21,
            "sunday_open_hour_utc": 21,
        }
    })

    # Friday 20:30 UTC -> Market open
    fri_open = pd.Timestamp("2026-10-23 20:30:00", tz="UTC")
    assert not sqf.is_weekend_session(fri_open)

    # Friday 21:05 UTC -> Weekend active
    fri_closed = pd.Timestamp("2026-10-23 21:05:00", tz="UTC")
    assert sqf.is_weekend_session(fri_closed)

    # Saturday 14:00 UTC -> Weekend active
    sat = pd.Timestamp("2026-10-24 14:00:00", tz="UTC")
    assert sqf.is_weekend_session(sat)

    # Sunday 18:00 UTC -> Weekend active
    sun_closed = pd.Timestamp("2026-10-25 18:00:00", tz="UTC")
    assert sqf.is_weekend_session(sun_closed)

    # Sunday 21:15 UTC -> Market reopened
    sun_open = pd.Timestamp("2026-10-25 21:15:00", tz="UTC")
    assert not sqf.is_weekend_session(sun_open)

    # Wednesday 10:00 UTC -> Weekday market open
    wed = pd.Timestamp("2026-10-21 10:00:00", tz="UTC")
    assert not sqf.is_weekend_session(wed)


def test_weekend_filter_rejects_forex_metals_indices():
    sqf = SignalQualityFilter()
    saturday_noon = pd.Timestamp("2026-10-24 12:00:00", tz="UTC")

    for closed_sym in ("EURUSD", "GBPUSD", "USDJPY", "CHFJPY", "XAUUSD", "NAS100"):
        sig = TradeSignal(
            signal_id=f"TEST_{closed_sym}",
            engine_name="Begonya00",
            math_family=MathFamily.FAMILY_SMC,
            strategy_regime=StrategyRegime.MEAN_REVERSION,
            symbol=closed_sym,
            direction="LONG",
            entry_time=saturday_noon,
            exit_time=None,
            entry_price=1.2000,
            sl_price=1.1980,
            tp1_price=1.2020,
            tp2_price=1.2040,
        )
        res = sqf.evaluate(sig, now_utc=saturday_noon)
        assert not res.allowed
        assert res.action == "QUALITY_WEEKEND_MARKET_CLOSED"
        assert closed_sym in res.reason


def test_weekend_filter_allows_all_8_crypto_symbols_when_conditions_met():
    sqf = SignalQualityFilter()
    saturday_noon = pd.Timestamp("2026-10-24 12:00:00", tz="UTC")

    for crypto_sym in ALL_8_CRYPTO_SYMBOLS:
        entry_p = 50000.0 if "BTC" in crypto_sym else (2500.0 if "ETH" in crypto_sym else 100.0)
        sl_p = entry_p * 0.99
        tp1_p = entry_p + (entry_p - sl_p) * 1.0
        tp2_p = entry_p + (entry_p - sl_p) * 2.0

        sig = TradeSignal(
            signal_id=f"TEST_{crypto_sym}",
            engine_name="Trawma",
            math_family=MathFamily.FAMILY_ORDERFLOW,
            strategy_regime=StrategyRegime.TREND_MOMENTUM,
            symbol=crypto_sym,
            direction="LONG",
            entry_time=saturday_noon,
            exit_time=None,
            entry_price=entry_p,
            sl_price=sl_p,
            tp1_price=tp1_p,
            tp2_price=tp2_p,
            metadata={"status": "OPEN"},
        )
        res = sqf.evaluate(sig, now_utc=saturday_noon)
        assert res.allowed, f"Crypto {crypto_sym} must be allowed on weekends! Reason: {res.reason}"
        assert res.metrics.get("weekend_crypto_verified") is True


def test_crypto_cluster_resolution():
    for crypto_sym in ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "LTCUSD", "DOGUSD"):
        legs = MegalordRiskFirewall.resolve_cluster_legs(crypto_sym)
        assert "CRYPTO" in legs
        assert "USD" in legs

    for eur_crypto in ("BTCEUR", "ETHEUR"):
        legs = MegalordRiskFirewall.resolve_cluster_legs(eur_crypto)
        assert "CRYPTO" in legs
        assert "EUR" in legs


def test_doge_symbol_normalization():
    assert norm_symbol("DOGEUSD") == "DOGUSD"
    assert norm_symbol("DOGEUSDT") == "DOGUSD"
    assert norm_symbol("DOGUSD") == "DOGUSD"
    assert norm_symbol("BTCUSDT") == "BTCUSD"
