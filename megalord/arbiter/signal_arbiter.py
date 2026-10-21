"""Megalord Signal Arbiter with Orthogonal Confluence, Clone Anti-Collision, and Trend vs MR Arbitration."""
from __future__ import annotations

from typing import Dict, List, Any, Optional
from megalord.types import TradeSignal, ArbiterDecision, MathFamily, StrategyRegime


class MegalordSignalArbiter:
    """
    Implements:
      Rule 2: Orthogonal Confluence (1.30x Boost across >=2 distinct Math Families)
              Clone Anti-Collision (Same FAMILY_SMC signals do NOT get boost, capped at 1.0x total risk)
      Rule 3: Trend vs Mean-Reversion Conflict Arbitration:
              - If active HTF Macro/Trend is opposite and candidate is TREND_MOMENTUM -> VETO (HTF_CONFLICT_VETO)
              - If active HTF Macro/Trend is opposite and candidate is approved MEAN_REVERSION -> DAMPEN to 0.50x
      Rule Allocator (Faz 4 Safra Temizliği):
              - Execution Enabled Check (Convexity_v2 is compass-only, Begonya old klon is disabled)
              - Consecutive loss cooldown (>=3 losses in a row on that engine -> 0.50x risk until next win)
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
        self.cooldown_threshold = int(alloc_cfg.get("consecutive_loss_cooldown_threshold", 3))
        self.cooldown_multiplier = float(alloc_cfg.get("consecutive_loss_cooldown_multiplier", 0.50))

        # Engine loss streak tracker: engine_name -> int
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
        macro_htf_bias: Optional[str] = None  # "LONG", "SHORT", "NEUTRAL"
    ) -> ArbiterDecision:
        engine = candidate.engine_name

        # 0. Check Execution Enabled (Faz 4 Pruning)
        if not self.engine_execution_enabled.get(engine, True):
            if engine in self.compass_only_engines:
                return ArbiterDecision(
                    allowed=False,
                    final_risk_mult=0.0,
                    action="COMPASS_ONLY_RECORDED",
                    reason=f"{engine} is calibrated as HTF Compass Only. Execution disabled; signal logged as Trend Beacon."
                )
            return ArbiterDecision(
                allowed=False,
                final_risk_mult=0.0,
                action="ENGINE_DISABLED",
                reason=f"{engine} execution is disabled in Megalord configuration (weight = 0.0)."
            )

        base_w = self.base_weights.get(engine, 1.0)
        if base_w <= 0.0:
            return ArbiterDecision(
                allowed=False,
                final_risk_mult=0.0,
                action="ENGINE_DISABLED",
                reason=f"{engine} has base risk weight {base_w} (disabled)."
            )

        # 1. Check Engine Cooldown
        streak = self.engine_loss_streaks.get(engine, 0)
        cooldown_applied = False
        if streak >= self.cooldown_threshold:
            base_w *= self.cooldown_multiplier
            cooldown_applied = True

        # 2. Check Rule 3: HTF Macro / Trend Conflict
        # Find if there is an active macro trend on this symbol (e.g. from Convexity D1 or macro_htf_bias)
        htf_trend_dir = None
        if macro_htf_bias in ("LONG", "SHORT"):
            htf_trend_dir = macro_htf_bias

        for pos in active_positions:
            if pos.get("symbol") == candidate.symbol and pos.get("math_family") == MathFamily.FAMILY_MACRO_TREND.value:
                htf_trend_dir = pos.get("direction")
                break

        if htf_trend_dir and htf_trend_dir != candidate.direction:
            # Opposite direction to HTF Macro Trend!
            if candidate.strategy_regime == StrategyRegime.TREND_MOMENTUM:
                return ArbiterDecision(
                    allowed=False,
                    final_risk_mult=0.0,
                    action="HTF_CONFLICT_VETO",
                    reason=f"Opposite direction ({candidate.direction}) to active HTF Trend ({htf_trend_dir}) for Trend/Momentum engine."
                )
            elif candidate.strategy_regime == StrategyRegime.MEAN_REVERSION:
                # Approved Mean-Reversion against trend: dampen to 0.50x
                final_mult = base_w * self.mr_counter_trend_mult
                return ArbiterDecision(
                    allowed=True,
                    final_risk_mult=round(final_mult, 4),
                    action="COUNTER_TREND_MR_DAMPED",
                    reason=f"Mean-Reversion trade against HTF Trend ({htf_trend_dir}) permitted with {self.mr_counter_trend_mult:.2f}x damping."
                )

        # 3. Check Rule 2: Confluence & Clone Anti-Collision with existing open/concurrent signals on the same symbol & direction
        same_sym_dir_positions = [
            pos for pos in active_positions
            if pos.get("symbol") == candidate.symbol and pos.get("direction") == candidate.direction
        ]

        if not same_sym_dir_positions:
            # Standalone valid entry
            cd_note = f" (Cooldown active: {streak} consecutive losses)" if cooldown_applied else ""
            return ArbiterDecision(
                allowed=True,
                final_risk_mult=round(base_w, 4),
                action="PROCEED",
                reason=f"Single engine trade approved with base weight {base_w:.2f}{cd_note}."
            )

        # Check families of concurrent positions
        existing_families = set(pos.get("math_family") for pos in same_sym_dir_positions)
        existing_engines = [pos.get("engine_name") for pos in same_sym_dir_positions]

        # Case A: Clone Collision inside same family (e.g. FAMILY_SMC)
        if candidate.math_family.value in existing_families:
            # Sum of risk of this family on this symbol must not exceed clone_cap (1.0x)
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
                    reason=f"Clone family {candidate.math_family.value} already allocated {current_family_risk:.2f}R (cap {self.clone_cap:.2f}R). Blocked to prevent correlation spike.",
                    confluent_engines=existing_engines,
                    confluent_families=list(existing_families)
                )
            capped_weight = min(base_w, remaining_room)
            return ArbiterDecision(
                allowed=True,
                final_risk_mult=round(capped_weight, 4),
                action="CLONE_CAPPED",
                reason=f"Clone collision inside {candidate.math_family.value}. Risk capped at remaining room {capped_weight:.2f}R (total family cap {self.clone_cap:.2f}R).",
                confluent_engines=existing_engines,
                confluent_families=list(existing_families)
            )

        # Case B: Real Orthogonal Confluence across distinct Math Families!
        confluent_families = list(existing_families) + [candidate.math_family.value]
        confluent_engines = existing_engines + [candidate.engine_name]
        boosted_mult = base_w * self.orthogonal_boost

        return ArbiterDecision(
            allowed=True,
            final_risk_mult=round(boosted_mult, 4),
            action="CONFLUENCE_BOOST",
            reason=f"Orthogonal Confluence verified across {len(confluent_families)} distinct math families ({', '.join(confluent_families)}). {self.orthogonal_boost:.2f}x boost applied.",
            confluent_engines=confluent_engines,
            confluent_families=confluent_families
        )
