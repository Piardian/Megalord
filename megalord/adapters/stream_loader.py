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
import math
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
import pandas as pd
from megalord.types import TradeSignal, MathFamily, StrategyRegime

# --- ENGINE-SPECIFIC STRICT INSTITUTIONAL WHITELISTS ---
# None = Bütün pariteler serbest (Whitelists disabled, all symbols allowed)
WEEKEND_CRYPTO_WHITELIST: Set[str] = {
    "XRPUSD", "BTCUSD", "ETHUSD", "LTCUSD", "SOLUSD", "DOGUSD", "BTCEUR", "ETHEUR"
}

BEGONYA00_WHITELIST: Optional[Set[str]] = None
PRAGMA_WHITELIST: Optional[Set[str]] = None
TRAWMA_WHITELIST: Optional[Set[str]] = None
STIGMA_WHITELIST: Optional[Set[str]] = None
CHECKLIST_WHITELIST: Optional[Set[str]] = None


def norm_symbol(s: str) -> str:
    s = str(s).upper().replace("/", "").replace("-", "").replace("_", "").strip()
    if s.endswith("USDT"):
        s = s[:-4] + "USD"
    if s == "DOGEUSD":
        s = "DOGUSD"
    return s


def norm_direction(d: str) -> str:
    d = str(d).upper().strip()
    if d in ("LONG", "BUY", "BULL", "BULLISH", "AL", "1", "+1"):
        return "LONG"
    if d in ("SHORT", "SELL", "BEAR", "BEARISH", "SAT", "-1"):
        return "SHORT"
    return d


def _parse_signal_time(ts_val: Any, assume_local_if_naive: bool = False) -> pd.Timestamp:
    """Parses signal timestamps safely, converting naive local timestamps (e.g. UTC+3) to UTC."""
    if ts_val is None or ts_val == "":
        return pd.Timestamp.now(tz="UTC")
    ts = pd.Timestamp(ts_val)
    if ts.tzinfo is not None:
        return ts.tz_convert("UTC")
    if assume_local_if_naive:
        from datetime import datetime
        local_tz = datetime.now().astimezone().tzinfo
        return ts.tz_localize(local_tz).tz_convert("UTC")
    return ts.tz_localize("UTC")


