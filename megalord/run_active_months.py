"""Execute backtest for active calendar months across 2026 and 2025."""
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


def run_active_monthly_slices():
    print("Megalord sinyal havuzu yükleniyor...")
    loader = ProjectStreamLoader()
    all_signals: List[TradeSignal] = loader.load_all_signals()
    print(f"Toplam sinyal: {len(all_signals)}")

    # 2026 ve 2025 aktif ayları:
    target_months = [
        {"name": "2026-10 (Ekim 2026)", "key": "2026-10"},
        {"name": "2026-09 (Eylül 2026)", "key": "2026-09"},
        {"name": "2026-08 (Ağustos 2026)", "key": "2026-08"},
        {"name": "2026-06 (Haziran 2026)", "key": "2026-06"},
        {"name": "2026-04 (Nisan 2026)", "key": "2026-04"},
        {"name": "2025-12 (Aralık 2025)", "key": "2025-12"},
    ]

    results = []
    sim = MegalordSimulator()

    for item in target_months:
        k = item["key"]
        month_sigs = [s for s in all_signals if str(s.entry_time)[:7] == k]
        sim_res = sim.run_orchestration(month_sigs)

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

        # Drawdown
        cum = np.cumsum(r_list)
        peak = np.maximum.accumulate(cum) if len(cum) > 0 else np.array([0])
        dd = peak - cum if len(cum) > 0 else np.array([0])
        max_dd = round(float(np.max(dd)), 2) if len(dd) > 0 else 0.0

        results.append({
            "name": item["name"],
            "key": k,
            "signals": len(month_sigs),
            "executed": total_exec,
            "wins": win_count,
            "losses": loss_count,
            "win_rate": win_rate,
            "net_r": net_r,
            "exp_r": exp_r,
            "pf": pf,
            "max_dd": max_dd,
            "engine_breakdown": pd.DataFrame(executed)["engine_name"].value_counts().to_dict() if executed else {}
        })

    out_file = Path(r"C:\Users\piard\Desktop\MegalordProject\megalord_active_months_results.json")
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 105)
    print(f"{'AY PERİYODU':<26} | {'İŞLEM':<6} | {'WR %':<7} | {'NET R':<9} | {'ORT R (Exp)':<12} | {'PF':<6} | {'MAX DD':<7} | {'MOTOR KIRILIMI'}")
    print("-" * 105)
    for r in results:
        engs = ", ".join([f"{k}:{v}" for k, v in r["engine_breakdown"].items()]) if r["engine_breakdown"] else "İşlem Yok"
        print(f"{r['name']:<26} | {r['executed']:<6} | %{r['win_rate']:<5} | {r['net_r']:<+9.2f} | {r['exp_r']:<+12.3f} | {r['pf']:<6} | -{r['max_dd']:<6.2f} | {engs}")
    print("=" * 105 + "\n")


if __name__ == "__main__":
    run_active_monthly_slices()
