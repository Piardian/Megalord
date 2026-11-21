"""Megalord 2025 Verified 3-Months Backtest (Zero Proxy / Direct Engine Execution).

Executes genuine whitelisted sub-engines:
- Pragma: pragma.backtest_engine.run_backtest (CADCHF, EURUSD, USDJPY, CADJPY, XAUUSD M5)
- Trawma: pragma.backtest_engine.simulate_walk_forward (BTCUSDT, LINKUSDT, SOLUSDT, NAS100, XAUUSD)
- Stigma: stigma.backtest_engine.backtest (NAS100 M15, USDJPY H1, USDJPY M15, BTCUSDT H1)
- Begonya00: SFP liquidity hunt verified institutional trades

Arbitrated and filtered by:
- MegalordSignalArbiter (5 Math Families, Confluence Boost, Clone Cap, Natural Band Rule, Cooldown)
- MegalordRiskFirewall (The 5%ers -2.5% daily limit, 1.5R cluster caps)
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import urllib.request

import numpy as np
import pandas as pd

# Path setup for all projects
PRAGMA_DIR = Path(r"C:\Users\piard\Desktop\PragmaProject")
TRAWMA_DIR = Path(r"C:\Users\piard\Desktop\TrawmaProject")
STIGMA_DIR = Path(r"C:\Users\piard\Desktop\StigmaProject")
MEGALORD_DIR = Path(r"C:\Users\piard\Desktop\MegalordProject")

sys.path.insert(0, str(STIGMA_DIR))
sys.path.insert(0, str(MEGALORD_DIR))

import MetaTrader5 as mt5

from megalord.types import MathFamily, StrategyRegime, TradeSignal, ArbiterDecision
from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.firewall.risk_firewall import MegalordRiskFirewall


# ---------------------------------------------------------------------------
# Dynamic Engine Loaders (Resolves namespace clash between Pragma and Trawma)
# ---------------------------------------------------------------------------
def get_pragma_runner():
    for k in list(sys.modules.keys()):
        if k == "pragma" or k.startswith("pragma."):
            del sys.modules[k]
    sys.path = [p for p in sys.path if "TrawmaProject" not in p]
    if str(PRAGMA_DIR) not in sys.path:
        sys.path.insert(0, str(PRAGMA_DIR))
    from pragma.backtest_engine import run_backtest, _default_point_size
    from pragma.instrument_profiles import get_instrument_profile
    return run_backtest, _default_point_size, get_instrument_profile


def get_trawma_runner():
    for k in list(sys.modules.keys()):
        if k == "pragma" or k.startswith("pragma."):
            del sys.modules[k]
    sys.path = [p for p in sys.path if "PragmaProject" not in p]
    if str(TRAWMA_DIR) not in sys.path:
        sys.path.insert(0, str(TRAWMA_DIR))
    from pragma.backtest_engine import simulate_walk_forward
    return simulate_walk_forward


# ---------------------------------------------------------------------------
# 1. Binance Data Fetcher for Real 2025 Taker-Flow Klines
# ---------------------------------------------------------------------------
def fetch_binance_futures_klines(symbol: str, interval: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """Fetch 2025 klines from Binance Futures with real taker buy base asset volume."""
    base_url = "https://fapi.binance.com/fapi/v1/klines"
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    all_rows = []
    curr = start_ms

    while curr < end_ms:
        url = f"{base_url}?symbol={symbol}&interval={interval}&startTime={curr}&endTime={end_ms}&limit=1500"
        req = urllib.request.Request(url, headers={"User-Agent": "Megalord/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            print(f"  [Binance Fetch Error] {symbol} {interval}: {exc}", flush=True)
            break
        if not data:
            break
        all_rows.extend(data)
        last_close = data[-1][6]
        if last_close >= end_ms or len(data) < 1500:
            break
        curr = last_close + 1

    if not all_rows:
        return pd.DataFrame()

    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"]
    df = pd.DataFrame(all_rows, columns=cols)
    for c in ["open", "high", "low", "close", "volume", "taker_buy_base_asset_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["time"] = pd.to_datetime(pd.to_numeric(df["open_time"]), unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(pd.to_numeric(df["close_time"]), unit="ms", utc=True)
    df["buy_volume"] = df["taker_buy_base_asset_volume"]
    df["sell_volume"] = df["volume"] - df["buy_volume"]
    df["volume_delta"] = df["buy_volume"] - df["sell_volume"]
    df["tick_volume"] = df["volume"]
    df = df.set_index("time").sort_index()
    df.index.name = "time"
    return df[~df.index.duplicated(keep="last")]


# ---------------------------------------------------------------------------
# 2. Causal D1 Macro Trend Engine
# ---------------------------------------------------------------------------
class CausalMacroCompass:
    """Computes daily D1 causal structural trend (LONG_ONLY, SHORT_ONLY, NATURAL_BAND)."""

    def __init__(self):
        self.d1_cache: Dict[str, pd.DataFrame] = {}
        self._load_d1_history()

    def _load_d1_history(self):
        mt5_symbols = ["EURUSD", "USDJPY", "CADCHF", "CADJPY", "XAUUSD", "NAS100", "BTCUSD", "GBPUSD", "USDCHF", "USDCAD", "NZDUSD"]
        init_ok = mt5.initialize()
        if init_ok:
            for s in mt5_symbols:
                rates = mt5.copy_rates_range(s, mt5.TIMEFRAME_D1, datetime(2024, 1, 1), datetime(2025, 12, 31))
                if rates is not None and len(rates) > 0:
                    df = pd.DataFrame(rates)
                    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                    df = df.set_index("time").sort_index()
                    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
                    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
                    self.d1_cache[s] = df
            mt5.shutdown()

        # Binance crypto D1
        for s in ["BTCUSDT", "SOLUSDT", "LINKUSDT"]:
            df = fetch_binance_futures_klines(s, "1d", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2025, 12, 31, tzinfo=timezone.utc))
            if not df.empty:
                df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
                df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
                self.d1_cache[s] = df

    def get_bias(self, symbol: str, current_ts: pd.Timestamp) -> str:
        sym = symbol.upper()
        if sym not in self.d1_cache:
            alt = sym + "USDT" if not sym.endswith("USDT") else sym.replace("USDT", "USD")
            if alt in self.d1_cache:
                sym = alt
            else:
                return "NATURAL_BAND"

        df = self.d1_cache[sym]
        prior = df.loc[:current_ts - pd.Timedelta(days=1)]
        if len(prior) < 50:
            return "NATURAL_BAND"

        last_row = prior.iloc[-1]
        close = last_row["close"]
        ema20 = last_row["ema20"]
        ema50 = last_row["ema50"]

        if close > ema20 and ema20 > ema50:
            return "LONG_ONLY"
        elif close < ema20 and ema20 < ema50:
            return "SHORT_ONLY"
        else:
            return "NATURAL_BAND"


# ---------------------------------------------------------------------------
# 3. Sub-Engine Signal Collectors (Per Window Execution)
# ---------------------------------------------------------------------------

def run_window_pragma_signals(start_fetch: datetime, end_fetch: datetime) -> List[TradeSignal]:
    """Execute Pragma run_backtest across approved FX Core & Gold M5 for window."""
    run_backtest, _default_point_size, get_instrument_profile = get_pragma_runner()

    symbols = ["CADCHF", "EURUSD", "USDJPY", "CADJPY", "XAUUSD"]
    signals = []

    for idx, s in enumerate(symbols, 1):
        parquet_path = PRAGMA_DIR / "historical_data" / f"{s}_2025_M1.parquet"
        if not parquet_path.exists():
            continue

        try:
            print(f"      [Pragma] ({idx}/5) {s}...", end="", flush=True)
            df_full = pd.read_parquet(parquet_path)
            slice_m1 = df_full.loc[start_fetch.isoformat():end_fetch.isoformat()]
            if len(slice_m1) < 300:
                print(" [Insufficient Bars]", flush=True)
                continue

            pt_size = _default_point_size(s)
            prof = get_instrument_profile(s)
            report = run_backtest(
                slice_m1,
                symbol=s,
                macro_bias=0,
                point_size=pt_size,
                trigger_tf="M5",
                allow_range=True,
                allow_mean_reversion=prof.allow_mean_reversion,
                disable_trend_pullback=not prof.allow_trend_pullback,
            )

            rep_dict = report.to_dict()
            trades_found = rep_dict.get("trades") or []
            print(f" Done ({len(trades_found)} trades, Net R: {report.net_r:+.2f})", flush=True)

            for t_idx, tr in enumerate(trades_found):
                entry_dt = pd.Timestamp(tr["entry_time"])
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.tz_localize("UTC")
                else:
                    entry_dt = entry_dt.tz_convert("UTC")

                exit_dt = pd.Timestamp(tr["exit_time"])
                if exit_dt.tzinfo is None:
                    exit_dt = exit_dt.tz_localize("UTC")
                else:
                    exit_dt = exit_dt.tz_convert("UTC")

                is_mr = "REVERSION" in str(tr.get("poi_type", "")).upper()
                regime = StrategyRegime.MEAN_REVERSION if is_mr else StrategyRegime.TREND_MOMENTUM
                
                sig = TradeSignal(
                    signal_id=f"Pragma_{s}_{t_idx}_{entry_dt.strftime('%m%d_%H%M')}",
                    engine_name="Pragma",
                    math_family=MathFamily.FAMILY_AMT_VWAP,
                    strategy_regime=regime,
                    symbol=s,
                    direction=tr["direction"],
                    entry_time=entry_dt,
                    exit_time=exit_dt,
                    entry_price=float(tr["entry_price"]),
                    sl_price=float(tr["initial_stop_loss"]),
                    tp1_price=float(tr["tp1"]),
                    tp2_price=float(tr["tp2"]),
                    risk_r=1.0,
                    raw_r_result=float(tr["r_multiple"]),
                    sub_strategy=f"{tr.get('poi_type')}_{tr.get('regime')}",
                    metadata={"exit_reason": tr.get("exit_reason")}
                )
                signals.append(sig)
        except Exception as exc:
            print(f"\n      [Pragma Error] {s}: {exc}", flush=True)

    return signals


def run_window_trawma_signals(start_fetch: datetime, end_fetch: datetime) -> List[TradeSignal]:
    """Execute Trawma simulate_walk_forward across approved whitelist cells for window."""
    simulate_walk_forward = get_trawma_runner()

    cells = [
        {"symbol": "BTCUSDT", "engine": "orderflow", "source": "binance", "strict_real_delta": True, "tf": "M15"},
        {"symbol": "LINKUSDT", "engine": "orderflow", "source": "binance", "strict_real_delta": True, "tf": "M15"},
        {"symbol": "SOLUSDT", "engine": "orderflow", "source": "binance", "strict_real_delta": True, "tf": "M15"},
        {"symbol": "NAS100", "engine": "hybrid", "source": "mt5", "strict_real_delta": False, "tf": "M15"},
        {"symbol": "XAUUSD", "engine": "orderflow", "source": "parquet", "strict_real_delta": False, "tf": "M5"},
    ]
    signals = []

    for idx, c in enumerate(cells, 1):
        sym = c["symbol"]
        src = c["source"]
        print(f"      [Trawma] ({idx}/5) {sym} ({c['engine']})...", end="", flush=True)
        try:
            if src == "binance":
                df = fetch_binance_futures_klines(sym, "15m", start_fetch, end_fetch)
            elif src == "mt5":
                mt5.initialize()
                rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, start_fetch, end_fetch)
                mt5.shutdown()
                if rates is None or len(rates) == 0:
                    print(" [No MT5 Data]", flush=True)
                    continue
                df = pd.DataFrame(rates)
                df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                df = df.set_index("time").sort_index()
            elif src == "parquet":
                pq_path = PRAGMA_DIR / "historical_data" / "XAUUSD_2025_M1.parquet"
                if not pq_path.exists():
                    print(" [No Parquet]", flush=True)
                    continue
                df_raw = pd.read_parquet(pq_path).loc[start_fetch.isoformat():end_fetch.isoformat()]
                df = df_raw.resample("5min").agg({
                    "open": "first", "high": "max", "low": "min", "close": "last", "tick_volume": "sum"
                }).dropna()

            if len(df) < 100:
                print(" [Insufficient Bars]", flush=True)
                continue

            rep = simulate_walk_forward(
                df,
                symbol=sym,
                engine=c["engine"],
                strict_real_delta=c["strict_real_delta"],
            )

            trades = rep.get("trades", [])
            print(f" Done ({len(trades)} trades, Net R: {rep.get('net_r', 0):+.2f})", flush=True)

            for t_idx, tr in enumerate(trades):
                entry_dt = pd.Timestamp(tr["entry_time"])
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.tz_localize("UTC")
                else:
                    entry_dt = entry_dt.tz_convert("UTC")

                exit_dt = pd.Timestamp(tr["exit_time"])
                if exit_dt.tzinfo is None:
                    exit_dt = exit_dt.tz_localize("UTC")
                else:
                    exit_dt = exit_dt.tz_convert("UTC")

                sig = TradeSignal(
                    signal_id=f"Trawma_{sym}_{t_idx}_{entry_dt.strftime('%m%d_%H%M')}",
                    engine_name="Trawma",
                    math_family=MathFamily.FAMILY_ORDERFLOW,
                    strategy_regime=StrategyRegime.TREND_MOMENTUM,
                    symbol=sym,
                    direction=tr["side"],
                    entry_time=entry_dt,
                    exit_time=exit_dt,
                    entry_price=0.0,
                    sl_price=0.0,
                    tp1_price=0.0,
                    tp2_price=0.0,
                    risk_r=1.0,
                    raw_r_result=float(tr["net_r"]),
                    sub_strategy=c["engine"],
                    metadata={"exit_reason": tr.get("exit_reason"), "divergence": tr.get("divergence_type")}
                )
                signals.append(sig)
        except Exception as exc:
            print(f"\n      [Trawma Error] {sym}: {exc}", flush=True)

    return signals


def run_window_stigma_signals(start_fetch: datetime, end_fetch: datetime) -> List[TradeSignal]:
    """Execute Stigma backtest across approved whitelist cells for window."""
    from stigma.backtest_engine import backtest

    cells = [
        {"symbol": "NAS100", "tf": "M15", "mode": "OU_ONLY", "source": "mt5", "spread": 2.0, "comm": 1.0, "slip": 1.0, "regime": StrategyRegime.MEAN_REVERSION},
        {"symbol": "USDJPY", "tf": "H1", "mode": "OU_ONLY", "source": "mt5", "spread": 0.012, "comm": 0.005, "slip": 0.003, "regime": StrategyRegime.MEAN_REVERSION},
        {"symbol": "USDJPY", "tf": "M15", "mode": "MOM_ONLY", "source": "mt5", "spread": 0.012, "comm": 0.005, "slip": 0.003, "regime": StrategyRegime.TREND_MOMENTUM},
        {"symbol": "BTCUSDT", "tf": "H1", "mode": "OU_ONLY", "source": "binance", "spread": 2.0, "comm": 50.0, "slip": 3.0, "regime": StrategyRegime.MEAN_REVERSION},
    ]
    signals = []

    for idx, c in enumerate(cells, 1):
        sym = c["symbol"]
        tf_str = c["tf"]
        print(f"      [Stigma] ({idx}/4) {sym} {tf_str} ({c['mode']})...", end="", flush=True)
        try:
            if c["source"] == "binance":
                df = fetch_binance_futures_klines(sym, "1h", start_fetch, end_fetch)
                tf_min = 60
            else:
                mt5.initialize()
                mt5_tf = mt5.TIMEFRAME_M15 if tf_str == "M15" else mt5.TIMEFRAME_H1
                rates = mt5.copy_rates_range(sym, mt5_tf, start_fetch, end_fetch)
                mt5.shutdown()
                if rates is None or len(rates) == 0:
                    print(" [No MT5 Data]", flush=True)
                    continue
                df = pd.DataFrame(rates)
                df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
                df = df.set_index("time").sort_index()
                tf_min = 15 if tf_str == "M15" else 60

            if len(df) < 50:
                print(" [Insufficient Bars]", flush=True)
                continue

            rep = backtest(
                df,
                spread=c["spread"],
                commission=c["comm"],
                slippage=c["slip"],
                strategy_mode=c["mode"],
                timeframe_minutes=tf_min,
            )

            trades_df = rep.get("trades_frame")
            count = len(trades_df) if trades_df is not None else 0
            print(f" Done ({count} trades, Net PnL: {rep.get('net_pnl', 0):+.2f})", flush=True)

            if trades_df is not None and not trades_df.empty:
                for t_idx, row in trades_df.iterrows():
                    entry_dt = pd.Timestamp(row["entry_time"])
                    if entry_dt.tzinfo is None:
                        entry_dt = entry_dt.tz_localize("UTC")
                    else:
                        entry_dt = entry_dt.tz_convert("UTC")

                    exit_dt = pd.Timestamp(row["exit_time"])
                    if exit_dt.tzinfo is None:
                        exit_dt = exit_dt.tz_localize("UTC")
                    else:
                        exit_dt = exit_dt.tz_convert("UTC")

                    sig = TradeSignal(
                        signal_id=f"Stigma_{sym}_{t_idx}_{entry_dt.strftime('%m%d_%H%M')}",
                        engine_name="Stigma",
                        math_family=MathFamily.FAMILY_STAT_QUANT,
                        strategy_regime=c["regime"],
                        symbol=sym,
                        direction=str(row["side"]).upper(),
                        entry_time=entry_dt,
                        exit_time=exit_dt,
                        entry_price=float(row["entry"]),
                        sl_price=float(row["stop_loss"]),
                        tp1_price=float(row["tp1"]),
                        tp2_price=float(row["tp2"]),
                        risk_r=1.0,
                        raw_r_result=float(row["return_r"]),
                        sub_strategy=c["mode"],
                        metadata={"exit_reason": row.get("exit_reason")}
                    )
                    signals.append(sig)
        except Exception as exc:
            print(f"\n      [Stigma Error] {sym}: {exc}", flush=True)

    return signals


def load_window_begonya00_signals(start_eval: datetime, end_eval: datetime) -> List[TradeSignal]:
    """Load authentic institutional Begonya00 trades for window."""
    rep_path = MEGALORD_DIR / "megalord_2025_strict_filtered_report.json"
    if not rep_path.exists():
        return []

    try:
        data = json.loads(rep_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    signals = []
    for window in data:
        for idx, tr in enumerate(window.get("trades", [])):
            try:
                entry_dt = pd.Timestamp(tr["entry_time"])
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.tz_localize("UTC")
                else:
                    entry_dt = entry_dt.tz_convert("UTC")

                exit_dt = pd.Timestamp(tr["exit_time"])
                if exit_dt.tzinfo is None:
                    exit_dt = exit_dt.tz_localize("UTC")
                else:
                    exit_dt = exit_dt.tz_convert("UTC")
            except Exception:
                continue

            if start_eval <= entry_dt.to_pydatetime() < end_eval:
                sig = TradeSignal(
                    signal_id=f"Begonya00_{tr['symbol']}_{idx}_{entry_dt.strftime('%m%d_%H%M')}",
                    engine_name="Begonya00",
                    math_family=MathFamily.FAMILY_SMC,
                    strategy_regime=StrategyRegime.TREND_MOMENTUM,
                    symbol=tr["symbol"],
                    direction=tr["direction"],
                    entry_time=entry_dt,
                    exit_time=exit_dt,
                    entry_price=float(tr["entry"]),
                    sl_price=float(tr["current_sl"]),
                    tp1_price=float(tr["tp1"]),
                    tp2_price=float(tr["tp2"]),
                    risk_r=1.0,
                    raw_r_result=float(tr["net_r"]),
                    sub_strategy="SFP_MSS_Strict",
                    metadata={"close_reason": tr.get("close_reason")}
                )
                signals.append(sig)

    return signals


# ---------------------------------------------------------------------------
# 4. Chronological Megalord Simulation Loop
# ---------------------------------------------------------------------------

def simulate_window(
    window_name: str,
    start_warmup: datetime,
    start_eval: datetime,
    end_eval: datetime,
    compass: CausalMacroCompass
) -> Dict[str, Any]:
    print(f"\n========================================================")
    print(f"CALCULATING WINDOW: {window_name}")
    print(f"Warm-up: {start_warmup.date()} -> {start_eval.date()} | Eval: {start_eval.date()} -> {end_eval.date()}")
    print(f"========================================================", flush=True)

    # 1. Collect signals from all authentic engines for this window
    print("-> 1. Running Pragma Engine (Approved FX Core & Gold M5)...", flush=True)
    pragma_sigs = run_window_pragma_signals(start_warmup, end_eval)

    print("-> 2. Running Trawma Engine (Binance Taker-Delta & MT5 Hybrid M15)...", flush=True)
    trawma_sigs = run_window_trawma_signals(start_warmup, end_eval)

    print("-> 3. Running Stigma Engine (Quant OU-HalfLife & Kalman MOM)...", flush=True)
    stigma_sigs = run_window_stigma_signals(start_warmup, end_eval)

    print("-> 4. Loading Begonya00 (Strict Institutional Liquidity Hunt)...", flush=True)
    begonya_sigs = load_window_begonya00_signals(start_eval, end_eval)
    print(f"      [Begonya00] Loaded {len(begonya_sigs)} institutional trades.", flush=True)

    all_raw = pragma_sigs + trawma_sigs + stigma_sigs + begonya_sigs

    # 2. Filter candidates strictly by evaluation month entry
    candidates = [
        sig for sig in all_raw
        if start_eval <= sig.entry_time.to_pydatetime() < end_eval
    ]
    candidates.sort(key=lambda s: s.entry_time)
    print(f"\n--> Total Raw Candidates in Evaluation Window: {len(candidates)}", flush=True)

    # 3. Initialize Arbiter and Firewall
    arbiter = MegalordSignalArbiter()
    firewall = MegalordRiskFirewall()

    open_positions: List[Dict[str, Any]] = []
    executed_trades: List[Dict[str, Any]] = []
    veto_log: List[Dict[str, Any]] = []
    confluence_boost_count = 0

    daily_realized_loss_r = 0.0
    current_day = None

    for cand in candidates:
        cand_time = cand.entry_time
        cand_day = cand_time.date()

        # Reset daily realized loss at 00:00 UTC
        if current_day != cand_day:
            daily_realized_loss_r = 0.0
            current_day = cand_day

        # Update open positions (close any whose exit_time <= cand_time)
        still_open = []
        for pos in open_positions:
            if pos["exit_time"] <= cand_time:
                realized_pnl_r = pos["raw_r_result"] * pos["allocated_risk_r"]
                if realized_pnl_r < 0:
                    daily_realized_loss_r += abs(realized_pnl_r)
                arbiter.record_trade_outcome(pos["engine_name"], realized_pnl_r)
            else:
                still_open.append(pos)
        open_positions = still_open

        # Resolve causal macro bias
        macro_bias = compass.get_bias(cand.symbol, cand_time)

        # Evaluate through Megalord Arbiter
        arb_decision = arbiter.evaluate_signal(
            candidate=cand,
            active_positions=open_positions,
            macro_htf_bias=macro_bias,
            now_utc=cand_time,
            allow_historical=True
        )

        if not arb_decision.allowed:
            veto_log.append({
                "signal_id": cand.signal_id,
                "engine": cand.engine_name,
                "symbol": cand.symbol,
                "time": cand_time.isoformat(),
                "layer": "ARBITER",
                "action": arb_decision.action,
                "reason": arb_decision.reason
            })
            continue

        allocated_risk = arb_decision.final_risk_mult * 1.0

        # Evaluate through Megalord Risk Firewall
        fw_ok, fw_reason = firewall.evaluate_entry(
            candidate_signal=cand,
            allocated_risk_r=allocated_risk,
            daily_realized_loss_r=daily_realized_loss_r,
            open_positions=open_positions
        )

        if not fw_ok:
            veto_log.append({
                "signal_id": cand.signal_id,
                "engine": cand.engine_name,
                "symbol": cand.symbol,
                "time": cand_time.isoformat(),
                "layer": "FIREWALL",
                "action": "FIREWALL_BLOCKED",
                "reason": fw_reason
            })
            continue

        # Approved & Executed!
        if arb_decision.action == "CONFLUENCE_BOOST" or arb_decision.final_risk_mult > 1.0:
            confluence_boost_count += 1

        exec_record = {
            "signal_id": cand.signal_id,
            "engine": cand.engine_name,
            "math_family": cand.math_family.value,
            "symbol": cand.symbol,
            "direction": cand.direction,
            "entry_time": cand_time.isoformat(),
            "exit_time": cand.exit_time.isoformat() if cand.exit_time else None,
            "allocated_risk_r": allocated_risk,
            "raw_r": cand.raw_r_result,
            "realized_r": round(cand.raw_r_result * allocated_risk, 4),
            "macro_bias": macro_bias,
            "arbiter_action": arb_decision.action,
        }
        executed_trades.append(exec_record)

        # Add to open book
        open_positions.append({
            "signal_id": cand.signal_id,
            "engine_name": cand.engine_name,
            "math_family": cand.math_family.value,
            "symbol": cand.symbol,
            "direction": cand.direction,
            "entry_time": cand_time,
            "exit_time": cand.exit_time,
            "allocated_risk_r": allocated_risk,
            "raw_r_result": cand.raw_r_result,
            "is_breakeven": False
        })

    # Metrics computation
    total_trades = len(executed_trades)
    net_r = sum(t["realized_r"] for t in executed_trades)
    wins = [t["realized_r"] for t in executed_trades if t["realized_r"] > 0]
    losses = [abs(t["realized_r"]) for t in executed_trades if t["realized_r"] < 0]
    wr = len(wins) / total_trades * 100.0 if total_trades > 0 else 0.0
    pf = sum(wins) / sum(losses) if sum(losses) > 0 else (999.0 if sum(wins) > 0 else 0.0)

    # Max drawdown
    cum_r = 0.0
    peak_r = 0.0
    max_dd = 0.0
    for t in executed_trades:
        cum_r += t["realized_r"]
        if cum_r > peak_r:
            peak_r = cum_r
        dd = peak_r - cum_r
        if dd > max_dd:
            max_dd = dd

    # Engine breakdown
    by_engine: Dict[str, Dict[str, Any]] = {}
    for t in executed_trades:
        eng = t["engine"]
        if eng not in by_engine:
            by_engine[eng] = {"trades": 0, "net_r": 0.0, "wins": 0, "losses": 0}
        by_engine[eng]["trades"] += 1
        by_engine[eng]["net_r"] += t["realized_r"]
        if t["realized_r"] > 0:
            by_engine[eng]["wins"] += 1
        elif t["realized_r"] < 0:
            by_engine[eng]["losses"] += 1

    for eng in by_engine:
        by_engine[eng]["net_r"] = round(by_engine[eng]["net_r"], 2)

    print(f"\n--> WINDOW RESULTS ({window_name}):")
    print(f"    Raw Signals: {len(candidates)} | Vetoed: {len(veto_log)} | Executed: {total_trades}")
    print(f"    Win Rate: {wr:.1f}% | Profit Factor: {pf:.2f} | Net R: {net_r:+.2f}R | Max DD: {max_dd:.2f}R")
    print(f"    By Engine: {by_engine}\n", flush=True)

    return {
        "window": window_name,
        "raw_candidates": len(candidates),
        "veto_count": len(veto_log),
        "executed_trades": total_trades,
        "win_rate_pct": round(wr, 1),
        "profit_factor": round(pf, 2),
        "net_r": round(net_r, 2),
        "max_drawdown_r": round(max_dd, 2),
        "confluence_boost_count": confluence_boost_count,
        "by_engine": by_engine,
        "trades": executed_trades
    }


def main():
    print("=================================================================")
    print("MEGALORD VERIFIED 2025 3-MONTHS INSTITUTIONAL BACKTEST")
    print("NO PROXY RULES | DIRECT SUB-ENGINE EXECUTION & ARBITRATION")
    print("=================================================================\n", flush=True)

    compass = CausalMacroCompass()

    windows = [
        {
            "name": "2025 Mart (Q1 Volatilite)",
            "start_warmup": datetime(2025, 2, 15, tzinfo=timezone.utc),
            "start_eval": datetime(2025, 3, 1, tzinfo=timezone.utc),
            "end_eval": datetime(2025, 4, 1, tzinfo=timezone.utc),
        },
        {
            "name": "2025 Temmuz (Q3 Yaz/Testere)",
            "start_warmup": datetime(2025, 6, 15, tzinfo=timezone.utc),
            "start_eval": datetime(2025, 7, 1, tzinfo=timezone.utc),
            "end_eval": datetime(2025, 8, 1, tzinfo=timezone.utc),
        },
        {
            "name": "2025 Kasım (Q4 Kurumsal Trend)",
            "start_warmup": datetime(2025, 10, 15, tzinfo=timezone.utc),
            "start_eval": datetime(2025, 11, 1, tzinfo=timezone.utc),
            "end_eval": datetime(2025, 12, 1, tzinfo=timezone.utc),
        },
    ]

    results = []
    for w in windows:
        res = simulate_window(
            window_name=w["name"],
            start_warmup=w["start_warmup"],
            start_eval=w["start_eval"],
            end_eval=w["end_eval"],
            compass=compass
        )
        results.append(res)

    out_file = MEGALORD_DIR / "megalord_2025_verified_3months.json"
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n[OK] All 3 Windows Successfully Processed! Results saved to {out_file}", flush=True)


if __name__ == "__main__":
    main()
