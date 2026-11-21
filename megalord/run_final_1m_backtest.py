"""Megalord Final 1-Month Unified Backtest Runner."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, Any, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BEGONYA_ROOT = Path(r"C:\Users\piard\Desktop\Begonya00")
if str(BEGONYA_ROOT) not in sys.path:
    sys.path.insert(0, str(BEGONYA_ROOT))

import numpy as np
import pandas as pd

from megalord.simulator.master_engine import MegalordSimulator
from megalord.adapters.stream_loader import ProjectStreamLoader
from megalord.types import TradeSignal
from sfp_orb_engine.macro_gated_3m_backtest import DailyMacroGateTable


def run_final_1m_backtest():
    print("Loading DailyMacroGateTable...")
    macro_table = DailyMacroGateTable(date(2026, 9, 1), date(2026, 10, 22))

    def macro_gate_provider(ts: Any, symbol: str) -> str:
        s = str(symbol).upper().replace("/", "").replace("-", "").strip()
        if "BTC" in s:
            s = "BTCUSD"
        elif "ETH" in s:
            s = "ETHUSD"
        elif "SOL" in s:
            s = "SOLUSD"
        elif s in ("NAS100", "QQQ", "US100"):
            s = "NAS100"
        elif s in ("SPX", "SP500", "US500", "SPY"):
            s = "SP500"
        return macro_table.get_gate(ts, s)

    print("Loading all project signals from active streams...")
    loader = ProjectStreamLoader()
    all_signals: List[TradeSignal] = loader.load_all_signals()

    # Exact 1-month window: 2026-09-21 to 2026-10-21
    dt_start = pd.to_datetime("2026-09-21 00:00:00", utc=True)
    dt_end = pd.to_datetime("2026-10-21 23:59:59", utc=True)

    month_signals = [s for s in all_signals if dt_start <= s.entry_time <= dt_end]
    print(f"Total signals received in 1-month window (21 Sep - 21 Oct 2026): {len(month_signals)}")

    sim = MegalordSimulator()
    sim_res = sim.run_orchestration(month_signals, macro_gate_provider=macro_gate_provider)

    executed = sim_res["executed_trades"]
    rejected = sim_res["rejected_trades"]

    r_list = [t["effective_r"] for t in executed]
    wins = [r for r in r_list if r > 0]
    losses = [r for r in r_list if r < 0]
    be_count = sum(1 for r in r_list if abs(r) < 0.01)

    total_exec = len(executed)
    win_count = len(wins)
    loss_count = len(losses)
    net_r = round(float(sum(r_list)), 2)
    win_rate = round((win_count / total_exec * 100), 1) if total_exec > 0 else 0.0
    exp_r = round(float(np.mean(r_list)), 3) if total_exec > 0 else 0.0

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(float(gross_win / gross_loss), 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

    # Max Drawdown
    cum = np.cumsum(r_list)
    peak = np.maximum.accumulate(cum) if len(cum) > 0 else np.array([0])
    dd = peak - cum if len(cum) > 0 else np.array([0])
    max_dd = round(float(np.max(dd)), 2) if len(dd) > 0 else 0.0

    # Engine breakdown
    engine_stats = {}
    for t in executed:
        eng = t["engine_name"]
        if eng not in engine_stats:
            engine_stats[eng] = {"trades": 0, "wins": 0, "losses": 0, "be": 0, "net_r": 0.0}
        engine_stats[eng]["trades"] += 1
        engine_stats[eng]["net_r"] += t["effective_r"]
        if t["effective_r"] > 0.01:
            engine_stats[eng]["wins"] += 1
        elif t["effective_r"] < -0.01:
            engine_stats[eng]["losses"] += 1
        else:
            engine_stats[eng]["be"] += 1

    # Rejection breakdown
    rej_reasons = {}
    for r in rejected:
        act = r.get("action", "UNKNOWN")
        rej_reasons[act] = rej_reasons.get(act, 0) + 1

    summary = {
        "period": "1 Aylık (21 Eylül 2026 - 21 Ekim 2026)",
        "total_signals_received": len(month_signals),
        "executed_trades_count": total_exec,
        "rejected_signals_count": len(rejected),
        "win_count": win_count,
        "loss_count": loss_count,
        "break_even_count": be_count,
        "win_rate_pct": win_rate,
        "net_r": net_r,
        "expectancy_r": exp_r,
        "profit_factor": pf,
        "max_drawdown_r": max_dd,
        "annualized_sharpe": sim_res.get("annualized_sharpe", 0.0),
        "engine_breakdown": engine_stats,
        "rejection_breakdown": rej_reasons,
        "trades": [
            {
                "time": str(t["entry_time"]),
                "symbol": t["symbol"],
                "engine": t["engine_name"],
                "direction": t["direction"],
                "risk_r": t["risk_r"],
                "net_r": round(float(t["effective_r"]), 2),
                "action": t.get("arbiter_action", "PROCEED")
            }
            for t in executed
        ]
    }

    out_file = PROJECT_ROOT / "megalord_1month_final_report.json"
    out_file.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nFinal 1-Month Backtest Report saved to: {out_file}\n")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    run_final_1m_backtest()
