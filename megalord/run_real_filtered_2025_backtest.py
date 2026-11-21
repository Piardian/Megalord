"""Megalord Real Strict Institutional Filtered Backtest across 2025 (3 Distinct Months).
Uses the genuine Begonya00 & Megalord institutional production pipeline:
1. SFP Liquidity Sweep (PDH/PDL with minimum 35% wick ratio).
2. MSS (Market Structure Shift) on confirmation bars with solid body.
3. 4H Trend Filter (EMA200 / EMA800 alignment).
4. Macro Gate Validation (Strict LONG_ONLY / SHORT_ONLY alignment; NEUTRAL_RANGE blocked).
5. Seans Pencereleri: Londra (07:00 - 11:00 UTC) & New York (13:00 - 17:00 UTC). Hafta sonu sadece Kripto.
6. TP1 = 1.0R (%50 Kâr + Stop BE), TP2 = 2.0R (Kalan %50).
7. No Same-Day Re-entry on SL.
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

from sfp_orb_engine.config import get_asset_sl_buffer, is_crypto
from sfp_orb_engine.macro_gated_3m_backtest import DailyMacroGateTable

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("Megalord.Strict2025")

# Safra Paritelerden Arındırılmış Kurumsal Evren
STRICT_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "NZDUSD",
    "XAUUSD", "BTCUSD", "ETHUSD", "SOLUSD"
]


class StrictInstitutionalRunner:
    def __init__(self, start_dt: datetime, end_dt: datetime, macro_table: DailyMacroGateTable):
        self.start_dt = start_dt
        self.end_dt = end_dt
        self.macro_table = macro_table

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

    def run_symbol(self, symbol: str) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        df = self.fetch_m15(symbol)
        if df is None:
            return [], {}

        sl_buf_mult = get_asset_sl_buffer(f"{symbol[:3]}/{symbol[3:]}" if len(symbol) == 6 and symbol.isalpha() else symbol)
        is_crypto_sym = is_crypto(symbol)

        n = len(df)
        opens = df["open"].values
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

        trades = []
        active_pos = None
        stopped_out_dates = set()
        veto_counts = {
            "VETO_MACRO": 0,
            "VETO_TREND": 0,
            "VETO_SESSION": 0,
            "VETO_NO_MSS": 0,
            "APPROVED": 0
        }

        for i in range(100, n):
            c_open = opens[i]
            c_high = highs[i]
            c_low = lows[i]
            c_close = closes[i]
            c_atr = atrs[i]
            cur_date = dates[i]
            dt_bar = dts[i]
            h_val = hours[i]
            wday = weekdays[i]
            bar_rng = max(c_high - c_low, 1e-8)

            # 1. Manage Active Position
            if active_pos is not None:
                p = active_pos
                entry = p["entry"]
                sl = p["current_sl"]
                tp1 = p["tp1"]
                tp2 = p["tp2"]
                status = p["status"]
                p_dir = p["direction"]
                p["bars_held"] += 1

                closed = False
                close_reason = ""
                r_res = 0.0

                if p_dir == "LONG":
                    # TP1 Check (1.0R -> %50 Kar, SL = BE)
                    if c_high >= tp1 and status == "OPEN":
                        p["status"] = "TP1_HIT"
                        p["current_sl"] = entry
                        sl = entry

                    if c_high >= tp2:
                        closed = True
                        close_reason = "TP2_HIT"
                        r_res = 1.50  # %50 TP1 (+0.5R) + %50 TP2 (+1.0R) = +1.5R
                    elif c_low <= sl:
                        closed = True
                        if status == "TP1_HIT":
                            close_reason = "BREAK_EVEN"
                            r_res = 0.50  # %50 TP1 (+0.5R) + %50 BE (0.0R) = +0.5R
                        else:
                            close_reason = "STOP_LOSS"
                            r_res = -1.0
                            stopped_out_dates.add(cur_date)

                elif p_dir == "SHORT":
                    if c_low <= tp1 and status == "OPEN":
                        p["status"] = "TP1_HIT"
                        p["current_sl"] = entry
                        sl = entry

                    if c_low <= tp2:
                        closed = True
                        close_reason = "TP2_HIT"
                        r_res = 1.50
                    elif c_high >= sl:
                        closed = True
                        if status == "TP1_HIT":
                            close_reason = "BREAK_EVEN"
                            r_res = 0.50
                        else:
                            close_reason = "STOP_LOSS"
                            r_res = -1.0
                            stopped_out_dates.add(cur_date)

                if closed:
                    p["exit_time"] = dt_bar.strftime("%Y-%m-%d %H:%M")
                    p["close_reason"] = close_reason
                    p["net_r"] = r_res
                    trades.append(p)
                    active_pos = None

                continue

            if dt_bar < self.start_dt or dt_bar > self.end_dt:
                continue

            # No same-day re-entry after SL
            if cur_date in stopped_out_dates:
                continue

            # Session Filtering
            is_weekend = (wday == 4 and h_val >= 21) or (wday == 5) or (wday == 6 and h_val < 21)
            if is_weekend:
                if not is_crypto_sym:
                    continue
                session_ok = (7 <= h_val <= 11) or (13 <= h_val <= 17)
            else:
                session_ok = (7 <= h_val <= 11) or (13 <= h_val <= 17)

            if not session_ok:
                veto_counts["VETO_SESSION"] += 1
                continue

            pdh = pdhs[i]
            pdl = pdls[i]
            if pdh == 0.0 or pdl == 0.0:
                continue

            upper_wick = (c_high - max(c_open, c_close)) / bar_rng
            lower_wick = (min(c_open, c_close) - c_low) / bar_rng

            # Macro Gate Check
            gate = self.macro_table.get_gate(dt_bar, symbol)
            if gate in ("NEUTRAL_RANGE", "NO_TRADE", "DEFENSIVE_HOLD", "VETO"):
                veto_counts["VETO_MACRO"] += 1
                continue

            htf_bull = ema50s[i] > ema200s[i]
            htf_bear = ema50s[i] < ema200s[i]

            # 1. Bullish SFP Setup
            if lower_wick >= 0.35 and c_low < pdl and c_close > pdl and htf_bull and gate == "LONG_ONLY":
                # Look for Market Structure Shift on next 1-3 bars
                mss_trigger = max(c_high, highs[i-1])
                mss_found = False
                entry_idx = -1

                for k in range(1, 4):
                    if i + k < n and closes[i+k] > mss_trigger:
                        b_rng = max(highs[i+k] - lows[i+k], 1e-8)
                        body_r = abs(closes[i+k] - opens[i+k]) / b_rng
                        if body_r >= 0.30:
                            mss_found = True
                            entry_idx = i + k
                            break

                if mss_found:
                    entry_p = closes[entry_idx]
                    sl_p = c_low - (c_atr * sl_buf_mult)
                    risk_d = entry_p - sl_p
                    if risk_d > 0:
                        veto_counts["APPROVED"] += 1
                        active_pos = {
                            "symbol": symbol,
                            "direction": "LONG",
                            "entry": entry_p,
                            "current_sl": sl_p,
                            "tp1": entry_p + risk_d * 1.0,
                            "tp2": entry_p + risk_d * 2.0,
                            "status": "OPEN",
                            "bars_held": 0,
                            "entry_time": dts[entry_idx].strftime("%Y-%m-%d %H:%M"),
                            "gate": gate
                        }
                else:
                    veto_counts["VETO_NO_MSS"] += 1

            # 2. Bearish SFP Setup
            elif upper_wick >= 0.35 and c_high > pdh and c_close < pdh and htf_bear and gate == "SHORT_ONLY":
                mss_trigger = min(c_low, lows[i-1])
                mss_found = False
                entry_idx = -1

                for k in range(1, 4):
                    if i + k < n and closes[i+k] < mss_trigger:
                        b_rng = max(highs[i+k] - lows[i+k], 1e-8)
                        body_r = abs(closes[i+k] - opens[i+k]) / b_rng
                        if body_r >= 0.30:
                            mss_found = True
                            entry_idx = i + k
                            break

                if mss_found:
                    entry_p = closes[entry_idx]
                    sl_p = c_high + (c_atr * sl_buf_mult)
                    risk_d = sl_p - entry_p
                    if risk_d > 0:
                        veto_counts["APPROVED"] += 1
                        active_pos = {
                            "symbol": symbol,
                            "direction": "SHORT",
                            "entry": entry_p,
                            "current_sl": sl_p,
                            "tp1": entry_p - risk_d * 1.0,
                            "tp2": entry_p - risk_d * 2.0,
                            "status": "OPEN",
                            "bars_held": 0,
                            "entry_time": dts[entry_idx].strftime("%Y-%m-%d %H:%M"),
                            "gate": gate
                        }
                else:
                    veto_counts["VETO_NO_MSS"] += 1

        return trades, veto_counts


def run_full_strict_audit():
    if not mt5.initialize():
        print("MT5 initialization failed!")
        return

    month_windows = [
        {"name": "2025 Mart (Q1)", "start": "2025-03-01 00:00:00", "end": "2025-03-31 23:59:59"},
        {"name": "2025 Temmuz (Q3)", "start": "2025-07-01 00:00:00", "end": "2025-07-31 23:59:59"},
        {"name": "2025 Kasım (Q4)", "start": "2025-11-01 00:00:00", "end": "2025-11-30 23:59:59"},
    ]

    print("Building historical Macro Gate Table across 2025...")
    macro_table = DailyMacroGateTable(date(2025, 2, 1), date(2025, 12, 15))

    results = []

    for win in month_windows:
        dt_start = pd.to_datetime(win["start"], utc=True).to_pydatetime()
        dt_end = pd.to_datetime(win["end"], utc=True).to_pydatetime()

        runner = StrictInstitutionalRunner(dt_start, dt_end, macro_table)
        all_month_trades = []
        total_vetos = {"VETO_MACRO": 0, "VETO_SESSION": 0, "VETO_NO_MSS": 0, "APPROVED": 0}

        for sym in STRICT_SYMBOLS:
            sym_trades, sym_vetos = runner.run_symbol(sym)
            all_month_trades.extend(sym_trades)
            for k in total_vetos:
                total_vetos[k] += sym_vetos.get(k, 0)

        r_list = [t["net_r"] for t in all_month_trades]
        tp2_trades = [t for t in all_month_trades if t["close_reason"] == "TP2_HIT"]
        be_trades = [t for t in all_month_trades if t["close_reason"] == "BREAK_EVEN"]
        sl_trades = [t for t in all_month_trades if t["close_reason"] == "STOP_LOSS"]

        total_exec = len(all_month_trades)
        win_count = len(tp2_trades) + len(be_trades)
        loss_count = len(sl_trades)

        net_r = round(float(sum(r_list)), 2)
        win_rate = round((win_count / total_exec * 100), 1) if total_exec > 0 else 0.0
        exp_r = round(float(np.mean(r_list)), 3) if total_exec > 0 else 0.0

        wins_val = sum(t["net_r"] for t in all_month_trades if t["net_r"] > 0)
        loss_val = abs(sum(t["net_r"] for t in all_month_trades if t["net_r"] < 0))
        pf = round(float(wins_val / loss_val), 2) if loss_val > 0 else (99.0 if wins_val > 0 else 0.0)

        cum = np.cumsum(r_list)
        peak = np.maximum.accumulate(cum) if len(cum) > 0 else np.array([0])
        dd = peak - cum if len(cum) > 0 else np.array([0])
        max_dd = round(float(np.max(dd)), 2) if len(dd) > 0 else 0.0

        results.append({
            "window": win["name"],
            "executed_trades": total_exec,
            "tp2_count": len(tp2_trades),
            "be_count": len(be_trades),
            "sl_count": len(sl_trades),
            "win_rate_pct": win_rate,
            "net_r": net_r,
            "expectancy_r": exp_r,
            "profit_factor": pf,
            "max_drawdown_r": max_dd,
            "veto_stats": total_vetos,
            "trades": all_month_trades
        })

    out_file = PROJECT_ROOT / "megalord_2025_strict_filtered_report.json"
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nStrict 2025 Backtest Report saved to: {out_file}\n")

    print("=" * 115)
    print(f"{'2025 DÖNEMİ':<24} | {'İŞLEM':<6} | {'TP2':<4} | {'BE':<4} | {'SL':<4} | {'WR %':<7} | {'NET R':<8} | {'BEKLENTİ':<10} | {'PF':<6} | {'MAX DD'}")
    print("-" * 115)
    for r in results:
        print(f"{r['window']:<24} | {r['executed_trades']:<6} | {r['tp2_count']:<4} | {r['be_count']:<4} | {r['sl_count']:<4} | %{r['win_rate_pct']:<5} | {r['net_r']:<+8.2f} | {r['expectancy_r']:<+10.3f} | {r['profit_factor']:<6} | -{r['max_drawdown_r']:<6.2f}")
    print("=" * 115)

    tot_trades = sum(r["executed_trades"] for r in results)
    tot_net_r = sum(r["net_r"] for r in results)
    tot_tp2 = sum(r["tp2_count"] for r in results)
    tot_be = sum(r["be_count"] for r in results)
    tot_sl = sum(r["sl_count"] for r in results)
    tot_wr = round((tot_tp2 + tot_be) / tot_trades * 100, 1) if tot_trades > 0 else 0.0
    print(f"{'3 AYIN GENEL BİLANÇOSU':<24} | {tot_trades:<6} | {tot_tp2:<4} | {tot_be:<4} | {tot_sl:<4} | %{tot_wr:<5} | {tot_net_r:<+8.2f}R")
    print("=" * 115)

    mt5.shutdown()


if __name__ == "__main__":
    run_full_strict_audit()
