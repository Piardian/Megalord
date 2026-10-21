"""Run 1-month backtests across 3 distinct months in 2026 and 3 distinct months in 2025."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Any, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from megalord.simulator.master_engine import MegalordSimulator
from megalord.adapters.stream_loader import ProjectStreamLoader
from megalord.types import TradeSignal


def run_monthly_slices():
    print("Loading all project signals (this loads data from all active engines)...")
    loader = ProjectStreamLoader()
    all_signals: List[TradeSignal] = loader.load_all_signals()
    print(f"Total signals loaded across history: {len(all_signals)}")

    # Test months requested:
    # 2026: 3 distinct months -> 2026-01 (Ocak), 2026-05 (Mayıs), 2026-09 (Eylül)
    # 2025: 3 distinct months -> 2025-03 (Mart), 2025-07 (Temmuz), 2025-11 (Kasım)
    month_windows = [
        {"name": "2025-03 (Mart 2025)", "start": "2025-03-01", "end": "2025-03-31 23:59:59"},
        {"name": "2025-07 (Temmuz 2025)", "start": "2025-07-01", "end": "2025-07-31 23:59:59"},
        {"name": "2025-11 (Kasım 2025)", "start": "2025-11-01", "end": "2025-11-30 23:59:59"},
        {"name": "2026-01 (Ocak 2026)", "start": "2026-01-01", "end": "2026-01-31 23:59:59"},
        {"name": "2026-05 (Mayıs 2026)", "start": "2026-05-01", "end": "2026-05-31 23:59:59"},
        {"name": "2026-09 (Eylül 2026)", "start": "2026-09-01", "end": "2026-09-30 23:59:59"},
    ]

    results = []

    for win in month_windows:
        dt_start = pd.to_datetime(win["start"], utc=True)
        dt_end = pd.to_datetime(win["end"], utc=True)

        month_signals = [s for s in all_signals if dt_start <= s.entry_time <= dt_end]
        
        sim = MegalordSimulator()
        sim_res = sim.run_orchestration(month_signals)

        executed = sim_res["executed_trades"]
        r_list = [t["effective_r"] for t in executed]
        wins = [r for r in r_list if r > 0]
        losses = [r for r in r_list if r < 0]

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

        results.append({
            "window": win["name"],
            "total_signals": len(month_signals),
            "executed_trades": total_exec,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate_pct": win_rate,
            "net_r": net_r,
            "expectancy_r": exp_r,
            "profit_factor": pf,
            "max_drawdown_r": max_dd,
            "annualized_sharpe": sim_res["annualized_sharpe"],
            "trades": [
                {
                    "symbol": t["symbol"],
                    "engine": t["engine_name"],
                    "direction": t["direction"],
                    "risk_r": t["risk_r"],
                    "net_r": t["effective_r"],
                    "action": t.get("arbiter_action", "PROCEED"),
                    "time": str(t["entry_time"])
                }
                for t in executed
            ]
        })

    out_path = Path(r"C:\Users\piard\Desktop\MegalordProject\megalord_monthly_backtest_results.json")
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nMonthly backtest complete! Results saved to {out_path}\n")

    # Print summary table
    print("=" * 95)
    print(f"{'AY':<25} | {'İŞLEM':<6} | {'WR %':<7} | {'NET R':<8} | {'ORT R (Exp)':<12} | {'PF':<6} | {'MAX DD':<7}")
    print("-" * 95)
    for r in results:
        print(f"{r['window']:<25} | {r['executed_trades']:<6} | %{r['win_rate_pct']:<5} | {r['net_r']:<+8.2f} | {r['expectancy_r']:<+12.3f} | {r['profit_factor']:<6} | -{r['max_drawdown_r']:<6.2f}")
    print("=" * 95)


if __name__ == "__main__":
    run_monthly_slices()
