"""Run 1-month backtest for Megalord's latest configuration."""
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


def run_backtest_windows():
    print("Loading all project signals...")
    loader = ProjectStreamLoader()
    all_signals: List[TradeSignal] = loader.load_all_signals()
    print(f"Total signals loaded across history: {len(all_signals)}")

    windows = [
        {"name": "Son 30 Gün (Rolling 1 Ay: 21 Eyl - 21 Eki 2026)", "start": "2026-09-21 00:00:00", "end": "2026-10-21 23:59:59"},
        {"name": "Son Tam Takvim Ayı (Eylül 2026: 01 Eyl - 30 Eyl)", "start": "2026-09-01 00:00:00", "end": "2026-09-30 23:59:59"},
        {"name": "İçinde Bulunduğumuz Ay (Ekim 2026 MTD: 01 Eki - 21 Eki)", "start": "2026-10-01 00:00:00", "end": "2026-10-21 23:59:59"},
    ]

    results = []

    for win in windows:
        dt_start = pd.to_datetime(win["start"], utc=True)
        dt_end = pd.to_datetime(win["end"], utc=True)

        month_signals = [s for s in all_signals if dt_start <= s.entry_time <= dt_end]
        
        sim = MegalordSimulator()
        sim_res = sim.run_orchestration(month_signals)

        executed = sim_res["executed_trades"]
        rejected = sim_res["rejected_trades"]
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

        # By Engine breakdown
        engine_stats = {}
        for t in executed:
            eng = t["engine_name"]
            if eng not in engine_stats:
                engine_stats[eng] = {"trades": 0, "wins": 0, "losses": 0, "net_r": 0.0}
            engine_stats[eng]["trades"] += 1
            engine_stats[eng]["net_r"] += t["effective_r"]
            if t["effective_r"] > 0:
                engine_stats[eng]["wins"] += 1
            elif t["effective_r"] < 0:
                engine_stats[eng]["losses"] += 1

        results.append({
            "window": win["name"],
            "total_signals": len(month_signals),
            "executed_trades": total_exec,
            "rejected_signals": len(rejected),
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate_pct": win_rate,
            "net_r": net_r,
            "expectancy_r": exp_r,
            "profit_factor": pf,
            "max_drawdown_r": max_dd,
            "annualized_sharpe": sim_res.get("annualized_sharpe", 0.0),
            "engine_breakdown": engine_stats,
            "executed_trade_list": [
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
            ],
            "rejected_trade_list": [
                {
                    "time": str(r["time"]),
                    "symbol": r["symbol"],
                    "engine": r["engine"],
                    "direction": r["direction"],
                    "reason": r.get("reason", ""),
                    "action": r.get("action", "")
                }
                for r in rejected
            ]
        })

    out_path = PROJECT_ROOT / "megalord_last_1m_backtest.json"
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nBacktest complete! Results saved to {out_path}\n")

    for r in results:
        print("=" * 100)
        print(f"PENCERE: {r['window']}")
        print(f"Toplam Gelen Sinyal: {r['total_signals']} | Kabul Edilen (İcra): {r['executed_trades']} | Filtrelenen / Red: {r['rejected_signals']}")
        print(f"Kazanma Oranı (WR): %{r['win_rate_pct']} ({r['win_count']} Win / {r['loss_count']} Loss)")
        print(f"Net Kâr: {r['net_r']:+.2f}R | Beklenti (Expectancy): {r['expectancy_r']:+.3f}R / işlem")
        print(f"Profit Factor (PF): {r['profit_factor']} | Max Drawdown: -{r['max_drawdown_r']}R | Sharpe: {r['annualized_sharpe']}")
        print("\nMotor Dağılımı:")
        for eng, stats in r["engine_breakdown"].items():
            wr = round(stats["wins"] / stats["trades"] * 100, 1) if stats["trades"] > 0 else 0
            print(f"  * {eng:<16}: {stats['trades']} işlem | WR %{wr} | Net: {stats['net_r']:+.2f}R")
        print("=" * 100)


if __name__ == "__main__":
    run_backtest_windows()
