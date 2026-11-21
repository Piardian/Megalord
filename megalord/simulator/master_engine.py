"""Megalord Master Orchestrator and Simulation Engine.
Coordinates:
- ProjectStreamLoader (7 Engines)
- MegalordSignalArbiter (Rule 2 Orthogonal Confluence + Clone Cap + Rule 3 Trend vs MR)
- MegalordRiskFirewall (Rule 4 Worst-Case Daily Budget + Cluster Caps)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
from megalord.types import TradeSignal, ArbiterDecision
from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.firewall.risk_firewall import MegalordRiskFirewall


class MegalordSimulator:
    def __init__(self, config_path: Optional[str] = None):
        cfg_file = Path(config_path) if config_path else Path(__file__).resolve().parents[2] / "config" / "megalord_config.json"
        # Missing configuration must not silently replace actual risk weights
        # and execution toggles with a different strategy's defaults.
        self.config = json.loads(cfg_file.read_text(encoding="utf-8"))
        self.config_path = cfg_file.resolve()
        self.arbiter = MegalordSignalArbiter(self.config)
        self.firewall = MegalordRiskFirewall(self.config)

    def run_orchestration(self, signals: List[TradeSignal], macro_gate_provider: Optional[Any] = None) -> Dict[str, Any]:
        """
        Simulates master portfolio execution across all signals.
        Tracks active positions, daily realized loss, worst-case open risk, and decisions.
        """
        # Reusing a simulator for another date window must not carry the last
        # window's (potentially future) loss streak into its initial state.
        self.arbiter.engine_loss_streaks.clear()
        sorted_sigs = sorted(signals, key=lambda s: s.entry_time)

        executed_trades = []
        rejected_trades = []

        open_positions: List[Dict[str, Any]] = []  # list of currently open position dicts
        current_day = None
        daily_realized_loss_r = 0.0

        for sig in sorted_sigs:
            sig_day = sig.entry_time.date()

            # Reset BEFORE applying overnight positions closed today. Doing
            # this afterwards erased their losses at the first entry of a day.
            if current_day != sig_day:
                current_day = sig_day
                daily_realized_loss_r = 0.0

            # Close any open positions whose exit_time <= sig.entry_time
            due = sorted(
                [p for p in open_positions if p.get("exit_time") is not None and p["exit_time"] <= sig.entry_time],
                key=lambda p: (p["exit_time"], p["signal_id"]),
            )
            for pos in due:
                pnl_r = pos["effective_r"]
                self.arbiter.record_trade_outcome(pos["engine_name"], pnl_r)
                if pos["exit_time"].date() == sig_day and pnl_r < 0:
                    daily_realized_loss_r += abs(pnl_r)
            open_positions = [p for p in open_positions if p.get("exit_time") is None or p["exit_time"] > sig.entry_time]

            # Determine Macro Gate Bias for candidate
            macro_bias = None
            if macro_gate_provider is not None:
                try:
                    macro_bias = macro_gate_provider(sig.entry_time, sig.symbol)
                except Exception:
                    macro_bias = None
            if not macro_bias and sig.metadata and "macro_gate" in sig.metadata:
                macro_bias = sig.metadata["macro_gate"]

            # 1. Arbiter Evaluation (Confluence, Clone Cap, Trend vs MR, Natural Band)
            arb_decision = self.arbiter.evaluate_signal(
                candidate=sig,
                active_positions=open_positions,
                macro_htf_bias=macro_bias,
                allow_historical=True,
            )
            if not arb_decision.allowed:
                rejected_trades.append({
                    "signal_id": sig.signal_id,
                    "engine": sig.engine_name,
                    "symbol": sig.symbol,
                    "direction": sig.direction,
                    "time": sig.entry_time,
                    "reason": arb_decision.reason,
                    "action": arb_decision.action
                })
                continue

            allocated_risk_r = arb_decision.final_risk_mult

            # 2. Firewall Evaluation (Worst-Case Daily Budget & Cluster Caps)
            fw_ok, fw_reason = self.firewall.evaluate_entry(
                candidate_signal=sig,
                allocated_risk_r=allocated_risk_r,
                daily_realized_loss_r=daily_realized_loss_r,
                open_positions=open_positions
            )

            if not fw_ok:
                rejected_trades.append({
                    "signal_id": sig.signal_id,
                    "engine": sig.engine_name,
                    "symbol": sig.symbol,
                    "direction": sig.direction,
                    "time": sig.entry_time,
                    "reason": fw_reason,
                    "action": "FIREWALL_BLOCKED"
                })
                continue

            # 3. Trade Executed!
            effective_r = sig.raw_r_result * allocated_risk_r
            trade_record = {
                "signal_id": sig.signal_id,
                "engine_name": sig.engine_name,
                "math_family": sig.math_family.value,
                "strategy_regime": sig.strategy_regime.value,
                "symbol": sig.symbol,
                "direction": sig.direction,
                "entry_time": sig.entry_time,
                "exit_time": sig.exit_time,
                "risk_r": allocated_risk_r,
                "raw_r": sig.raw_r_result,
                "effective_r": effective_r,
                "arbiter_action": arb_decision.action,
                "arbiter_reason": arb_decision.reason,
                "is_breakeven": False
            }
            executed_trades.append(trade_record)
            open_positions.append(trade_record)

        # Performance Metrics Calculation
        realized_trades = sorted(
            [t for t in executed_trades if t["exit_time"] is not None],
            key=lambda t: (t["exit_time"], t["signal_id"]),
        )
        # Equity is realized in exit order, not the order positions were opened.
        # Still-open records cannot contribute their supplied future outcome.
        r_list = [t["effective_r"] for t in realized_trades]
        n = len(r_list)
        wins = [r for r in r_list if r > 0]
        losses = [r for r in r_list if r < 0]
        wr = (len(wins) / n * 100.0) if n > 0 else 0.0
        net_r = sum(r_list)
        exp_r = net_r / n if n > 0 else 0.0
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        pf = (gross_win / gross_loss) if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)

        # Drawdown calculation
        eq = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in r_list:
            eq += r
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd

        ret_dd = (net_r / max_dd) if max_dd > 0 else 0.0

        # Calendar-day closed-R proxy. Include inactive days and use actual
        # close times; this is explicitly not an account-return Sharpe ratio.
        if realized_trades:
            df_trades = pd.DataFrame(realized_trades)
            df_trades["date"] = pd.to_datetime(df_trades["exit_time"], utc=True).dt.floor("D")
            start = min(pd.Timestamp(t["entry_time"]).tz_convert("UTC") for t in executed_trades).floor("D")
            end = max(
                max(pd.Timestamp(t["exit_time"]).tz_convert("UTC") for t in realized_trades),
                max(pd.Timestamp(t["entry_time"]).tz_convert("UTC") for t in executed_trades),
            ).floor("D")
            daily_r = df_trades.groupby("date")["effective_r"].sum().reindex(pd.date_range(start, end, freq="D"), fill_value=0.0)
            std = daily_r.std()
            sharpe = (daily_r.mean() / std * (365.25 ** 0.5)) if std > 0 else 0.0
        else:
            sharpe = 0.0

        return {
            "simulation_scope": "historical_outcome_allocation_replay",
            "live_readiness_proven": False,
            "config_path": str(self.config_path),
            "r_statistics_basis": "closed native outcomes in exit-time order; open positions excluded",
            "sharpe_basis": "calendar-day closed-R proxy including zero days; not account-return Sharpe",
            "limitations": [
                "Native outcomes are supplied by adapters, not reconstructed from broker fills.",
                "Historical quality bypass is enabled; this is not test/live entry or exit parity.",
            ],
            "total_signals_received": len(signals),
            "executed_trades_count": len(executed_trades),
            "realized_trades_count": n,
            "open_trades_count": len(executed_trades) - n,
            "realized_signal_ids": [t["signal_id"] for t in realized_trades],
            "rejected_trades_count": len(rejected_trades),
            "win_rate_pct": round(wr, 2),
            "profit_factor": round(pf, 2),
            "net_r": round(net_r, 2),
            "expectancy_r": round(exp_r, 3),
            "max_drawdown_r": round(max_dd, 2),
            "return_to_dd": round(ret_dd, 2),
            "annualized_sharpe": round(sharpe, 2),
            "executed_trades": executed_trades,
            "rejected_trades": rejected_trades
        }
