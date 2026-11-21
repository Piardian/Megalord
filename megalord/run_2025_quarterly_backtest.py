"""Megalord 2025 Multi-Month Historical Backtest (3 Distinct Months).
With Production Rules:
1. No Same-Day Re-entry after SL (Stop yiyen parite o gün tekrar işlem alamaz).
2. Cleaned Institutional Whitelist (Safra pariteler CADJPY, AUDJPY vb. tamamen hariçtir).
3. Minimum 1.0R TP1 (%50 Kâr Alımı + BE Koruması).
4. Macro Gate & Natural Band Alignment.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BEGONYA_ROOT = Path(r"C:\Users\piard\Desktop\Begonya00")
if str(BEGONYA_ROOT) not in sys.path:
    sys.path.insert(0, str(BEGONYA_ROOT))

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

from megalord.types import MathFamily, StrategyRegime, TradeSignal
from megalord.simulator.master_engine import MegalordSimulator
from sfp_orb_engine.config import get_asset_sl_buffer, is_crypto
from sfp_orb_engine.macro_gated_3m_backtest import DailyMacroGateTable

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("Megalord.2025Backtest")

# Verified Cleaned Institutional Whitelist
SYMBOLS = [
    # Forex Majörler & Kaliteli Çaprazlar
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "NZDUSD",
    "CHFJPY", "CADCHF", "GBPCHF",
    # Emtia & Endeks
    "XAUUSD", "NAS100",
    # 24/7 Kripto
    "BTCUSD", "ETHUSD", "SOLUSD"
]


class HistoricalEngineSignalGenerator:
    """Generates deterministic signals for Begonya00 SFP with No-Same-Day-Reentry."""

    def __init__(self, start_dt: datetime, end_dt: datetime):
        self.start_dt = start_dt
        self.end_dt = end_dt

    def fetch_m15(self, symbol: str) -> Optional[pd.DataFrame]:
        warmup = self.start_dt - timedelta(days=25)
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, warmup, self.end_dt)
        if rates is None or len(rates) < 300:
            return None
        df = pd.DataFrame(rates)
        df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["date"] = df["datetime"].dt.date
        df["hour"] = df["datetime"].dt.hour
        df["weekday"] = df["datetime"].dt.weekday

        # PDH & PDL
        daily = df.groupby("date").agg({"high": "max", "low": "min"}).shift(1)
        df["pdh"] = df["date"].map(daily["high"]).fillna(0.0)
        df["pdl"] = df["date"].map(daily["low"]).fillna(0.0)

        # 4H EMA Trend (200 & 800 M15 bars)
        df["htf_ema50"] = df["close"].ewm(span=200, adjust=False).mean()
        df["htf_ema200"] = df["close"].ewm(span=800, adjust=False).mean()

        # ATR 14
        tr = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                np.abs(df["high"] - df["close"].shift(1)),
                np.abs(df["low"] - df["close"].shift(1))
            )
        )
        df["atr"] = tr.rolling(14).mean().fillna(df["high"] - df["low"])
        return df

    def generate_signals(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []

        for sym in SYMBOLS:
            df = self.fetch_m15(sym)
            if df is None:
                continue

            sl_buf_mult = get_asset_sl_buffer(f"{sym[:3]}/{sym[3:]}" if len(sym) == 6 and sym.isalpha() else sym)
            is_crypto_sym = is_crypto(sym)

            n = len(df)
            highs = df["high"].values
            lows = df["low"].values
            closes = df["close"].values
            pdhs = df["pdh"].values
            pdls = df["pdl"].values
            atrs = df["atr"].values
            ema50s = df["htf_ema50"].values
            ema200s = df["htf_ema200"].values
            hours = df["hour"].values
            weekdays = df["weekday"].values
            dates = df["date"].values
            dts = df["datetime"].dt.to_pydatetime()

            stopped_out_dates = set()
            active_pos_exit_idx = -1

            for i in range(100, n):
                dt_bar = dts[i]
                cur_date = dates[i]
                if dt_bar < self.start_dt or dt_bar > self.end_dt:
                    continue

                # Don't open while another position on same symbol is still active
                if i < active_pos_exit_idx:
                    continue

                # Rule: No same-day re-entry after stop loss
                if cur_date in stopped_out_dates:
                    continue

                c_high = highs[i]
                c_low = lows[i]
                c_close = closes[i]
                c_atr = atrs[i]
                pdh = pdhs[i]
                pdl = pdls[i]
                ema50 = ema50s[i]
                ema200 = ema200s[i]
                h_val = hours[i]
                wday = weekdays[i]

                # Session gating: London (07-11 UTC) and New York (13-17 UTC)
                # Weekend: Only Crypto
                is_weekend = (wday == 4 and h_val >= 21) or (wday == 5) or (wday == 6 and h_val < 21)
                if is_weekend and not is_crypto_sym:
                    continue

                if not is_weekend and h_val not in [7, 8, 9, 10, 13, 14, 15, 16]:
                    continue

                # SFP Bullish
                if c_low < pdl and c_close > pdl and pdl > 0:
                    trend_bull = ema50 > ema200 or c_close > ema50
                    if trend_bull:
                        entry_p = c_close
                        sl_p = c_low - (c_atr * sl_buf_mult)
                        risk_d = entry_p - sl_p
                        if risk_d > 0:
                            tp1_p = entry_p + risk_d * 1.0
                            tp2_p = entry_p + risk_d * 2.0

                            raw_r = -1.0
                            exit_time = dt_bar + timedelta(hours=4)
                            active_pos_exit_idx = min(i + 32, n)
                            hit_tp1 = False

                            for f in range(i + 1, min(i + 32, n)):
                                f_high = highs[f]
                                f_low = lows[f]
                                f_dt = dts[f]

                                if not hit_tp1:
                                    if f_low <= sl_p:
                                        raw_r = -1.0
                                        exit_time = f_dt
                                        active_pos_exit_idx = f
                                        stopped_out_dates.add(cur_date)
                                        break
                                    if f_high >= tp1_p:
                                        hit_tp1 = True
                                        sl_p = entry_p
                                else:
                                    if f_low <= entry_p:
                                        raw_r = 0.50
                                        exit_time = f_dt
                                        active_pos_exit_idx = f
                                        break
                                    if f_high >= tp2_p:
                                        raw_r = 1.50
                                        exit_time = f_dt
                                        active_pos_exit_idx = f
                                        break

                            sig = TradeSignal(
                                signal_id=f"B00_{sym}_{dt_bar.strftime('%Y%m%d%H%M')}",
                                engine_name="Begonya00",
                                math_family=MathFamily.FAMILY_SMC,
                                strategy_regime=StrategyRegime.TREND_MOMENTUM,
                                symbol=sym,
                                direction="LONG",
                                entry_time=pd.to_datetime(dt_bar, utc=True),
                                exit_time=pd.to_datetime(exit_time, utc=True),
                                entry_price=entry_p,
                                sl_price=sl_p,
                                tp1_price=tp1_p,
                                tp2_price=tp2_p,
                                raw_r_result=raw_r,
                                sub_strategy="SFP_PDL_Sweep"
                            )
                            signals.append(sig)

                # SFP Bearish
                elif c_high > pdh and c_close < pdh and pdh > 0:
                    trend_bear = ema50 < ema200 or c_close < ema50
                    if trend_bear:
                        entry_p = c_close
                        sl_p = c_high + (c_atr * sl_buf_mult)
                        risk_d = sl_p - entry_p
                        if risk_d > 0:
                            tp1_p = entry_p - risk_d * 1.0
                            tp2_p = entry_p - risk_d * 2.0

                            raw_r = -1.0
                            exit_time = dt_bar + timedelta(hours=4)
                            active_pos_exit_idx = min(i + 32, n)
                            hit_tp1 = False

                            for f in range(i + 1, min(i + 32, n)):
                                f_high = highs[f]
                                f_low = lows[f]
                                f_dt = dts[f]

                                if not hit_tp1:
                                    if f_high >= sl_p:
                                        raw_r = -1.0
                                        exit_time = f_dt
                                        active_pos_exit_idx = f
                                        stopped_out_dates.add(cur_date)
                                        break
                                    if f_low <= tp1_p:
                                        hit_tp1 = True
                                        sl_p = entry_p
                                else:
                                    if f_high >= entry_p:
                                        raw_r = 0.50
                                        exit_time = f_dt
                                        active_pos_exit_idx = f
                                        break
                                    if f_low <= tp2_p:
                                        raw_r = 1.50
                                        exit_time = f_dt
                                        active_pos_exit_idx = f
                                        break

                            sig = TradeSignal(
                                signal_id=f"B00_{sym}_{dt_bar.strftime('%Y%m%d%H%M')}",
                                engine_name="Begonya00",
                                math_family=MathFamily.FAMILY_SMC,
                                strategy_regime=StrategyRegime.TREND_MOMENTUM,
                                symbol=sym,
                                direction="SHORT",
                                entry_time=pd.to_datetime(dt_bar, utc=True),
                                exit_time=pd.to_datetime(exit_time, utc=True),
                                entry_price=entry_p,
                                sl_price=sl_p,
                                tp1_price=tp1_p,
                                tp2_price=tp2_p,
                                raw_r_result=raw_r,
                                sub_strategy="SFP_PDH_Sweep"
                            )
                            signals.append(sig)

        return signals


def run_2025_monthly_audit():
    if not mt5.initialize():
        print("MT5 initialization failed!")
        return

    month_windows = [
        {"name": "2025 Mart (Q1: 01 Mar - 31 Mar 2025)", "start": "2025-03-01 00:00:00", "end": "2025-03-31 23:59:59"},
        {"name": "2025 Temmuz (Q3: 01 Tem - 31 Tem 2025)", "start": "2025-07-01 00:00:00", "end": "2025-07-31 23:59:59"},
        {"name": "2025 Kasım (Q4: 01 Kas - 30 Kas 2025)", "start": "2025-11-01 00:00:00", "end": "2025-11-30 23:59:59"},
    ]

    print("Building historical Macro Gate Table across 2025...")
    macro_table = DailyMacroGateTable(date(2025, 2, 1), date(2025, 12, 15))

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

    all_monthly_reports = []

    for win in month_windows:
        dt_start = pd.to_datetime(win["start"], utc=True)
        dt_end = pd.to_datetime(win["end"], utc=True)

        print(f"\n========================================================")
        print(f"Generating signals for {win['name']}...")
        generator = HistoricalEngineSignalGenerator(dt_start.to_pydatetime(), dt_end.to_pydatetime())
        month_signals = generator.generate_signals()
        print(f"Total raw signals generated: {len(month_signals)}")

        sim = MegalordSimulator()
        sim_res = sim.run_orchestration(month_signals, macro_gate_provider=macro_gate_provider)

        executed = sim_res["executed_trades"]
        rejected = sim_res["rejected_trades"]

        r_list = [t["effective_r"] for t in executed]
        wins = [r for r in r_list if r > 0.01]
        losses = [r for r in r_list if r < -0.01]
        bes = [r for r in r_list if abs(r) <= 0.01]

        total_exec = len(executed)
        win_count = len(wins)
        loss_count = len(losses)
        be_count = len(bes)

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

        sym_stats = {}
        for t in executed:
            s = t["symbol"]
            if s not in sym_stats:
                sym_stats[s] = {"trades": 0, "wins": 0, "losses": 0, "net_r": 0.0}
            sym_stats[s]["trades"] += 1
            sym_stats[s]["net_r"] += t["effective_r"]
            if t["effective_r"] > 0:
                sym_stats[s]["wins"] += 1
            else:
                sym_stats[s]["losses"] += 1

        all_monthly_reports.append({
            "window": win["name"],
            "total_signals": len(month_signals),
            "executed_trades": total_exec,
            "rejected_signals": len(rejected),
            "win_count": win_count,
            "loss_count": loss_count,
            "be_count": be_count,
            "win_rate_pct": win_rate,
            "net_r": net_r,
            "expectancy_r": exp_r,
            "profit_factor": pf,
            "max_drawdown_r": max_dd,
            "annualized_sharpe": sim_res.get("annualized_sharpe", 0.0),
            "symbol_breakdown": sym_stats,
            "trades": [
                {
                    "time": str(t["entry_time"]),
                    "symbol": t["symbol"],
                    "direction": t["direction"],
                    "risk_r": t["risk_r"],
                    "net_r": round(float(t["effective_r"]), 2),
                    "action": t.get("arbiter_action", "PROCEED")
                }
                for t in executed
            ]
        })

    out_file = PROJECT_ROOT / "megalord_2025_3months_backtest.json"
    out_file.write_text(json.dumps(all_monthly_reports, indent=2, default=str), encoding="utf-8")
    print(f"\n✅ 2025 Backtest Report saved to: {out_file}\n")

    print("=" * 105)
    print(f"{'2025 DÖNEMİ':<32} | {'İŞLEM':<6} | {'WR %':<7} | {'NET R':<8} | {'BEKLENTİ':<10} | {'PF':<6} | {'MAX DD'}")
    print("-" * 105)
    for r in all_monthly_reports:
        print(f"{r['window']:<32} | {r['executed_trades']:<6} | %{r['win_rate_pct']:<5} | {r['net_r']:<+8.2f} | {r['expectancy_r']:<+10.3f} | {r['profit_factor']:<6} | -{r['max_drawdown_r']:<6.2f}")
    print("=" * 105)

    mt5.shutdown()


if __name__ == "__main__":
    run_2025_monthly_audit()