class ProjectStreamLoader:
    def __init__(self):
        # Begonya00
        self.begonya00_path = Path(r"C:\Users\piard\Desktop\Begonya00\data\trade_ledger.json")
        self.begonya00_active_path = Path(r"C:\Users\piard\Desktop\Begonya00\data\active_trades.json")
        self.begonya_path = Path(r"C:\Users\piard\.gemini\antigravity\scratch\Begonya\data\begonya_1m_3m_1y_final_backtest.json")

        # Checklist Trigger
        self.checklist_path = Path(r"C:\Users\piard\.gemini\antigravity\scratch\swing-bos-core\evidence\research\v2-quality-aggregation-56-live-audit.json")
        self.checklist_live_path = self.checklist_path.parents[1] / "megalord_entry_intents.jsonl"
        self.checklist_queue_path = Path(r"C:\Users\piard\.gemini\antigravity\scratch\swing-bos-core\data\signal_delivery_queue.json")

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
        """Decoupled: Begonya00 operates 100% independently from Megalord."""
        signals = []
        # 1. Load active trades (currently live/open/pending)
        if self.begonya00_active_path.exists():
            try:
                raw_text = self.begonya00_active_path.read_text(encoding="utf-8").strip()
                if raw_text:
                    active_data = json.loads(raw_text)
                    for t in active_data:
                        sym = norm_symbol(t.get("symbol", "EURUSD"))
                        if BEGONYA00_WHITELIST is not None and sym not in BEGONYA00_WHITELIST:
                            continue
                        t_id = t.get("id") or str(len(signals))
                        raw_ts = t.get("filled_at") or t.get("opened_at") or t.get("entry_candle_time")
                        is_local_iso = bool(t.get("filled_at") or t.get("opened_at"))
                        dt_in = _parse_signal_time(raw_ts, assume_local_if_naive=is_local_iso)
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
                            metadata={
                                "status": t.get("status", "OPEN"),
                                "grade": str(t.get("grade", "A+")),
                                "pool_type": str(t.get("pool_type", "")),
                                "live_active": True,
                                "rr_ratio": float(t.get("rr_ratio", 3.0)),
                            }
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
                    if BEGONYA00_WHITELIST is not None and sym not in BEGONYA00_WHITELIST:
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

    def load_begonya(self, source_timezone: Optional[str] = "UTC") -> List[TradeSignal]:
        """Historical archive only. Its producer formats UTC bars without an offset.

        run_begonya_multi_period_backtest.py constructs bar datetimes with
        utc=True, then uses strftime for opened_at/closed_at. Pass None for an
        archive whose source timezone has not been established.
        """
        signals = []
        if not self.begonya_path.exists():
            return signals
        try:
            data = json.loads(self.begonya_path.read_text(encoding="utf-8"))
            trades_1y = data.get("3. SON 1 YIL", {}).get("trades", [])
            quarantined = 0
            for idx, t in enumerate(trades_1y):
                try:
                    entry_value = t.get("entry_time") or t.get("opened_at")
                    exit_value = t.get("exit_time") or t.get("closed_at")
                    result_value = t.get("r_multiple") if t.get("r_multiple") is not None else t.get("r_result")
                    if entry_value is None or exit_value is None or result_value is None:
                        raise ValueError("Missing historical evidence")
                    dt_in, dt_out = pd.Timestamp(entry_value), pd.Timestamp(exit_value)
                    if pd.isna(dt_in) or pd.isna(dt_out):
                        raise ValueError("Invalid historical time")
                    if dt_in.tzinfo is None:
                        if source_timezone is None:
                            raise ValueError("Source timezone unresolved")
                        dt_in = dt_in.tz_localize(source_timezone)
                    if dt_out.tzinfo is None:
                        if source_timezone is None:
                            raise ValueError("Source timezone unresolved")
                        dt_out = dt_out.tz_localize(source_timezone)
                    dt_in, dt_out = dt_in.tz_convert("UTC"), dt_out.tz_convert("UTC")
                    r_value = float(result_value)
                    direction = norm_direction(t.get("type") or t.get("direction") or "")
                    if dt_out <= dt_in or not math.isfinite(r_value) or direction not in ("LONG", "SHORT"):
                        raise ValueError("Invalid historical outcome")
                except (ValueError, TypeError, OverflowError):
                    quarantined += 1
                    continue
                sig = TradeSignal(
                    signal_id=f"BEG_{idx}",
                    engine_name="Begonya",
                    math_family=MathFamily.FAMILY_SMC,
                    strategy_regime=StrategyRegime.TREND_MOMENTUM,
                    symbol=norm_symbol(t.get("symbol", "EURUSD")),
                    direction=direction,
                    entry_time=dt_in,
                    exit_time=dt_out,
                    raw_r_result=r_value,
                    sub_strategy="M1_M3_SMC_Legacy",
                    metadata={"evidence_kind": "historical_archive", "cost_coverage": "unverified",
                              "declared_source_timezone": source_timezone,
                              "entry_time_basis": "archive_order_creation_not_verified_fill",
                              "exit_time_basis": "archive_bar_label_not_verified_availability"},
                )
                signals.append(sig)
            if quarantined:
                print(f"[StreamLoader] Begonya: quarantined {quarantined} incomplete/ambiguous historical records")
        except Exception as e:
            print(f"[StreamLoader] Warning loading Begonya: {e}")
        return signals

    def load_checklist_trigger(self) -> List[TradeSignal]:
        """Native pending intents plus closed evidence; never fabricate outcomes."""
        signals = self.load_checklist_live_entries()
        if not self.checklist_path.exists():
            return signals
        try:
            data = json.loads(self.checklist_path.read_text(encoding="utf-8"))
            trades = data if isinstance(data, list) else data.get("audit_trades", [])
            for idx, r in enumerate(trades):
                sym = norm_symbol(r.get("symbol", "EURUSD"))
                if CHECKLIST_WHITELIST is not None and sym not in CHECKLIST_WHITELIST:
                    continue
                # This audit source documents numeric timestamps as epoch ms.
                # Unknown exits/outcomes are not executable entry signals.
                entry_value = r.get("entry_time") if r.get("entry_time") is not None else r.get("timestamp")
                exit_value = r.get("exit_time") if r.get("exit_time") is not None else r.get("exitTimestamp")
                result_value = r.get("realizedR") if r.get("realizedR") is not None else r.get("r_multiple")
                if entry_value is None or exit_value is None or result_value is None:
                    continue
                try:
                    dt_in = pd.to_datetime(entry_value, unit="ms", utc=True) if isinstance(entry_value, (int, float)) else pd.Timestamp(entry_value)
                    dt_out = pd.to_datetime(exit_value, unit="ms", utc=True) if isinstance(exit_value, (int, float)) else pd.Timestamp(exit_value)
                    r_val = float(result_value)
                    if pd.isna(dt_in) or pd.isna(dt_out) or dt_in.tzinfo is None or dt_out.tzinfo is None:
                        continue
                    if dt_out <= dt_in or not math.isfinite(r_val):
                        continue
                    dt_in, dt_out = dt_in.tz_convert("UTC"), dt_out.tz_convert("UTC")
                except (ValueError, TypeError, OverflowError):
                    continue
                sig = TradeSignal(
                    signal_id=f"CLT_{idx}",
                    engine_name="ChecklistTrigger",
                    math_family=MathFamily.FAMILY_SMC,
                    strategy_regime=StrategyRegime.TREND_MOMENTUM,
                    symbol=sym,
                    direction=norm_direction(r.get("direction", "LONG")),
                    entry_time=dt_in,
                    exit_time=dt_out,
                    raw_r_result=r_val,
                    sub_strategy="H1_M15_SFP_iFVG",
                    metadata={"evidence_kind": "historical_audit", "cost_coverage": "unverified"},
                )
                signals.append(sig)
        except Exception as e:
            print(f"[StreamLoader] Warning loading ChecklistTrigger: {e}")
        return signals

    def load_checklist_live_entries(self) -> List[TradeSignal]:
        """Native admitted pending intents, reading live queue and intents."""
        signals, seen = [], set()
        
        # 1. Read live signal_delivery_queue.json from active swing-bos-core server
        if getattr(self, "checklist_queue_path", None) and self.checklist_queue_path.exists():
            try:
                q_data = json.loads(self.checklist_queue_path.read_text(encoding="utf-8"))
                for item in q_data:
                    cand = item.get("candidate", {})
                    poi = cand.get("poi", {})
                    sym = norm_symbol(item.get("symbol", ""))
                    tradeDir = norm_direction(cand.get("tradeDirection", "LONG"))
                    z_low = float(poi.get("gapLow") or poi.get("zoneLow") or 0.0)
                    z_high = float(poi.get("gapHigh") or poi.get("zoneHigh") or 0.0)
                    if z_low <= 0 or z_high <= 0 or z_low >= z_high:
                        continue
                    entry = (z_low + z_high) / 2.0
                    risk_dist = abs(z_high - z_low)
                    sl = (z_low - risk_dist) if tradeDir == "LONG" else (z_high + risk_dist)
                    tp1 = (entry + risk_dist) if tradeDir == "LONG" else (entry - risk_dist)
                    tp2 = (entry + 2.0 * risk_dist) if tradeDir == "LONG" else (entry - 2.0 * risk_dist)
                    created_at = item.get("signalCreatedAt") or cand.get("marketDataTimestamp")
                    if not created_at:
                        continue
                    dt_in = pd.to_datetime(created_at, utc=True)
                    sid = item.get("signalId") or f"CLT_Q_{sym}_{int(dt_in.timestamp())}"
                    if sid in seen:
                        continue
                    sig = TradeSignal(
                        signal_id=sid,
                        engine_name="ChecklistTrigger",
                        math_family=MathFamily.FAMILY_SMC,
                        strategy_regime=StrategyRegime.TREND_MOMENTUM,
                        symbol=sym,
                        direction=tradeDir,
                        entry_time=dt_in,
                        exit_time=None,
                        entry_price=entry,
                        sl_price=sl,
                        tp1_price=tp1,
                        tp2_price=tp2,
                        sub_strategy=str(cand.get("poiType", "SMC_FVG")),
                        metadata={
                            "status": "OPEN",
                            "live_active": True,
                            "grade": str(cand.get("gradeResult", {}).get("grade", "A+")),
                            "rr_ratio": 2.0,
                        }
                    )
                    signals.append(sig)
                    seen.add(sid)
            except Exception as e:
                print(f"[StreamLoader] Warning reading Checklist queue: {e}")

        if not self.checklist_live_path.exists():
            return signals
        # First valid origin wins: a repeated publication must not refresh an
        # expired pending order. A malformed append cannot consume other IDs.
        for line in self.checklist_live_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if r.get("schema_version") != 1 or r.get("source_kind") != "NATIVE_ENTRY_INTENT":
                    continue
                if any(r.get(k) is not None for k in ("exit_time", "realizedR", "r_multiple", "raw_r_result")):
                    continue
                sid, sym = str(r["signal_id"]), norm_symbol(r["symbol"])
                if sid in seen or (CHECKLIST_WHITELIST is not None and sym not in CHECKLIST_WHITELIST) or r.get("status") != "PENDING_LIMIT":
                    continue
                origin, available = pd.Timestamp(r["entry_time"]), pd.Timestamp(r["available_at"])
                if origin.tzinfo is None or available.tzinfo is None or available != origin:
                    continue
                entry, stop = float(r["entry_price"]), float(r["sl_price"])
                direction = norm_direction(r["direction"])
                sign = 1 if direction == "LONG" else -1 if direction == "SHORT" else 0
                if not sign or not all(math.isfinite(v) and v > 0 for v in (entry, stop)) or sign * (entry-stop) <= 0:
                    continue
                target = float(r["tp2_price"])
                if not math.isfinite(target) or sign * (target-entry) <= 0:
                    continue
                signals.append(TradeSignal(signal_id=sid, engine_name="ChecklistTrigger",
                    math_family=MathFamily.FAMILY_SMC, strategy_regime=StrategyRegime.TREND_MOMENTUM,
                    symbol=sym, direction=direction, entry_time=origin.tz_convert("UTC"), exit_time=None,
                    entry_price=entry, sl_price=stop, tp1_price=float(r.get("tp1_price", 0.0)),
                    tp2_price=target, sub_strategy=str(r.get("poi_type", "SMC")),
                    metadata={"status": "PENDING_LIMIT", "live_active": True, "grade": str(r.get("grade", "")),
                              "entry_time_basis": "native_observed_intent", "available_at": available.isoformat()}))
                seen.add(sid)
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return signals[-1000:]

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
                    if PRAGMA_WHITELIST is not None and sym not in PRAGMA_WHITELIST:
                        continue
                    source_time = tr.get("bar_time") or tr.get("created_at")
                    if not source_time:
                        continue
                    dt_in = pd.to_datetime(source_time, utc=True)
                    if pd.isna(dt_in):
                        continue
                    if tr.get("bar_time"):
                        # Native notifier supplies the last closed M1 OPEN
                        # label, independent of its M5/M15 trigger timeframe.
                        dt_in += pd.Timedelta(minutes=1)
                    is_mr = any("MEAN_REVERSION" in str(tr.get(k, "")) for k in ("poi_type", "regime"))
                    regime = StrategyRegime.MEAN_REVERSION if is_mr else StrategyRegime.TREND_MOMENTUM
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
                        metadata={"status": tr.get("status", "OPEN"), "live_active": True, "rr_ratio": float(tr.get("rr_ratio", 2.0)),
                                  "entry_time_basis": "closed_m1_signal" if tr.get("bar_time") else "native_created_at_legacy"}
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
                    if PRAGMA_WHITELIST is not None and nsym not in PRAGMA_WHITELIST:
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
                    if TRAWMA_WHITELIST is not None and sym not in TRAWMA_WHITELIST:
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
                        sl_price=float(pos.get("initial_stop") or pos.get("stop_loss", 0.0)),
                        tp1_price=float(pos.get("tp1", 0.0)),
                        tp2_price=float(pos.get("tp2", 0.0)),
                        raw_r_result=float(pos.get("realized_r", 0.0)),
                        sub_strategy=f"{pos.get('timeframe', 'M15')}_OrderFlow",
                        metadata={
                            "status": pos.get("status", "OPEN"),
                            "live_active": is_open,
                            "rr_ratio": 2.0,
                            "price_source": "binance_futures" if str(pos.get("symbol", "")).upper().endswith("USDT") else "mt5",
                        }
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
                    if TRAWMA_WHITELIST is not None and sym not in TRAWMA_WHITELIST:
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
        # The native tracker publishes next-open intents separately from filled
        # paper positions. Waiting for a filled candle's CLOSE would deliver an
        # H1 intent an hour late. Never read future paper exits as entry inputs.
        stigma_paths = (self.stigma_live_path.with_suffix('.pending.json'), self.stigma_live_path)
        seen_stigma_ids = set()
        for stigma_path in stigma_paths:
            if not stigma_path.exists():
                continue
            try:
                live_dict = json.loads(stigma_path.read_text(encoding="utf-8"))
                positions = live_dict.values() if isinstance(live_dict, dict) else live_dict
                for pos in positions:
                    sym = norm_symbol(pos.get("symbol", ""))
                    if STIGMA_WHITELIST is not None and sym not in STIGMA_WHITELIST:
                        continue
                    minutes = {"M5": 5, "M15": 15, "H1": 60}.get(str(pos.get("timeframe", "M15")).upper())
                    signal_time = pos.get("signal_time") or (pos.get("bar_time") if pos.get("status") == "PENDING_NEXT_OPEN" else None)
                    if signal_time is not None:
                        if minutes is None:
                            continue
                        dt_in = pd.to_datetime(signal_time, utc=True) + pd.Timedelta(minutes=minutes)
                    elif pos.get("entry_time"):
                        dt_in = pd.to_datetime(pos["entry_time"], utc=True)
                    else:
                        continue
                    strat_mode = str(pos.get("strategy_mode", "OU_ONLY")).upper()
                    regime = StrategyRegime.MEAN_REVERSION if "OU" in strat_mode else StrategyRegime.TREND_MOMENTUM
                    signal_id = f"STG_{sym}_{strat_mode}_{int(dt_in.timestamp())}"
                    if signal_id in seen_stigma_ids:
                        continue
                    seen_stigma_ids.add(signal_id)
                    sig = TradeSignal(
                        signal_id=signal_id,
                        engine_name="Stigma",
                        math_family=MathFamily.FAMILY_STAT_QUANT,
                        strategy_regime=regime,
                        symbol=sym,
                        direction=norm_direction(pos.get("side", "LONG")),
                        entry_time=dt_in,
                        exit_time=None,
                        entry_price=float(pos.get("entry") or pos.get("entry_price", 0.0)),
                        sl_price=float(pos.get("initial_stop_loss") or pos.get("stop_loss", 0.0)),
                        tp1_price=float(pos.get("tp1", 0.0)),
                        tp2_price=float(pos.get("tp2", 0.0)),
                        raw_r_result=0.0,
                        sub_strategy=strat_mode,
                        metadata={"status": pos.get("status", "OPEN"), "live_active": True, "rr_ratio": 2.0,
                                  "entry_time_basis": "closed_signal_next_open_intent" if signal_time is not None else "native_recorded_entry",
                                  "initial_stop_preserved": bool(pos.get("initial_stop_loss")), "source_artifact": stigma_path.name}
                    )
                    for metric in ("hurst", "half_life"):
                        try:
                            number = float(pos.get(metric))
                        except (TypeError, ValueError):
                            continue
                        if math.isfinite(number):
                            sig.metadata[metric] = number
                    signals.append(sig)
            except Exception as e:
                print(f"[StreamLoader] Warning loading Stigma live positions: {e}")

        # 2. Historical Cache for context
        if self.stigma_cache_path.exists():
            try:
                data = json.loads(self.stigma_cache_path.read_text(encoding="utf-8"))
                for d in data:
                    sym = norm_symbol(d["symbol"])
                    if STIGMA_WHITELIST is not None and sym not in STIGMA_WHITELIST:
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
