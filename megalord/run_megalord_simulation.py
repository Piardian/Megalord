"""Full 7-Engine Megalord Master Simulation Runner across 1-Year and 3-Month Windows."""
from __future__ import annotations

import json
import sys
from pathlib import Path
import pandas as pd

PROJECT_DIR = Path(r"C:\Users\piard\Desktop\MegalordProject")
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from megalord.adapters.stream_loader import ProjectStreamLoader
from megalord.simulator.master_engine import MegalordSimulator

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main():
    print("=" * 105)
    print(" 👑 MEGALORD 7-PROJE META-ORKESTRATÖR VE MASTER ALOKASYON SİMÜLASYONU 👑 ")
    print("   • Mimari Modeller : 5 Ortogonal Aile (SMC, Macro Trend, AMT/VWAP, Orderflow, Stat Quant)")
    print("   • Klon Koruması   : Aynı aile içi (Begonya00 + Begonya) sinerjisi 1.0R tavanla sınırlandırıldı")
    print("   • Ortogonal Boost : Farklı ailelerden gelen eşzamanlı teyitlerde 1.30x risk çarpanı")
    print("   • Trend vs MR     : Karşı-trend momentum vetosu (HTF_VETO) / Karşı-trend Mean-Reversion (0.50x Risk)")
    print("   • Risk Firewall   : Worst-Case Günlük Risk Bütçesi (%2.5) + Küresel Döviz Tavanı (1.5R)")
    print("=" * 105 + "\n")

    loader = ProjectStreamLoader()
    print("📡 7 Bağımsız Motorun Gerçek Veri Akışları Yükleniyor...")
    all_signals = loader.load_all_signals()
    print(f"✅ Toplam {len(all_signals)} adet ham sinyal başarıyla yüklendi.\n")

    sim = MegalordSimulator()

    # 1. Tam 1 Yıllık Test (2025-10-01 ile 2026-10-19)
    sigs_1y = [s for s in all_signals if s.entry_time >= pd.Timestamp("2025-10-01", tz="UTC")]
    res_1y = sim.run_orchestration(sigs_1y)

    # 2. Son 3 Aylık Test (2026-07-01 ile 2026-10-19)
    sigs_3m = [s for s in all_signals if s.entry_time >= pd.Timestamp("2026-07-01", tz="UTC")]
    res_3m = sim.run_orchestration(sigs_3m)

    # Motor bazlı breakdown
    by_engine_1y = {}
    for t in res_1y["executed_trades"]:
        eng = t["engine_name"]
        by_engine_1y.setdefault(eng, []).append(t["effective_r"])

    print("=" * 105)
    print(" 📊 1 YILLIK VE 3 AYLIK MEGALORD SİMÜLASYON KARNESİ 📊")
    print("=" * 105)
    print(f"{'Metrik':<38} │ {'Son 1 Yıl (Megalord Master)':<30} │ {'Son 3 Ay (Megalord Master)'}")
    print("-" * 105)
    print(f"{'Toplam İşlem Sayısı (N)':<38} │ {res_1y['executed_trades_count']:>4} işlem{'':<20} │ {res_3m['executed_trades_count']:>4} işlem")
    print(f"{'Engellenen / Reddedilen Sinyal':<38} │ {res_1y['rejected_trades_count']:>4} sinyal{'':<19} │ {res_3m['rejected_trades_count']:>4} sinyal")
    print(f"{'Kazanma Oranı (Win Rate)':<38} │ %{res_1y['win_rate_pct']:>5.1f}{'':<24} │ %{res_3m['win_rate_pct']:>5.1f}")
    print(f"{'Kâr Faktörü (Profit Factor)':<38} │ {res_1y['profit_factor']:>6.2f}{'':<24} │ {res_3m['profit_factor']:>6.2f}")
    print(f"{'Matematiksel Beklenti (Expectancy)':<38} │ {res_1y['expectancy_r']:>+6.3f} R / işlem{'':<15} │ {res_3m['expectancy_r']:>+6.3f} R / işlem")
    print(f"{'Toplam Net Getiri (R)':<38} │ {res_1y['net_r']:>+7.2f} R{'':<21} │ {res_3m['net_r']:>+7.2f} R")
    print(f"{'Maksimum Çekilme (Max DD R)':<38} │ -{res_1y['max_drawdown_r']:>5.2f} R{'':<22} │ -{res_3m['max_drawdown_r']:>5.2f} R")
    print(f"{'Getiri / Çekilme Oranı (Return/DD)':<38} │ {res_1y['return_to_dd']:>6.2f} x{'':<23} │ {res_3m['return_to_dd']:>6.2f} x")
    print(f"{'Yıllıklandırılmış Sharpe Oranı':<38} │ {res_1y['annualized_sharpe']:>6.2f}{'':<24} │ {res_3m['annualized_sharpe']:>6.2f}")
    print("=" * 105 + "\n")

    print("=" * 105)
    print(" 🏛️ 1 YILLIK MOTOR BAZLI NET KATKI DAĞILIMI")
    print("=" * 105)
    for eng, r_vals in sorted(by_engine_1y.items()):
        w = [r for r in r_vals if r > 0]
        n_eng = len(r_vals)
        wr_eng = (len(w) / n_eng * 100.0) if n_eng > 0 else 0.0
        net_eng = sum(r_vals)
        print(f"  • {eng:<20}: {n_eng:>3} işlem │ Win Rate: %{wr_eng:>4.1f} │ Net: {net_eng:>+6.2f} R")
    print("=" * 105 + "\n")

    # Hakem Karar Dağılımı (Arbiter & Firewall Actions)
    action_counts = {}
    for t in res_1y["executed_trades"]:
        act = t["arbiter_action"]
        action_counts[act] = action_counts.get(act, 0) + 1
    for r in res_1y["rejected_trades"]:
        act = r["action"]
        action_counts[act] = action_counts.get(act, 0) + 1

    print("=" * 105)
    print(" 🛡️ MEGALORD HAKEM & KORUMA KALKANI KARAR İSTATİSTİKLERİ")
    print("=" * 105)
    for act, cnt in sorted(action_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {act:<28}: {cnt:>3} kez tetiklendi")
    print("=" * 105 + "\n")

    # Save results to json
    out_file = PROJECT_DIR / "megalord_master_backtest_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        # Don't serialize non-serializable objects
        json.dump({
            "results_1y": {k: v for k, v in res_1y.items() if k not in ("executed_trades", "rejected_trades")},
            "results_3m": {k: v for k, v in res_3m.items() if k not in ("executed_trades", "rejected_trades")},
            "action_counts": action_counts
        }, f, indent=2)
    print(f"📁 Megalord master sonuçları kaydedildi: {out_file}")


if __name__ == "__main__":
    main()
