"""Megalord Risk Firewall enforcing Prop-Firm Worst-Case Daily Budget and Currency Cluster Caps."""
from __future__ import annotations

from typing import Dict, List, Any, Optional
from megalord.types import TradeSignal


class MegalordRiskFirewall:
    """
    Worst-Case Open Risk + Realized PnL Protection Firewall.
    Rule 4 & Faz 3.1:
      Total Daily Risk Exposure = Daily Realized Loss + sum(Open Positions Worst-Case SL Risk)
      If Total Daily Risk Exposure + new_signal_risk > max_daily_loss_pct (2.5%), REJECT.
      Currency / Asset cluster cap: Max 1.5R open risk per currency leg or asset class.
      Anti-Starvation Kuralı (Faz 3.1):
        Convexity_v2 (D1 Trend Runner) pozisyonları günlerce açık kalır. Gün içi (Intraday)
        motorların önünü açmak için: Intraday bir motor sinyal verdiğinde, açık Convexity_v2
        pozisyonlarının döviz kümelenmesi Intraday motorunun 1.5R'lık Intraday bütçesini bloke etmez!
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        firewall_cfg = cfg.get("firewall", {})
        self.max_daily_loss_pct = float(firewall_cfg.get("max_daily_loss_pct", 2.5))
        self.max_worst_case_exposure_pct = float(firewall_cfg.get("max_worst_case_exposure_pct", 2.5))
        self.cluster_caps = firewall_cfg.get("currency_cluster_caps", {
            "USD": 1.5, "JPY": 1.5, "EUR": 1.5, "GBP": 1.5, "XAU": 1.5, "INDEX": 1.5, "CRYPTO": 1.5
        })

    @staticmethod
    def resolve_cluster_legs(symbol: str) -> List[str]:
        s = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
        legs = []
        if "XAU" in s or "GOLD" in s:
            legs.append("XAU")
            legs.append("USD")
        elif "NAS" in s or "NQ" in s or "SP500" in s or "ES" in s or "INDEX" in s:
            legs.append("INDEX")
            legs.append("USD")
        elif any(c in s for c in ("BTC", "ETH", "SOL", "XRP", "LTC", "DOG", "LINK")):
            legs.append("CRYPTO")
            if "EUR" in s:
                legs.append("EUR")
            else:
                legs.append("USD")
        else:
            # FX currencies
            for cur in ("USD", "JPY", "EUR", "GBP", "CHF", "AUD", "CAD", "NZD"):
                if cur in s:
                    legs.append(cur)
        return list(set(legs))

    def evaluate_entry(
        self,
        candidate_signal: TradeSignal,
        allocated_risk_r: float,
        daily_realized_loss_r: float,
        open_positions: List[Dict[str, Any]]
    ) -> tuple[bool, str]:
        """
        Denetim A: Worst-Case Daily Drawdown
        Denetim B: Asset / Currency Cluster Cap (Intraday Anti-Starvation Destekli)
        """
        is_candidate_intraday = (candidate_signal.engine_name != "Convexity_v2")

        # 1. Calculate current open worst-case SL risk (excluding positions locked at BE or in profit)
        current_open_worst_case_r = 0.0
        cluster_exposure: Dict[str, float] = {}

        for pos in open_positions:
            pos_risk = float(pos.get("allocated_risk_r", pos.get("risk_r", 0.50)))
            is_be = bool(
                pos.get("is_breakeven", False)
                or pos.get("be_moved", False)
                or float(pos.get("trailing_level", 0.0) or 0.0) > 0
            )
            pos_is_convexity = (pos.get("engine_name") == "Convexity_v2")

            # Worst-case risk for daily drawdown
            worst_case_pos = 0.0 if is_be else pos_risk
            current_open_worst_case_r += worst_case_pos

            # Faz 3.1: Eğer aday sinyal bir Intraday motoruysa, açık Convexity_v2 pozisyonunun
            # cluster yükü Intraday motorunun döviz tavanını bloke etmez (Starvation Engeli).
            if is_candidate_intraday and pos_is_convexity:
                continue

            # Risk-free positions (Break-Even / Trailing Stop) carry 0.0R downside risk and do not consume cluster cap
            if is_be:
                continue

            sym = pos.get("symbol", "")
            legs = self.resolve_cluster_legs(sym)
            for leg in legs:
                cluster_exposure[leg] = cluster_exposure.get(leg, 0.0) + pos_risk

        # Check total daily risk exposure
        potential_total_loss_r = daily_realized_loss_r + current_open_worst_case_r + allocated_risk_r
        if potential_total_loss_r > self.max_daily_loss_pct:
            return False, (
                f"FIREWALL_DAILY_BUDGET_FULL: Potential daily loss ({potential_total_loss_r:.2f}R) "
                f"exceeds max daily budget ({self.max_daily_loss_pct:.2f}R). "
                f"[Realized: {daily_realized_loss_r:.2f}R, Open SL: {current_open_worst_case_r:.2f}R, New: {allocated_risk_r:.2f}R]"
            )

        # Check cluster caps
        cand_legs = self.resolve_cluster_legs(candidate_signal.symbol)
        for leg in cand_legs:
            curr_leg_exp = cluster_exposure.get(leg, 0.0)
            max_leg = self.cluster_caps.get(leg, 1.5)
            if curr_leg_exp + allocated_risk_r > max_leg:
                return False, (
                    f"FIREWALL_CLUSTER_CAP_EXCEEDED: {leg} cluster exposure ({curr_leg_exp + allocated_risk_r:.2f}R) "
                    f"exceeds limit ({max_leg:.2f}R) [Current: {curr_leg_exp:.2f}R, New: {allocated_risk_r:.2f}R]"
                )

        return True, "FIREWALL_PASS"
