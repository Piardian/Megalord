"""Megalord Signal Arbiter with quality gating, confluence, and conflict arbitration."""
from __future__ import annotations

import math
from typing import Dict, List, Any, Optional
from megalord.types import TradeSignal, ArbiterDecision, MathFamily, StrategyRegime, GLOBAL_EXECUTION_WHITELIST
from megalord.filters.signal_quality_filter import SignalQualityFilter


class MegalordSignalArbiter:
    """
    Central arbitration:
    - Signal quality validation before allocation.
    - Orthogonal confluence across distinct math families.
    - Clone-family exposure caps.
    - Trend vs mean-reversion conflict arbitration.
    - Engine execution toggles and consecutive-loss cooldown.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        alloc_cfg = cfg.get("allocator", {})
        self.base_weights = alloc_cfg.get("base_engine_weights", {
            "Begonya00": 1.0,
            "Begonya": 0.0,
            "ChecklistTrigger": 1.0,
            "Convexity_v2": 0.0,
            "Pragma": 0.90,
            "Trawma": 0.85,
            "Stigma": 0.85,
        })
        self.engine_execution_enabled = alloc_cfg.get("engine_execution_enabled", {
            "Begonya00": True,
            "Begonya": False,
            "ChecklistTrigger": True,
            "Convexity_v2": False,
            "Pragma": True,
            "Trawma": True,
            "Stigma": True,
        })
        self.compass_only_engines = set(alloc_cfg.get("compass_only_engines", ["Convexity_v2"]))
        self.orthogonal_boost = float(alloc_cfg.get("orthogonal_confluence_multiplier", 1.30))
        self.clone_cap = float(alloc_cfg.get("clone_confluence_cap", 1.0))
        self.mr_counter_trend_mult = float(alloc_cfg.get("counter_trend_mean_reversion_multiplier", 0.50))
        self.begonya00_aplus_mult = float(alloc_cfg.get("begonya00_aplus_multiplier", 0.80))
        self.cooldown_threshold = int(alloc_cfg.get("consecutive_loss_cooldown_threshold", 3))
        self.cooldown_multiplier = float(alloc_cfg.get("consecutive_loss_cooldown_multiplier", 0.50))
        
        q_cfg = dict(cfg.get("signal_quality_filter", {}))
        if "weekend_trading" in cfg:
            q_cfg["weekend_trading"] = cfg["weekend_trading"]
        self.quality_filter = SignalQualityFilter(q_cfg)
        self.engine_loss_streaks: Dict[str, int] = {}

    def record_trade_outcome(self, engine_name: str, pnl_r: float):
        if pnl_r < 0:
            self.engine_loss_streaks[engine_name] = self.engine_loss_streaks.get(engine_name, 0) + 1
        else:
            self.engine_loss_streaks[engine_name] = 0

    def evaluate_signal(
        self,
        candidate: TradeSignal,
        active_positions: List[Dict[str, Any]],
        macro_htf_bias: Optional[str] = None,
        now_utc: Any = None,
        allow_historical: bool = False,
    ) -> ArbiterDecision:
        engine = candidate.engine_name

        # Historical closed records must never enter the live execution path.
        # The simulator opts in explicitly to preserve legacy outcome replay.
        if candidate.exit_time is not None and not allow_historical:
            return ArbiterDecision(
                allowed=False,
                final_risk_mult=0.0,
                action="QUALITY_NOT_AN_ENTRY",
                reason="Completed historical trade record cannot be executed as a new entry.",
            )

        # 0. Institutional Whitelist Gate (Fail-closed if set)
        norm_sym = str(candidate.symbol).upper().replace("/", "").replace("-", "").replace("_", "").strip()
        if GLOBAL_EXECUTION_WHITELIST is not None and norm_sym not in GLOBAL_EXECUTION_WHITELIST:
            return ArbiterDecision(
                allowed=False,
                final_risk_mult=0.0,
                action="QUALITY_UNWHITELISTED_SYMBOL",
                reason=f"Symbol {candidate.symbol} ({norm_sym}) is not on the institutional approved execution whitelist.",
            )

        # 0. Execution eligibility before running signal-quality checks.
        if not self.engine_execution_enabled.get(engine, True):
            if engine in self.compass_only_engines:
                return ArbiterDecision(
                    allowed=False,
                    final_risk_mult=0.0,
                    action="COMPASS_ONLY_RECORDED",
                    reason=f"{engine} is HTF Compass Only; execution is disabled.",
                )
            return ArbiterDecision(
                allowed=False,
                final_risk_mult=0.0,
                action="ENGINE_DISABLED",
                reason=f"{engine} execution is disabled in Megalord configuration.",
            )

        base_w = float(self.base_weights.get(engine, 1.0))
        if base_w <= 0.0:
            return ArbiterDecision(
                allowed=False,
                final_risk_mult=0.0,
                action="ENGINE_DISABLED",
                reason=f"{engine} has base risk weight {base_w} (disabled).",
            )

        # 1. Quality filter is a veto layer; it does not modify engine entry logic.
        quality = self.quality_filter.evaluate(candidate, now_utc=now_utc, allow_historical=allow_historical)
        if not quality.allowed:
            return ArbiterDecision(
                allowed=False,
                final_risk_mult=0.0,
                action=quality.action,
                reason=f"{quality.reason} Metrics={quality.metrics}",
            )

        # 2. Grade-aware risk scaling & Engine loss-streak cooldown.
        meta = candidate.metadata or {}
        grade_str = str(meta.get("grade", "")).upper()
        if engine == "Begonya00" and "A+" in grade_str and "AAA" not in grade_str:
            base_w *= self.begonya00_aplus_mult

        streak = self.engine_loss_streaks.get(engine, 0)
        cooldown_applied = streak >= self.cooldown_threshold
        if cooldown_applied:
            base_w *= self.cooldown_multiplier

        # 3. Macro Trend & Natural Band Regime Arbitration
        # Macro gates: LONG_ONLY (LONG), SHORT_ONLY (SHORT), or NATURAL_BAND / NEUTRAL_RANGE.
        # User Institutional Rule:
        # - Directional Macro Trend (LONG_ONLY / SHORT_ONLY): Mean-reversion engines must STAY AWAY (VETO).
        #   Trend/Momentum engines must align strictly with the macro trend direction.
        # - NATURAL_BAND / NEUTRAL_RANGE: Ideal habitat for Mean-Reversion engines (Fully APPROVED).
        macro_gate_str = str(macro_htf_bias or "").upper().strip()
        htf_trend_dir = None
        if macro_gate_str in ("LONG_ONLY", "LONG", "+1", "BULLISH"):
            htf_trend_dir = "LONG"
        elif macro_gate_str in ("SHORT_ONLY", "SHORT", "-1", "BEARISH"):
            htf_trend_dir = "SHORT"

        # Also check if any open position in MACRO_TREND family exists
        if not htf_trend_dir:
            for pos in active_positions:
                if pos.get("symbol") == candidate.symbol and pos.get("math_family") == MathFamily.FAMILY_MACRO_TREND.value:
                    htf_trend_dir = pos.get("direction")
                    break

        # Empirical Quantitative Data Verification for Natural Band / Mean Reversion:
        # Not just string checking: verify quantitative metrics (Hurst exponent & empirical trend slope)
        if candidate.strategy_regime == StrategyRegime.MEAN_REVERSION:
            is_confirmed_natural_band = macro_gate_str in ("NATURAL_BAND", "NEUTRAL_RANGE", "NEUTRAL", "RANGE", "0")
            if not is_confirmed_natural_band:
                trend_ctx = f"directional trend ({htf_trend_dir}_ONLY)" if htf_trend_dir else f"unverified macro regime ({macro_gate_str or 'MISSING'})"
                return ArbiterDecision(
                    allowed=False,
                    final_risk_mult=0.0,
                    action="NATURAL_BAND_REQUIRED",
                    reason=f"Mean-Reversion strictly blocked under {trend_ctx}. Permitted ONLY when NATURAL_BAND / NEUTRAL_RANGE is actively confirmed.",
                )

            # Empirical Data Check: Hurst exponent verification
            # In financial physics, H < 0.5 indicates mean-reverting (anti-persistent) regime.
            # If H >= 0.50 (random walk or trending persistence), reject mean-reversion.
            h_val = float((candidate.metadata or {}).get("hurst", float("nan")))
            if math.isfinite(h_val) and h_val >= 0.50:
                return ArbiterDecision(
                    allowed=False,
                    final_risk_mult=0.0,
                    action="EMPIRICAL_TREND_DETECTED",
                    reason=f"Mean-Reversion rejected: Empirical market data shows trending persistence (Hurst H={h_val:.3f} >= 0.50). Natural Band mathematically invalid.",
                )

        if htf_trend_dir:
            # Active Directional Macro Trend: Trend/Momentum engines must strictly align
            if candidate.strategy_regime == StrategyRegime.TREND_MOMENTUM and candidate.direction != htf_trend_dir:
                return ArbiterDecision(
                    allowed=False,
                    final_risk_mult=0.0,
                    action="HTF_CONFLICT_VETO",
                    reason=f"Opposite direction ({candidate.direction}) to active HTF Trend ({htf_trend_dir}) for Trend/Momentum engine.",
                )

        # 4. Confluence and clone anti-collision among positions on same symbol/direction.
        same_sym_dir_positions = [
            pos for pos in active_positions
            if pos.get("symbol") == candidate.symbol and pos.get("direction") == candidate.direction
        ]

        if not same_sym_dir_positions:
            cd_note = f" (Cooldown active: {streak} consecutive losses)" if cooldown_applied else ""
            return ArbiterDecision(
                allowed=True,
                final_risk_mult=round(base_w, 4),
                action="PROCEED",
                reason=f"Single engine trade approved with base weight {base_w:.2f}{cd_note}. Quality score={quality.score}.",
            )

        existing_families = set(pos.get("math_family") for pos in same_sym_dir_positions)
        existing_engines = [pos.get("engine_name") for pos in same_sym_dir_positions]

        if candidate.math_family.value in existing_families:
            current_family_risk = sum(
                # The daemon stores the actual allocation under this key;
                # older simulator records use risk_mult or risk_r instead.
                float(pos.get("allocated_risk_r", pos.get("risk_mult", pos.get("risk_r", 1.0))))
                for pos in same_sym_dir_positions
                if pos.get("math_family") == candidate.math_family.value
            )
            remaining_room = max(0.0, self.clone_cap - current_family_risk)
            if remaining_room <= 0.05:
                return ArbiterDecision(
                    allowed=False,
                    final_risk_mult=0.0,
                    action="CLONE_CAPPED",
                    reason=f"Clone family {candidate.math_family.value} already allocated {current_family_risk:.2f}R (cap {self.clone_cap:.2f}R).",
                    confluent_engines=existing_engines,
                    confluent_families=list(existing_families),
                )
            capped_weight = min(base_w, remaining_room)
            return ArbiterDecision(
                allowed=True,
                final_risk_mult=round(capped_weight, 4),
                action="CLONE_CAPPED",
                reason=f"Clone collision inside {candidate.math_family.value}; allocated remaining room {capped_weight:.2f}R.",
                confluent_engines=existing_engines,
                confluent_families=list(existing_families),
            )

        confluent_families = list(existing_families) + [candidate.math_family.value]
        confluent_engines = existing_engines + [candidate.engine_name]
        boosted_mult = base_w * self.orthogonal_boost
        return ArbiterDecision(
            allowed=True,
            final_risk_mult=round(boosted_mult, 4),
            action="CONFLUENCE_BOOST",
            reason=f"Orthogonal Confluence across {len(confluent_families)} math families. {self.orthogonal_boost:.2f}x boost applied. Quality score={quality.score}.",
            confluent_engines=confluent_engines,
            confluent_families=confluent_families,
        )
