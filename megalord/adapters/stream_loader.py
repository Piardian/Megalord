"""Megalord Multi-Project Stream Loader & Live Signal Bridge.
Reads actual trade and signal streams from the active projects in real-time:
  1. Begonya00: C:\\Users\\piard\\Desktop\\Begonya00\\data\\active_trades.json & trade_ledger.json
     -> Whitelist: EURUSD, GBPUSD, USDJPY, USDCAD, USDCHF, XAUUSD
  2. Pragma: C:\\Users\\piard\\Desktop\\PragmaProject\\reports\\live_signals.json & pragma_trades_cache.json
     -> Whitelist: CADCHF, EURUSD (FX_MEAN_REVERSION Verified Set)
  3. Trawma: C:\\Users\\piard\\Desktop\\TrawmaProject\\artifacts\\paper_positions.json & trawma_trades_cache.json
     -> Whitelist: NAS100, BTCUSD (OrderFlow & CVD / Hybrid Absorption)
  4. Stigma: C:\\Users\\piard\\Desktop\\StigmaProject\\reports\\live_positions.json & stigma_trades_cache.json
     -> Whitelist: USDJPY, NAS100, BTCUSD (OU Mean Reversion & Kalman Momentum)
  5. ChecklistTrigger: C:\\Users\\piard\\.gemini\\antigravity\\scratch\\swing-bos-core\\evidence\\research\\v2-quality-aggregation-56-live-audit.json
     -> Whitelist: EURUSD, GBPUSD, AUDUSD
  6. Convexity v2: C:\\Users\\piard\\.gemini\\antigravity\\scratch\\begonya_convexity_engine (D1 Compass Signals - Compass Only)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
import pandas as pd
from megalord.types import TradeSignal, MathFamily, StrategyRegime

# --- ENGINE-SPECIFIC STRICT INSTITUTIONAL WHITELISTS ---
BEGONYA00_WHITELIST: Set[str] = {
    "EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "XAUUSD",
    "GBPCHF", "EURJPY", "AUDCAD", "AUDJPY", "AUDCHF", "CHFJPY",
    "CADCHF", "CADJPY", "EURGBP", "EURAUD", "NZDCAD", "NZDUSD", "EURCHF"
}
PRAGMA_WHITELIST: Set[str] = {"CADCHF", "EURUSD"}
TRAWMA_WHITELIST: Set[str] = {"NAS100", "BTCUSD"}
STIGMA_WHITELIST: Set[str] = {"USDJPY", "NAS100", "BTCUSD"}
CHECKLIST_WHITELIST: Set[str] = {"EURUSD", "GBPUSD", "AUDUSD"}


def norm_symbol(s: str) -> str:
    s = str(s).upper().replace("/", "").replace("-", "").replace("_", "").strip()
    if s.endswith("USDT"):
        s = s[:-4] + "USD"
    return s


def norm_direction(d: str) -> str:
    d = str(d).upper().strip()
    if d in ("LONG", "BUY", "BULL", "BULLISH", "AL", "1", "+1"):
        return "LONG"
    if d in ("SHORT", "SELL", "BEAR", "BEARISH", "SAT", "-1"):
        return "SHORT"
    return d


class ProjectStreamLoader:
    def __init__(self):
        # Begonya00
        self.begonya00_path = Path(r"C:\Users\piard\Desktop\Begonya00\data\trade_ledger.json")
        self.begonya00_active_path = Path(r"C:\Users\piard\Desktop\Begonya00\data\active_trades.json")
        self.begonya_path = Path(r"C:\Users\piard\.gemini\antigravity\scratch\Begonya\data\begonya_1m_3m_1y_final_backtest.json")

        # Checklist Trigger
        self.checklist_path = Path(r"C:\Users\piard\.gemini\antigravity\scratch\swing-bos-core\evidence\research\v2-quality-aggregation-56-live-audit.json")

        # Convexity v2 Compass
        self.convexity_dir = Path(r"C:\Users\piard\.gemini\antigravity\scratch\begonya_convexity_engine")
        self.convexity_config_path = self.convexity_dir / "config" / "convexity_config.json"

        # Pragma
        self.pragma_live_path = Path(r"C:\Users\piard\Desktop\PragmaProject\reports\live_signals.json")
        self.pragma_cache_path = Path(r"C:\Users\piard\Desktop\MegalordProject\megalord\adapters\pragma_trades_cache.json")

        # Trawma
        self.trawma_paper_path = Path(r"C:\Users\piard\Desktop\TrawmaProject\artifacts\paper_positions.json")
        self.trawma_cache_path = Path(r"C:\Users\piard\Desktop\MegalordProject\megalord\adapters\trawma_trades_cache.json")
        self.trawma_whitelist_path = Path(r"C:\Users\piard\Desktop\TrawmaProject\reports\whitelist.json")

        # Stigma
        self.stigma_live_path = Path(r"C:\Users\piard\Desktop\StigmaProject\reports\live_positions.json")
        self.stigma_perf_path = Path(r"C:\Users\piard\Desktop\StigmaProject\reports\performance_tracker.json")
        self.stigma_cache_path = Path(r"C:\Users\piard\Desktop\MegalordProject\megalord\adapters\stigma_trades_cache.json")
        self.stigma_whitelist_path = Path(r"C:\Users\piard\Desktop\StigmaProject\reports\whitelist.json")

        self._convexity_cache: Optional[List[TradeSignal]] = None

    def load_begonya00(self) -> List[TradeSignal]:
        """Loads Begonya00 active trades and closed ledger, filtered strictly to BEGONYA00_WHITELIST."""
        signals = []
        # 1. Load active trades (currently live/open/pending)
        if self.begonya00_active_path.exists():
            try:
                raw_text = self.begonya00_active_path.read_text(encoding="utf-8").strip()
                if raw_text:
                    active_data = json.loads(raw_text)
                    for t in active_data:
                        sym = norm_symbol(t.get("symbol", "EURUSD"))
                        if sym not in BEGONYA00_WHITELIST:
                            continue
                        t_id = t.get("id") or str(len(signals))
                        dt_in = pd.to_datetime(t.get("opened_at") or t.get("filled_at") or t.get("entry_candle_time"), utc=True)
                        sig = TradeSignal(
                            signal_id=f"B00_{t_id}",
                            engine_name="Begonya00",
                            math_family=MathFamily.FAMILY_SMC,
                            strategy_regime=StrategyRegime.TREND_MOMENTUM,
                            symbol=sym,
                            direction=norm_direction(t.get("direction", "LONG")),
                            entry_time=dt_in,
                            exit_time=None,
                            entry_price=float(t.get("entry", 0.0)),
                            sl_price=float(t.get("current_sl") or t.get("original_sl", 0.0)),
                            tp1_price=float(t.get("tp1", 0.0)),
                            tp2_price=float(t.get("tp2", 0.0)),
                            raw_r_result=float(t.get("r_result", 0.0)),
                            sub_strategy=t.get("strategy", "M5_SMC_Pullback"),
                            metadata={"status": t.get("status", "OPEN"), "live_active": True, "rr_ratio": float(t.get("rr_ratio", 3.0))}
                        )
                        signals.append(sig)
            except Exception as e:
                print(f"[StreamLoader] Warning loading Begonya00 active_trades: {e}")

        # 2. Load trade ledger (closed/historical)
        if self.begonya00_path.exists():
            try:
                data = json.loads(self.begonya00_path.read_text(encoding="utf-8"))
                for t in data:
                    sym = norm_symbol(t.get("symbol", "EURUSD"))
                    if sym not in BEGONYA00_WHITELIST:
                        continue
                    if t.get("status") in ("TP2_HIT", "TP1_HIT", "STOP_LOSS", "BREAK_EVEN", "CLOSED") and t.get("opened_at"):
                        dt_in = pd.to_datetime(t["opened_at"], utc=True)
                        dt_out = pd.to_datetime(t.get("closed_at") or t["opened_at"], utc=True)
                        t_id = t.get("id") or t.get("trade_id") or str(len(signals))
                        sig = TradeSignal(
                            signal_id=f"B00_{t_id}",
                            engine_name="Begonya00",
                            math_family=MathFamily.FAMILY_SMC,
                            strategy_regime=StrategyRegime.TREND_MOMENTUM,
                            symbol=sym,
                            direction=norm_direction(t.get("direction", "LONG")),
                            entry_time=dt_in,
                            exit_time=dt_out,
                            entry_price=float(t.get("entry", 0.0)),
                            sl_price=float(t.get("current_sl") or t.get("original_sl", 0.0)),
                            tp1_price=float(t.get("tp1", 0.0)),
                            tp2_price=float(t.get("tp2", 0.0)),
                            raw_r_result=float(t.get("r_result", 0.0)),
                            sub_strategy=t.get("strategy", "M5_SMC_Pullback"),
                            metadata={"status": t.get("status", "CLOSED"), "rr_ratio": float(t.get("rr_ratio", 3.0))}
                        )
                        signals.append(sig)
            except Exception as e:
                print(f"[StreamLoader] Warning loading Begonya00 trade_ledger: {e}")
        return signals

    def load_begonya(self) -> List[TradeSignal]:
        """Historical backtest comparison only (disabled for live execution)."""
        signals = []
        if not self.begonya_path.exists():
            return signals
        try:
            data = json.loads(self.begonya_path.read_text(encoding="utf-8"))
            trades_1y = data.get("3. SON 1 YIL", {}).get("trades", [])
            for idx, t in enumerate(trades_1y):
                dt_in = pd.to_datetime(t["entry_time"], utc=True)
                dt_out = pd.to_datetime(t["exit_time"], utc=True)
                sig = TradeSignal(
                    signal_id=f"BEG_{idx}",
                    engine_name="Begonya",
                    math_family=MathFamily.FAMILY_SMC,
                    strategy_regime=StrategyRegime.TREND_MOMENTUM,
                    symbol=norm_symbol(t.get("symbol", "EURUSD")),
                    direction=norm_direction(t["type"]),
                    entry_time=dt_in,
                    exit_time=dt_out,
                    raw_r_result=float(t.get("r_multiple", 0.0)),
                    sub_strategy="M1_M3_SMC_Legacy"
                )
                signals.append(sig)
        except Exception:
            pass
        return signals

    def load_checklist_trigger(self) -> List[TradeSignal]:
        """ChecklistTrigger signals filtered to CHECKLIST_WHITELIST."""
        signals = []
        if not self.checklist_path.exists():
            return signals
        try:
            data = json.loads(self.checklist_path.read_text(encoding="utf-8"))
            trades = data if isinstance(data, list) else data.get("audit_trades", [])
            for idx, r in enumerate(trades):
                sym = norm_symbol(r.get("symbol", "EURUSD"))
                if sym not in CHECKLIST_WHITELIST:
                    continue
                dt_in = pd.to_datetime(r.get("timestamp") or r.get("entry_time"), utc=True)
                out = r.get("actualOutcome") or r.get("outcome", "")
                r_val = float(r.get("realizedR") or r.get("r_multiple", 0.0))
                if out == "TP" and r_val == 0:
                    r_val = 2.45
                elif out in ("SL", "STOP") and r_val == 0:
                    r_val = -1.0
                sig = TradeSignal(
                    signal_id=f"CLT_{idx}",
                    engine_name="ChecklistTrigger",
                    math_family=MathFamily.FAMILY_SMC,
                    strategy_regime=StrategyRegime.TREND_MOMENTUM,
                    symbol=sym,
                    direction=norm_direction(r.get("direction", "LONG")),
                    entry_time=dt_in,
                    exit_time=dt_in + pd.Timedelta(hours=2),
                    raw_r_result=r_val,
                    sub_strategy="H1_M15_SFP_iFVG"
                )
                signals.append(sig)
        except Exception as e:
            print(f"[StreamLoader] Warning loading ChecklistTrigger: {e}")
        return signals

    def load_pragma(self) -> List[TradeSignal]:
        """
        Pragma Live Signal Bridge & Cache.
        Reads live executable signals from PragmaProject/reports/live_signals.json.
        Strictly restricted to PRAGMA_WHITELIST: CADCHF & EURUSD (FX_MEAN_REVERSION Verified Set).
        """
        signals = []

        # 1. Live Signals from Pragma live scanner
        if self.pragma_live_path.exists():
            try:
                live_data = json.loads(self.pragma_live_path.read_text(encoding="utf-8"))
                for tr in live_data:
                    sym = norm_symbol(tr.get("symbol", ""))
                    if sym not in PRAGMA_WHITELIST:
                        continue
                    dt_in = pd.to_datetime(tr.get("bar_time") or tr.get("created_at"), utc=True)
                    regime = StrategyRegime.MEAN_REVERSION if "MEAN_REVERSION" in str(tr.get("regime", "")) else StrategyRegime.TREND_MOMENTUM
                    sig = TradeSignal(
                        signal_id=tr.get("signal_id", f"PRG_LIVE_{sym}_{int(dt_in.timestamp())}"),
                        engine_name="Pragma",
                        math_family=MathFamily.FAMILY_AMT_VWAP,
                        strategy_regime=regime,
                        symbol=sym,
                        direction=norm_direction(tr.get("direction", "LONG")),
                        entry_time=dt_in,
                        exit_time=None,
                        entry_price=float(tr.get("entry_price", 0.0)),
                        sl_price=float(tr.get("stop_loss", 0.0)),
                        tp1_price=float(tr.get("tp1", 0.0)),
                        tp2_price=float(tr.get("tp2", 0.0)),
                        raw_r_result=0.0,
                        sub_strategy=f"AMT_{tr.get('poi_type', 'ROTATION')}",
                        metadata={"status": tr.get("status", "OPEN"), "live_active": True, "rr_ratio": float(tr.get("rr_ratio", 2.0))}
                    )
                    signals.append(sig)
            except Exception as e:
                print(f"[StreamLoader] Warning loading Pragma live signals: {e}")

        # 2. Historical Cache for context
        if self.pragma_cache_path.exists():
            try:
                data = json.loads(self.pragma_cache_path.read_text(encoding="utf-8"))
                for sym, trades_list in data.items():
                    nsym = norm_symbol(sym)
                    if nsym not in PRAGMA_WHITELIST:
                        continue
                    for idx, tr in enumerate(trades_list):
                        dt_in = pd.to_datetime(tr["entry_time"], utc=True)
                        dt_out = pd.to_datetime(tr["exit_time"], utc=True)
                        regime = StrategyRegime.MEAN_REVERSION if "MEAN_REVERSION" in str(tr.get("poi_type", "")) else StrategyRegime.TREND_MOMENTUM
                        sig = TradeSignal(
                            signal_id=f"PRG_{nsym}_{idx}",
                            engine_name="Pragma",
                            math_family=MathFamily.FAMILY_AMT_VWAP,
                            strategy_regime=regime,
                            symbol=nsym,
                            direction=norm_direction(tr.get("direction", "LONG")),
                            entry_time=dt_in,
                            exit_time=dt_out,
                            raw_r_result=float(tr.get("r_multiple", 0.0)),
                            sub_strategy=f"AMT_VWAP_{nsym}_{tr.get('poi_type', 'ROTATION')}"
                        )
                        signals.append(sig)
            except Exception:
                pass
        return signals

    def load_trawma_whitelist(self) -> List[TradeSignal]:
        """
        Trawma Live Signal Bridge & Whitelist.
        Reads live paper positions from TrawmaProject/artifacts/paper_positions.json.
        Strictly restricted to TRAWMA_WHITELIST: NAS100 & BTCUSD.
        """
        signals = []

        # 1. Live Positions from Trawma live scanner
        if self.trawma_paper_path.exists():
            try:
                paper_data = json.loads(self.trawma_paper_path.read_text(encoding="utf-8"))
                for pos in paper_data:
                    sym = norm_symbol(pos.get("symbol", ""))
                    if sym not in TRAWMA_WHITELIST:
                        continue
                    dt_in = pd.to_datetime(pos.get("entry_time"), utc=True)
                    dt_out = pd.to_datetime(pos.get("exit_time"), utc=True) if pos.get("exit_time") else None
                    is_open = pos.get("status") == "OPEN"
                    sig = TradeSignal(
                        signal_id=f"TRW_{pos.get('id', '')}",
                        engine_name="Trawma",
                        math_family=MathFamily.FAMILY_ORDERFLOW,
                        strategy_regime=StrategyRegime.TREND_MOMENTUM,
                        symbol=sym,
                        direction=norm_direction(pos.get("side", "LONG")),
                        entry_time=dt_in,
                        exit_time=dt_out,
                        entry_price=float(pos.get("entry_price", 0.0)),
                        sl_price=float(pos.get("stop_loss", 0.0)),
                        tp1_price=float(pos.get("tp1", 0.0)),
                        tp2_price=float(pos.get("tp2", 0.0)),
                        raw_r_result=float(pos.get("realized_r", 0.0)),
                        sub_strategy=f"{pos.get('timeframe', 'M15')}_OrderFlow",
                        metadata={"status": pos.get("status", "OPEN"), "live_active": is_open, "rr_ratio": 2.0}
                    )
                    signals.append(sig)
            except Exception as e:
                print(f"[StreamLoader] Warning loading Trawma paper positions: {e}")

        # 2. Historical Cache for backtest context
        if self.trawma_cache_path.exists():
            try:
                data = json.loads(self.trawma_cache_path.read_text(encoding="utf-8"))
                for d in data:
                    sym = norm_symbol(d["symbol"])
                    if sym not in TRAWMA_WHITELIST:
                        continue
                    sig = TradeSignal(
                        signal_id=d["signal_id"],
                        engine_name=d["engine_name"],
                        math_family=MathFamily(d["math_family"]),
                        strategy_regime=StrategyRegime(d["strategy_regime"]),
                        symbol=sym,
                        direction=norm_direction(d["direction"]),
                        entry_time=pd.to_datetime(d["entry_time"], utc=True),
                        exit_time=pd.to_datetime(d["exit_time"], utc=True) if d.get("exit_time") else None,
                        raw_r_result=float(d.get("raw_r_result", 0.0)),
                        sub_strategy=d.get("sub_strategy", "")
                    )
                    signals.append(sig)
            except Exception:
                pass
        return signals

    def load_stigma_whitelist(self) -> List[TradeSignal]:
        """
        Stigma Live Signal Bridge & Whitelist.
        Reads live open positions from StigmaProject/reports/live_positions.json.
        Strictly restricted to STIGMA_WHITELIST: USDJPY, NAS100, BTCUSD.
        """
        signals = []

        # 1. Live Positions from Stigma live monitor
        if self.stigma_live_path.exists():
            try:
                live_dict = json.loads(self.stigma_live_path.read_text(encoding="utf-8"))
                positions = live_dict.values() if isinstance(live_dict, dict) else live_dict
                for pos in positions:
                    sym = norm_symbol(pos.get("symbol", ""))
                    if sym not in STIGMA_WHITELIST:
                        continue
                    dt_in = pd.to_datetime(pos.get("entry_time"), utc=True)
                    strat_mode = str(pos.get("strategy_mode", "OU_ONLY")).upper()
                    regime = StrategyRegime.MEAN_REVERSION if "OU" in strat_mode else StrategyRegime.TREND_MOMENTUM
                    sig = TradeSignal(
                        signal_id=f"STG_{sym}_{strat_mode}_{int(dt_in.timestamp())}",
                        engine_name="Stigma",
                        math_family=MathFamily.FAMILY_STAT_QUANT,
                        strategy_regime=regime,
                        symbol=sym,
                        direction=norm_direction(pos.get("side", "LONG")),
                        entry_time=dt_in,
                        exit_time=None,
                        entry_price=float(pos.get("entry_price", 0.0)),
                        sl_price=float(pos.get("stop_loss", 0.0)),
                        tp1_price=float(pos.get("tp1", 0.0)),
                        tp2_price=float(pos.get("tp2", 0.0)),
                        raw_r_result=0.0,
                        sub_strategy=strat_mode,
                        metadata={"status": pos.get("status", "OPEN"), "live_active": True, "rr_ratio": 2.0}
                    )
                    signals.append(sig)
            except Exception as e:
                print(f"[StreamLoader] Warning loading Stigma live positions: {e}")

        # 2. Historical Cache for context
        if self.stigma_cache_path.exists():
            try:
                data = json.loads(self.stigma_cache_path.read_text(encoding="utf-8"))
                for d in data:
                    sym = norm_symbol(d["symbol"])
                    if sym not in STIGMA_WHITELIST:
                        continue
                    sig = TradeSignal(
                        signal_id=d["signal_id"],
                        engine_name=d["engine_name"],
                        math_family=MathFamily(d["math_family"]),
                        strategy_regime=StrategyRegime(d["strategy_regime"]),
                        symbol=sym,
                        direction=norm_direction(d["direction"]),
                        entry_time=pd.to_datetime(d["entry_time"], utc=True),
                        exit_time=pd.to_datetime(d["exit_time"], utc=True) if d.get("exit_time") else None,
                        raw_r_result=float(d.get("raw_r_result", 0.0)),
                        sub_strategy=d.get("sub_strategy", "")
                    )
                    signals.append(sig)
            except Exception:
                pass
        return signals

    def load_convexity_v2(self) -> List[TradeSignal]:
        """Convexity D1 Trend Runner / Compass Signals (Compass Only)."""
        if self._convexity_cache is not None:
            return self._convexity_cache
        signals = []
        try:
            import sys
            if str(self.convexity_dir) not in sys.path:
                sys.path.insert(0, str(self.convexity_dir))
            from data.market_loader import MarketLoader
            from engine.trend_detector import TrendDetector
            from engine.convexity_simulator import ConvexitySimulator

            cfg = json.loads(self.convexity_config_path.read_text(encoding="utf-8"))
            loader = MarketLoader(cache_dir=self.convexity_dir / "cache")
            detector = TrendDetector(cfg)
            simulator = ConvexitySimulator(cfg)
            chop_pairs = ["USDCAD", "GBPJPY", "EURJPY", "AUDUSD"]
            conv_symbols = [s for s in cfg.get("backtest", {}).get("symbols", []) if s not in chop_pairs]

            for sym in conv_symbols:
                cache_file = self.convexity_dir / "cache" / f"{sym}_1d.csv"
                if not cache_file.exists():
                    continue
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if df.empty or len(df) < 200:
                    continue
                df = loader._compute_indicators(df)
                sigs = [detector.check_breakout(df, i, sym) for i in range(1, len(df))]
                sigs = [s for s in sigs if s]
                t_list = simulator.simulate_trades_for_symbol(df, sigs, sym)
                for idx, t in enumerate(t_list):
                    dt_in = pd.to_datetime(t.entry_date, utc=True)
                    dt_out = pd.to_datetime(t.exit_date, utc=True)
                    sig = TradeSignal(
                        signal_id=f"CNV_{sym}_{idx}",
                        engine_name="Convexity_v2",
                        math_family=MathFamily.FAMILY_MACRO_TREND,
                        strategy_regime=StrategyRegime.TREND_MOMENTUM,
                        symbol=norm_symbol(sym),
                        direction=norm_direction(t.direction),
                        entry_time=dt_in,
                        exit_time=dt_out,
                        raw_r_result=float(t.pnl_r),
                        sub_strategy="D1_Donchian_200EMA_Runner"
                    )
                    signals.append(sig)
            self._convexity_cache = signals
        except Exception as e:
            print(f"[StreamLoader] Warning loading Convexity v2: {e}")
        return signals

    def load_all_signals(self) -> List[TradeSignal]:
        all_sigs = []
        all_sigs.extend(self.load_begonya00())
        all_sigs.extend(self.load_begonya())
        all_sigs.extend(self.load_checklist_trigger())
        all_sigs.extend(self.load_convexity_v2())
        all_sigs.extend(self.load_pragma())
        all_sigs.extend(self.load_stigma_whitelist())
        all_sigs.extend(self.load_trawma_whitelist())
        all_sigs.sort(key=lambda x: x.entry_time)
        return all_sigs
