"""Comprehensive Unit Tests for Megalord Architecture."""
from __future__ import annotations

import unittest
import pandas as pd
from megalord.types import TradeSignal, MathFamily, StrategyRegime
from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.firewall.risk_firewall import MegalordRiskFirewall


class TestMegalordArchitecture(unittest.TestCase):
    def setUp(self):
        self.arbiter = MegalordSignalArbiter()
        self.firewall = MegalordRiskFirewall()
        self.now = pd.Timestamp.now(tz="UTC")

    # =========================================================================
    # Test 1: Clone Anti-Collision (Rule 2)
    # =========================================================================
    def test_clone_collision_capped_inside_same_family(self):
        """Begonya00 and ChecklistTrigger (both FAMILY_SMC) must NOT receive confluence boost and must be capped at 1.0R total."""
        # 1st signal: Begonya00 enters EURUSD LONG (risk 1.0R)
        pos_b00 = {
            "symbol": "EURUSD", "direction": "LONG",
            "math_family": MathFamily.FAMILY_SMC.value,
            "engine_name": "Begonya00", "risk_mult": 1.0
        }

        # 2nd signal: ChecklistTrigger tries to enter EURUSD LONG concurrently
        sig_chk = TradeSignal(
            signal_id="S2", engine_name="ChecklistTrigger",
            math_family=MathFamily.FAMILY_SMC,
            strategy_regime=StrategyRegime.TREND_MOMENTUM,
            symbol="EURUSD", direction="LONG",
            entry_time=self.now, exit_time=None,
            entry_price=1.1000, sl_price=1.0950, tp2_price=1.1100,
        )

        decision = self.arbiter.evaluate_signal(sig_chk, [pos_b00])
        # Total family risk is already 1.0R (cap 1.0R), so second SMC clone should be rejected or capped to 0
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "CLONE_CAPPED")

    # =========================================================================
    # Test 2: Real Orthogonal Confluence (Rule 2)
    # =========================================================================
    def test_orthogonal_confluence_boost_distinct_families(self):
        """Pragma (FAMILY_AMT_VWAP) and Trawma (FAMILY_ORDERFLOW) on BTCUSD LONG must receive 1.30x boost."""
        # Active position: Pragma (FAMILY_AMT_VWAP) LONG BTCUSD
        pos_pragma = {
            "symbol": "BTCUSD", "direction": "LONG",
            "math_family": MathFamily.FAMILY_AMT_VWAP.value,
            "engine_name": "Pragma", "risk_mult": 0.7
        }

        # Candidate: Trawma (FAMILY_ORDERFLOW) LONG BTCUSD
        sig_trawma = TradeSignal(
            signal_id="S_TRW", engine_name="Trawma",
            math_family=MathFamily.FAMILY_ORDERFLOW,
            strategy_regime=StrategyRegime.TREND_MOMENTUM,
            symbol="BTCUSD", direction="LONG",
            entry_time=self.now, exit_time=None,
            entry_price=100000.0, sl_price=99000.0, tp2_price=102000.0,
        )

        decision = self.arbiter.evaluate_signal(sig_trawma, [pos_pragma])
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.action, "CONFLUENCE_BOOST")
        expected_boosted_risk = 0.85 * 1.30  # base weight for Trawma is 0.85 (Phase 4 calibrated)
        self.assertAlmostEqual(decision.final_risk_mult, expected_boosted_risk, places=2)

    # =========================================================================
    # Test 3: Trend vs. Mean-Reversion Conflict (Rule 3)
    # =========================================================================
    def test_htF_trend_conflict_vetoes_momentum_engine(self):
        """If active HTF Trend is LONG, an opposing SHORT from a Momentum engine must be VETOED."""
        pos_convexity = {
            "symbol": "XAUUSD", "direction": "LONG",
            "math_family": MathFamily.FAMILY_MACRO_TREND.value,
            "engine_name": "Convexity_v2", "risk_mult": 0.7
        }

        sig_mom_short = TradeSignal(
            signal_id="S_MOM", engine_name="Begonya00",
            math_family=MathFamily.FAMILY_SMC,
            strategy_regime=StrategyRegime.TREND_MOMENTUM,
            symbol="XAUUSD", direction="SHORT",
            entry_time=self.now, exit_time=None,
            entry_price=2000.0, sl_price=2010.0, tp2_price=1980.0,
        )

        decision = self.arbiter.evaluate_signal(sig_mom_short, [pos_convexity])
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "HTF_CONFLICT_VETO")

    def test_htf_trend_conflict_dampens_mean_reversion_engine(self):
        """If active HTF Trend is LONG, an opposing SHORT from approved Mean-Reversion is permitted at 0.50x risk."""
        pos_convexity = {
            "symbol": "NAS100", "direction": "LONG",
            "math_family": MathFamily.FAMILY_MACRO_TREND.value,
            "engine_name": "Convexity_v2", "risk_mult": 0.7
        }

        sig_mr_short = TradeSignal(
            signal_id="S_MR", engine_name="Stigma",
            math_family=MathFamily.FAMILY_STAT_QUANT,
            strategy_regime=StrategyRegime.MEAN_REVERSION,
            symbol="NAS100", direction="SHORT",
            entry_time=self.now, exit_time=None,
            entry_price=20000.0, sl_price=20100.0, tp2_price=19800.0,
        )

        decision = self.arbiter.evaluate_signal(sig_mr_short, [pos_convexity])
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.action, "COUNTER_TREND_MR_DAMPED")
        expected_damped = 0.85 * 0.50  # Stigma base 0.85 * 0.50 = 0.425
        self.assertAlmostEqual(decision.final_risk_mult, expected_damped, places=2)

    # =========================================================================
    # Test 4: Risk Firewall Worst-Case Daily Drawdown (Rule 4)
    # =========================================================================
    def test_firewall_blocks_when_worst_case_exposure_exceeds_budget(self):
        """Worst-case daily loss (Realized Loss + Open SL + New Risk) > 2.5R must be blocked."""
        sig = TradeSignal(
            signal_id="S_NEW", engine_name="ChecklistTrigger",
            math_family=MathFamily.FAMILY_SMC,
            strategy_regime=StrategyRegime.TREND_MOMENTUM,
            symbol="EURUSD", direction="LONG",
            entry_time=self.now, exit_time=self.now + pd.Timedelta(hours=2)
        )

        # Realized loss 1.2R, Open positions risk 1.0R -> Current exposure = 2.2R
        open_pos = [{"symbol": "GBPUSD", "risk_r": 1.0, "is_breakeven": False}]
        # New signal wants 0.5R -> 2.2 + 0.5 = 2.7R > 2.5R limit!
        allowed, reason = self.firewall.evaluate_entry(
            candidate_signal=sig,
            allocated_risk_r=0.5,
            daily_realized_loss_r=1.2,
            open_positions=open_pos
        )
        self.assertFalse(allowed)
        self.assertIn("FIREWALL_DAILY_BUDGET_FULL", reason)

    # =========================================================================
    # Test 5: Risk Firewall Currency Cluster Cap (Rule 4)
    # =========================================================================
    def test_firewall_currency_cluster_cap(self):
        """Total risk across same currency leg (e.g. USD) cannot exceed 1.5R."""
        sig = TradeSignal(
            signal_id="S_USD3", engine_name="Convexity_v2",
            math_family=MathFamily.FAMILY_MACRO_TREND,
            strategy_regime=StrategyRegime.TREND_MOMENTUM,
            symbol="USDJPY", direction="LONG",
            entry_time=self.now, exit_time=self.now + pd.Timedelta(days=5)
        )

        # Already open USD positions totaling 1.2R
        open_pos = [
            {"symbol": "EURUSD", "risk_r": 0.7, "is_breakeven": False},
            {"symbol": "GBPUSD", "risk_r": 0.5, "is_breakeven": False}
        ]
        # New trade wants 0.7R -> 1.2 + 0.7 = 1.9R > 1.5R cap!
        allowed, reason = self.firewall.evaluate_entry(
            candidate_signal=sig,
            allocated_risk_r=0.7,
            daily_realized_loss_r=0.0,
            open_positions=open_pos
        )
        self.assertFalse(allowed)
        self.assertIn("FIREWALL_CLUSTER_CAP_EXCEEDED", reason)


if __name__ == "__main__":
    unittest.main()
