"""Deterministic pre-trade signal quality checks.

The filter uses only fields supplied by the signal producer. Missing optional
market-quality fields are reported as unavailable, never fabricated. Completed
historical trades are intentionally not re-filtered as if they were new entries.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from megalord.types import StrategyRegime, TradeSignal


@dataclass
class SignalQualityResult:
    allowed: bool
    score: int
    action: str
    reason: str
    metrics: Dict[str, Any] = field(default_factory=dict)


class SignalQualityFilter:
    """Fail-closed validation plus configurable quality gates for new entries."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.max_signal_age_seconds = float(cfg.get("max_signal_age_seconds", 900))
        self.max_spread_to_sl_ratio = float(cfg.get("max_spread_to_sl_ratio", 0.10))
        self.max_entry_drift_r = float(cfg.get("max_entry_drift_r", 0.25))
        self.min_tp2_rr = float(cfg.get("min_tp2_rr", 1.50))
        self.min_score = int(cfg.get("min_score", 60))
        self.require_sl = bool(cfg.get("require_sl", True))
        self.require_entry = bool(cfg.get("require_entry", True))
        self.veto_trend_htf_conflict = bool(cfg.get("veto_trend_htf_conflict", True))

    @staticmethod
    def _finite_positive(value: Any) -> bool:
        try:
            number = float(value)
            return math.isfinite(number) and number > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _age_seconds(signal: TradeSignal) -> Optional[float]:
        try:
            now = __import__("pandas").Timestamp.now(tz="UTC")
            ts = __import__("pandas").Timestamp(signal.entry_time)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            return float((now - ts).total_seconds())
        except Exception:
            return None

    def evaluate(self, signal: TradeSignal, *, now_utc: Any = None, allow_historical: bool = False) -> SignalQualityResult:
        if not self.enabled:
            return SignalQualityResult(True, 100, "QUALITY_FILTER_DISABLED", "Signal quality filter is disabled.")

        # Completed trades are outcome observations, not live entry candidates.
        # Backtests may explicitly opt into bypassing entry-only checks because legacy
        # historical records often do not retain entry/SL/TP prices.
        if signal.exit_time is not None:
            if allow_historical:
                return SignalQualityResult(True, 100, "HISTORICAL_TRADE_BYPASS", "Historical simulation explicitly bypassed entry-only quality checks.")
            return SignalQualityResult(False, 0, "QUALITY_NOT_AN_ENTRY", "Completed trade record cannot be executed as a new entry.")

        metrics: Dict[str, Any] = {}
        score = 100
        direction = str(signal.direction).upper()
        if direction not in ("LONG", "SHORT"):
            return SignalQualityResult(False, 0, "QUALITY_INVALID_DIRECTION", f"Unsupported direction: {signal.direction!r}.", metrics)

        entry = signal.entry_price
        stop = signal.sl_price
        if self.require_entry and not self._finite_positive(entry):
            return SignalQualityResult(False, 0, "QUALITY_MISSING_ENTRY", "Entry price is missing or invalid; refusing to infer an entry price.", metrics)
        if self.require_sl and not self._finite_positive(stop):
            return SignalQualityResult(False, 0, "QUALITY_MISSING_SL", "Stop-loss price is missing or invalid; new entry blocked.", metrics)

        if self._finite_positive(entry) and self._finite_positive(stop):
            entry_f, stop_f = float(entry), float(stop)
            if (direction == "LONG" and stop_f >= entry_f) or (direction == "SHORT" and stop_f <= entry_f):
                return SignalQualityResult(False, 0, "QUALITY_INVALID_SL_SIDE", "Stop-loss is not on the loss side of the entry.", {"entry_price": entry_f, "sl_price": stop_f})
            stop_dist = abs(entry_f - stop_f)
            if not math.isfinite(stop_dist) or stop_dist <= 0:
                return SignalQualityResult(False, 0, "QUALITY_INVALID_SL_DISTANCE", "Stop-loss distance is zero or invalid.", metrics)
            metrics["stop_distance"] = stop_dist

            tp2 = getattr(signal, "tp2_price", 0.0)
            if self._finite_positive(tp2):
                reward = float(tp2) - entry_f if direction == "LONG" else entry_f - float(tp2)
                if reward <= 0:
                    return SignalQualityResult(False, 0, "QUALITY_INVALID_TP2_SIDE", "TP2 is not on the profit side of entry.", {"tp2_price": float(tp2)})
                rr = reward / stop_dist
                metrics["tp2_rr"] = round(rr, 4)
                if rr < self.min_tp2_rr:
                    return SignalQualityResult(False, 0, "QUALITY_LOW_TP2_RR", f"TP2 reward/risk {rr:.2f} is below minimum {self.min_tp2_rr:.2f}.", metrics)
            else:
                metrics["tp2_rr"] = None

            metadata = signal.metadata or {}
            spread_price = metadata.get("spread_price")
            if spread_price is None and metadata.get("spread_points") is not None and metadata.get("point") is not None:
                try:
                    spread_price = float(metadata["spread_points"]) * float(metadata["point"])
                except (TypeError, ValueError):
                    spread_price = None
            if spread_price is not None:
                try:
                    spread_ratio = float(spread_price) / stop_dist
                    metrics["spread_to_sl_ratio"] = round(spread_ratio, 6)
                    if not math.isfinite(spread_ratio) or spread_ratio < 0 or spread_ratio > self.max_spread_to_sl_ratio:
                        return SignalQualityResult(False, 0, "QUALITY_SPREAD_TOO_HIGH", f"Spread/SL ratio {spread_ratio:.3f} exceeds allowed {self.max_spread_to_sl_ratio:.3f}.", metrics)
                except (TypeError, ValueError):
                    return SignalQualityResult(False, 0, "QUALITY_INVALID_SPREAD", "Spread metadata is invalid.", metrics)
            else:
                metrics["spread_to_sl_ratio"] = None

            current_price = metadata.get("current_price")
            if current_price is not None:
                try:
                    drift_r = abs(float(current_price) - entry_f) / stop_dist
                    metrics["entry_drift_r"] = round(drift_r, 4)
                    if not math.isfinite(drift_r) or drift_r > self.max_entry_drift_r:
                        return SignalQualityResult(False, 0, "QUALITY_ENTRY_DRIFT", f"Current price drift {drift_r:.2f}R exceeds {self.max_entry_drift_r:.2f}R.", metrics)
                except (TypeError, ValueError):
                    return SignalQualityResult(False, 0, "QUALITY_INVALID_CURRENT_PRICE", "Current-price metadata is invalid.", metrics)
            else:
                metrics["entry_drift_r"] = None

        # Signal freshness is enforced for live-origin signals; historical records
        # and backtest events must not be rejected based on today's wall clock.
        metadata = signal.metadata or {}
        if metadata.get("live_active") or metadata.get("quality_context") == "live":
            age = self._age_seconds(signal) if now_utc is None else self._age_seconds_with_now(signal, now_utc)
            metrics["signal_age_seconds"] = None if age is None else round(age, 2)
            if age is None or age < 0 or age > self.max_signal_age_seconds:
                return SignalQualityResult(False, 0, "QUALITY_STALE_SIGNAL", f"Live signal age is invalid or exceeds {self.max_signal_age_seconds:.0f}s.", metrics)

        htf_bias = str(metadata.get("htf_bias", "")).upper()
        if htf_bias in ("LONG", "SHORT") and htf_bias != direction:
            if signal.strategy_regime == StrategyRegime.TREND_MOMENTUM and self.veto_trend_htf_conflict:
                return SignalQualityResult(False, 0, "QUALITY_HTF_CONFLICT", f"Trend signal direction {direction} conflicts with supplied HTF bias {htf_bias}.", metrics)
            score -= 20
            metrics["htf_alignment"] = False
        elif htf_bias in ("LONG", "SHORT"):
            metrics["htf_alignment"] = True
        else:
            metrics["htf_alignment"] = None

        # Optional signal-quality metadata is scored only when producers supply it.
        # No ATR/regime/price-action feature is inferred from absent data.
        if metrics.get("spread_to_sl_ratio") is not None:
            ratio = metrics["spread_to_sl_ratio"]
            if ratio <= self.max_spread_to_sl_ratio * 0.5:
                score += 0
            else:
                score -= 10
        if metrics.get("entry_drift_r") is not None and metrics["entry_drift_r"] > self.max_entry_drift_r * 0.5:
            score -= 10

        metrics["quality_score"] = max(0, min(100, score))
        if metrics["quality_score"] < self.min_score:
            return SignalQualityResult(False, metrics["quality_score"], "QUALITY_SCORE_TOO_LOW", f"Quality score {metrics['quality_score']} is below minimum {self.min_score}.", metrics)
        return SignalQualityResult(True, metrics["quality_score"], "QUALITY_PASS", "Signal passed available quality checks.", metrics)

    @staticmethod
    def _age_seconds_with_now(signal: TradeSignal, now_utc: Any) -> Optional[float]:
        try:
            import pandas as pd
            now = pd.Timestamp(now_utc)
            ts = pd.Timestamp(signal.entry_time)
            if now.tzinfo is None:
                now = now.tz_localize("UTC")
            else:
                now = now.tz_convert("UTC")
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            return float((now - ts).total_seconds())
        except Exception:
            return None
