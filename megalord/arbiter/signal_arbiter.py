"""Megalord Signal Arbiter with quality gating, confluence, and conflict arbitration."""
from __future__ import annotations

from typing import Dict, List, Any, Optional
from megalord.types import TradeSignal, ArbiterDecision, MathFamily, StrategyRegime
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

        # 3. HTF macro/trend conflict.
        htf_trend_dir = macro_htf_bias if macro_htf_bias in ("LONG", "SHORT") else None
        for pos in active_positions:
            if pos.get("symbol") == candidate.symbol and pos.get("math_family") == MathFamily.FAMILY_MACRO_TREND.value:
                htf_trend_dir = pos.get("direction")
                break

        if htf_trend_dir and htf_trend_dir != candidate.direction:
            if candidate.strategy_regime == StrategyRegime.TREND_MOMENTUM:
                return ArbiterDecision(
                    allowed=False,
                    final_risk_mult=0.0,
                    action="HTF_CONFLICT_VETO",
                    reason=f"Opposite direction ({candidate.direction}) to active HTF Trend ({htf_trend_dir}) for Trend/Momentum engine.",
                )
            if candidate.strategy_regime == StrategyRegime.MEAN_REVERSION:
                final_mult = base_w * self.mr_counter_trend_mult
                return ArbiterDecision(
                    allowed=True,
                    final_risk_mult=round(final_mult, 4),
                    action="COUNTER_TREND_MR_DAMPED",
                    reason=f"Mean-Reversion against HTF Trend ({htf_trend_dir}) permitted with {self.mr_counter_trend_mult:.2f}x damping.",
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
                float(pos.get("risk_mult", pos.get("risk_r", 1.0)))
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
