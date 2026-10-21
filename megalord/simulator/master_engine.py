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
        cfg_file = Path(config_path) if config_path else Path(__file__).resolve().parent.parent / "config" / "megalord_config.json"
        self.config = json.loads(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
        self.arbiter = MegalordSignalArbiter(self.config)
        self.firewall = MegalordRiskFirewall(self.config)

    def run_orchestration(self, signals: List[TradeSignal]) -> Dict[str, Any]:
        """
        Simulates master portfolio execution across all signals.
        Tracks active positions, daily realized loss, worst-case open risk, and decisions.
        """
        sorted_sigs = sorted(signals, key=lambda s: s.entry_time)

        executed_trades = []
        rejected_trades = []

        open_positions: List[Dict[str, Any]] = []  # list of currently open position dicts
        current_day = None
        daily_realized_loss_r = 0.0

        for sig in sorted_sigs:
            sig_day = sig.entry_time.date()

            # Close any open positions whose exit_time <= sig.entry_time
            still_open = []
            for pos in open_positions:
                pos_exit = pos.get("exit_time")
                if pos_exit and pos_exit <= sig.entry_time:
                    # Position closed!
                    pnl_r = pos["effective_r"]
                    self.arbiter.record_trade_outcome(pos["engine_name"], pnl_r)
                    if pos_exit.date() == sig_day and pnl_r < 0:
                        daily_realized_loss_r += abs(pnl_r)
                else:
                    still_open.append(pos)
            open_positions = still_open

            # Daily loss reset on new day
            if current_day != sig_day:
                current_day = sig_day
                daily_realized_loss_r = 0.0

            # 1. Arbiter Evaluation (Confluence, Clone Cap, Trend vs MR)
            arb_decision = self.arbiter.evaluate_signal(sig, open_positions, allow_historical=True)
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
        r_list = [t["effective_r"] for t in executed_trades]
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

        # Daily Sharpe (assuming 252 trading days)
        if executed_trades:
            df_trades = pd.DataFrame(executed_trades)
            df_trades["date"] = pd.to_datetime(df_trades["entry_time"]).dt.date
            daily_r = df_trades.groupby("date")["effective_r"].sum()
            std = daily_r.std()
            sharpe = (daily_r.mean() / std * (252 ** 0.5)) if std > 0 else 0.0
        else:
            sharpe = 0.0

        return {
            "total_signals_received": len(signals),
            "executed_trades_count": n,
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
