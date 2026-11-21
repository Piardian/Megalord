"""Megalord 24/7 Command Center & Multi-Engine Live/Shadow Daemon.

Monitors live market signals, arbitrates across orthogonal families,
enforces worst-case prop-firm risk firewalls, persists state, and dispatches
console dashboards and Telegram alerts.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from megalord.types import TradeSignal, ArbiterDecision, MathFamily, StrategyRegime
from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.firewall.risk_firewall import MegalordRiskFirewall
from megalord.firewall.news_guard import MegalordNewsGuard
from megalord.live.mt5_executor import MegalordMT5Executor
from megalord.live.position_policy import execution_plan, protection_steps, stop_at_r
from megalord.adapters.stream_loader import ProjectStreamLoader, norm_symbol, norm_direction
from megalord.research.decision_evidence import capture_entry_context, finish_entry_context, append_entry_evidence

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# Configure UTF-8 for console output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


class MegalordTelegramNotifier:
    """Dispatches formatted HTML execution alerts to Telegram."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        cfg_tg = (config or {}).get("telegram", {})
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN") or cfg_tg.get("bot_token")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID") or cfg_tg.get("chat_id")

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_alert(self, text_html: str) -> bool:
        if not self.is_configured():
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as exc:
            print(f"[TelegramNotifier] Warning: Failed to send Telegram alert: {exc}")
            return False


class MegalordDaemon:
    """24/7 Multi-Engine Orchestrator and Live Decision Gateway."""

    def __init__(
        self,
        config_path: Path | str = Path(r"C:\Users\piard\Desktop\MegalordProject\config\megalord_config.json"),
        state_path: Path | str = Path(r"C:\Users\piard\Desktop\MegalordProject\megalord_live_state.json"),
        mode: str = "shadow",
        notifier: Optional[MegalordTelegramNotifier] = None,
    ):
        self.config_path = Path(config_path)
        self.state_path = Path(state_path)
        self.entry_evidence_path = self.state_path.with_name(self.state_path.stem + ".entry_evidence.jsonl")
        from megalord.research.context_archive import ContextArchive
        self.context_archive = ContextArchive(self.state_path.with_name(self.state_path.stem + ".context_evidence.jsonl"))
        self.entry_evidence_counts = {"captured": 0, "errors": 0, "mismatches": 0, "compass_excluded": 0}
        if str(mode).lower() == "mt5_live":
            print("[GÜVENLİK KİLİDİ] The 5%ers canlı bağlantısı kesilmiştir! Mod zorunlu olarak 'shadow' yapıldı.")
        self.mode = "shadow"
        self.config = self._load_config()
        self.arbiter = MegalordSignalArbiter(self.config)
        self.firewall = MegalordRiskFirewall(self.config)
        self.news_guard = MegalordNewsGuard()
        self.notifier = notifier or MegalordTelegramNotifier(config=self.config)
        risk_cfg = self.config.get("risk_sizing", {})
        self.mt5_executor = MegalordMT5Executor(
            target_account=self.config.get("target_account", 26735345),
            base_risk_pct=float(risk_cfg.get("base_risk_pct", 0.50)),
        )
        self.stream_loader = ProjectStreamLoader()
        self.state = self._load_state()

    def _entry_context(self, candidate, decided_at, open_positions, macro_bias):
        """Passive archive input. Archive failure must not change trading policy."""
        if candidate.engine_name == "Convexity_v2":
            self.entry_evidence_counts["compass_excluded"] += 1
            return None
        try:
            return capture_entry_context(self.config, candidate, decided_at=decided_at,
                active_positions=open_positions,
                daily_realized_loss_r=float(self.state.get("daily_realized_loss_r", 0.0)),
                loss_streaks=self.arbiter.engine_loss_streaks, macro_bias=macro_bias)
        except Exception as exc:
            self.entry_evidence_counts["errors"] += 1
            print(f"[EntryEvidence] Input not archived for {candidate.signal_id}: {exc}")
            return None

    def _archive_entry_context(self, context, decision, *, news_check=None, news_available_at=None, firewall_result=None):
        if context is None:
            return
        try:
            record = finish_entry_context(context, decision=decision, news_check=news_check,
                news_available_at=news_available_at, firewall_result=firewall_result)
            append_entry_evidence(self.entry_evidence_path, record)
            self.entry_evidence_counts["captured"] += 1
            if not record["gate_parity_equal"]:
                self.entry_evidence_counts["mismatches"] += 1
                print(f"[EntryEvidence] Gate replay differs for {context['signal']['signal_id']}")
        except Exception as exc:
            self.entry_evidence_counts["errors"] += 1
            print(f"[EntryEvidence] Result not archived for {context['signal']['signal_id']}: {exc}")

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[Daemon] Warning: Could not parse config {self.config_path}: {exc}")
        return {}

    def _load_state(self) -> Dict[str, Any]:
        default_state = {
            "system_name": "Megalord Meta-Allocator",
            "mode": self.mode,
            "account_id": self.config.get("target_account", 26735345),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "daily_realized_loss_r": 0.0,
            "daily_realized_pnl_r": 0.0,
            "current_date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "open_positions": [],
            "closed_trades_history": [],
            "processed_signal_ids": [],
            "active_compass_bias": {},
            "performance_summary": {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "net_r": 0.0,
                "confluence_boosts_count": 0,
                "firewall_blocks_count": 0,
                "vetoes_count": 0,
            },
        }
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if data.get("current_date_utc") != today:
                    # New trading day reset for daily drawdown budget
                    data["current_date_utc"] = today
                    data["daily_realized_loss_r"] = 0.0
                    data["daily_realized_pnl_r"] = 0.0
                data["mode"] = self.mode
                return data
            except Exception as exc:
                print(f"[Daemon] Warning: Could not read state from {self.state_path}: {exc}")
        return default_state

    def save_state(self) -> None:
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            tmp_path = self.state_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(self.state, indent=2, default=str), encoding="utf-8")
            os.replace(tmp_path, self.state_path)
        except Exception as exc:
            print(f"[Daemon] Warning: Could not persist state: {exc}")

    def reconcile_mt5_positions(self) -> None:
        """Synchronizes any untracked live MT5 positions into state['open_positions']."""
        if self.mode != "mt5_live":
            return
        try:
            import MetaTrader5 as mt5
            if not self.mt5_executor.ensure_connected():
                return
            mt5_positions = mt5.positions_get()
            if not mt5_positions:
                return

            open_positions = self.state.setdefault("open_positions", [])
            tracked_tickets = set()
            for pos in open_positions:
                for key in ("ticket", "leg1_ticket", "leg2_ticket"):
                    val = pos.get(key)
                    if val:
                        tracked_tickets.add(int(val))

            for mp in mt5_positions:
                t_id = int(mp.ticket)
                if t_id in tracked_tickets:
                    continue
                direction = "LONG" if int(mp.type) == 0 else "SHORT"
                entry_p = float(mp.price_open or 0.0)
                sl_p = float(mp.sl or 0.0)
                tp_p = float(mp.tp or 0.0)
                already_be = (
                    (direction == "LONG" and sl_p >= entry_p > 0)
                    or (direction == "SHORT" and 0 < sl_p <= entry_p)
                )
                r_dist = abs(entry_p - sl_p) if (entry_p > 0 and sl_p > 0 and not already_be) else (entry_p * 0.003)
                synced_record = {
                    "signal_id": f"MT5_SYNC_{t_id}",
                    "engine_name": "MT5_Synced",
                    "math_family": MathFamily.FAMILY_SMC.value,
                    "strategy_regime": StrategyRegime.MEAN_REVERSION.value,
                    "symbol": norm_symbol(mp.symbol),
                    "direction": direction,
                    "entry_time": datetime.fromtimestamp(int(mp.time), tz=timezone.utc).isoformat(),
                    "allocated_risk_r": 0.50,
                    "action": "RECONCILED_LIVE",
                    "arbiter_reason": "Adopted from live MT5 open positions table",
                    "confluent_families": [],
                    "confluent_engines": [],
                    "status": "OPEN",
                    "raw_r_result": 0.0,
                    "ticket": t_id,
                    "leg1_ticket": t_id,
                    "leg1_vol": float(mp.volume or 0.0),
                    "leg1_tp": tp_p,
                    "leg1_open": True,
                    "leg1_profit": 0.0,
                    "leg2_ticket": None,
                    "leg2_vol": 0.0,
                    "leg2_tp": 0.0,
                    "leg2_open": False,
                    "entry_price": entry_p,
                    "sl_price": sl_p,
                    "risk_dist": r_dist,
                    "be_moved": already_be,
                    "be_pending": False,
                    "trailing_level": 0.0,
                }
                open_positions.append(synced_record)
                tracked_tickets.add(t_id)
                print(f"[Reconcile] 🔄 MT5 Açık Pozisyon Senkronize Edildi: #{t_id} {mp.symbol} {direction} (SL: {sl_p})")
        except Exception as exc:
            print(f"[Reconcile] Warning during MT5 position sync: {exc}")

    def fetch_htf_macro_gates(self) -> Dict[str, str]:
        """Reads Begonya shared macro bias gates and Convexity D1 trend direction."""
        gates: Dict[str, str] = {}
        macro_path = Path(r"C:\Users\piard\Desktop\Begonya00\shared\macro_bias_gate.json")
        if not macro_path.exists():
            macro_path = Path(r"C:\Users\piard\Desktop\Begonya00\macro_engine\gateways\macro_bias_gate.json")

        if macro_path.exists():
            try:
                data = json.loads(macro_path.read_text(encoding="utf-8"))
                bias_dict = data.get("deterministic_execution_bias_gates") or data.get("execution_bias_gates") or {}
                for sym, bias in bias_dict.items():
                    nsym = norm_symbol(sym)
                    if bias == "LONG_ONLY":
                        gates[nsym] = "LONG"
                    elif bias == "SHORT_ONLY":
                        gates[nsym] = "SHORT"
                    elif bias == "NEUTRAL_RANGE":
                        gates[nsym] = "NEUTRAL"
                # Map broad US equity index trend to NAS100 if not explicitly defined
                if "SPX" in gates and "NAS100" not in gates:
                    gates["NAS100"] = gates["SPX"]
                    gates["US100"] = gates["SPX"]
            except Exception as exc:
                print(f"[Daemon] Warning reading macro gate: {exc}")

        # Also register live compass bias in state
        self.state["active_compass_bias"] = gates
        return gates

    def poll_new_incoming_signals(self) -> List[TradeSignal]:
        """Loads signals across all active and compass streams, returning unhandled candidates."""
        all_signals = self.stream_loader.load_all_signals()
        processed_set = set(self.state.get("processed_signal_ids", []))
        new_signals = [s for s in all_signals if s.signal_id not in processed_set]
        return new_signals

    @staticmethod
    def _is_sl_tightening(direction: str, current_sl: float, candidate_sl: float) -> bool:
        """Ratchet guard: ensures BE/Trailing updates only tighten SL and never loosen it."""
        if candidate_sl <= 0:
            return False
        if current_sl <= 0:
            return True
        if direction == "LONG":
            return candidate_sl > (current_sl + 1e-7)
        return candidate_sl < (current_sl - 1e-7)

    def _compute_be_price(self, pos: Dict[str, Any], default_entry: float = 0.0) -> float:
        """Computes Break-Even price with a +0.05R commission/spread buffer."""
        entry_p = float(pos.get("entry_price") or default_entry or 0.0)
        if entry_p <= 0:
            return 0.0
        r_dist = float(pos.get("risk_dist") or 0.0)
        direction = pos.get("direction", "LONG")
        return stop_at_r(entry_p, max(r_dist, 0.0), direction, .05)

    def execute_cycle(self) -> Dict[str, Any]:
        """Runs a single orchestration cycle."""
        self.entry_evidence_counts = {"captured": 0, "errors": 0, "mismatches": 0, "compass_excluded": 0}
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.get("current_date_utc") != today:
            self.state["current_date_utc"] = today
            self.state["daily_realized_loss_r"] = 0.0
            self.state["daily_realized_pnl_r"] = 0.0

        if self.mode == "mt5_live":
            self.reconcile_mt5_positions()

        macro_gates = self.fetch_htf_macro_gates()
        try:
            self.context_archive.observe(observed_at=datetime.now(timezone.utc), macro_gates=macro_gates,
                news_config=self.news_guard.config, news_refreshed_at=self.news_guard._last_refresh_utc)
        except Exception as exc:
            print(f"[ContextArchive] observation could not be archived: {type(exc).__name__}")
        new_signals = self.poll_new_incoming_signals()
        open_positions = self.state.get("open_positions", [])

        cycle_results = {
            "entry_evidence": self.entry_evidence_counts,
            "processed": len(new_signals),
            "approved": 0,
            "blocked": 0,
            "vetoed": 0,
            "confluences": 0,
            "actions": [],
        }

        # Convert open positions to TradeSignal objects for Arbiter
        active_engine_trades: List[TradeSignal] = []
        for pos in open_positions:
            try:
                sig_obj = TradeSignal(
                    signal_id=pos["signal_id"],
                    engine_name=pos["engine_name"],
                    math_family=MathFamily(pos["math_family"]),
                    strategy_regime=StrategyRegime(pos["strategy_regime"]),
                    symbol=pos["symbol"],
                    direction=pos["direction"],
                    entry_time=pd.to_datetime(pos["entry_time"], utc=True),
                    exit_time=None,
                    risk_r=pos.get("allocated_risk_r", 1.0),
                )
                active_engine_trades.append(sig_obj)
            except Exception:
                continue

        for candidate in new_signals:
            macro_bias = macro_gates.get(norm_symbol(candidate.symbol))

            # Live quality checks require a fresh broker quote. On a transient
            # quote/account failure, leave the signal unprocessed so it can retry.
            if self.mode == "mt5_live" and candidate.exit_time is None:
                candidate.metadata = dict(candidate.metadata or {})
                candidate.metadata["quality_context"] = "live"
                if macro_bias:
                    candidate.metadata["htf_bias"] = macro_bias
                market_context = self.mt5_executor.get_market_quality_context(
                    candidate.symbol, candidate.direction
                )
                if market_context is None:
                    cycle_results["blocked"] += 1
                    cycle_results["actions"].append({
                        "signal_id": candidate.signal_id,
                        "engine": candidate.engine_name,
                        "symbol": candidate.symbol,
                        "status": "QUALITY_MARKET_CONTEXT_MISSING",
                        "action": "QUALITY_MARKET_CONTEXT_MISSING",
                        "reason": "Fresh MT5 quote/account context unavailable; signal left unprocessed for retry.",
                    })
                    continue
                candidate.metadata.update(market_context)

                # Trawma Binance Futures -> MT5 Spot/CFD Basis Normalization
                if candidate.metadata.get("price_source") == "binance_futures" and candidate.entry_price > 0:
                    live_quote = float(market_context.get("current_price") or 0.0)
                    sig_age = (datetime.now(timezone.utc) - candidate.entry_time).total_seconds()
                    if live_quote > 0 and -5.0 <= sig_age <= 180.0:
                        basis_shift = live_quote - float(candidate.entry_price)
                        candidate.entry_price = round(float(candidate.entry_price) + basis_shift, 5)
                        if candidate.sl_price > 0:
                            candidate.sl_price = round(float(candidate.sl_price) + basis_shift, 5)
                        if candidate.tp1_price > 0:
                            candidate.tp1_price = round(float(candidate.tp1_price) + basis_shift, 5)
                        if candidate.tp2_price > 0:
                            candidate.tp2_price = round(float(candidate.tp2_price) + basis_shift, 5)
                        candidate.metadata["basis_shift_applied"] = round(basis_shift, 5)

            # 1. Evaluate candidate through Arbiter (Confluence, Clone, MR vs Trend, Compass-only, Weekend Filter)
            decision_at = datetime.now(timezone.utc)
            decision: ArbiterDecision = self.arbiter.evaluate_signal(
                candidate=candidate,
                active_positions=open_positions,
                macro_htf_bias=macro_bias,
                now_utc=decision_at,
            )
            entry_context = self._entry_context(candidate, decision_at, open_positions, macro_bias)

            # Do NOT permanently burn PENDING_LIMIT signals when waiting for price to pull back into entry box
            sig_status = str((candidate.metadata or {}).get("status", "")).upper()
            is_waiting_pullback = (
                not decision.allowed
                and decision.action == "QUALITY_ENTRY_DRIFT"
                and sig_status == "PENDING_LIMIT"
            )
            if not is_waiting_pullback:
                self.state.setdefault("processed_signal_ids", []).append(candidate.signal_id)

            if not decision.allowed:
                self._archive_entry_context(entry_context, decision)
                if decision.action == "HTF_CONFLICT_VETO":
                    self.state["performance_summary"]["vetoes_count"] += 1
                    cycle_results["vetoed"] += 1
                cycle_results["blocked"] += 1
                cycle_results["actions"].append({
                    "signal_id": candidate.signal_id,
                    "engine": candidate.engine_name,
                    "symbol": candidate.symbol,
                    "status": "WAITING_LIMIT_PULLBACK" if is_waiting_pullback else "REJECTED_BY_ARBITER",
                    "action": decision.action,
                    "reason": decision.reason,
                })
                continue

            # 2. Check The 5%ers High-Impact News Freeze (News Guard)
            news_check = self.news_guard.check_news_freeze(candidate.symbol)
            news_available_at = datetime.now(timezone.utc)
            if news_check.get("is_frozen"):
                self._archive_entry_context(entry_context, decision,
                    news_check=news_check, news_available_at=news_available_at)
                cycle_results["blocked"] += 1
                cycle_results["actions"].append({
                    "signal_id": candidate.signal_id,
                    "engine": candidate.engine_name,
                    "symbol": candidate.symbol,
                    "status": "NEWS_GUARD_BLOCKED",
                    "action": "NEWS_GUARD_BLOCKED",
                    "reason": news_check.get("reason"),
                })
                print(f"[NewsGuard] 🛑 Veto: {candidate.symbol} {candidate.direction} -> {news_check.get('reason')}")
                continue

            # 3. Evaluate Firewall (Worst-case daily draw, currency cluster caps)
            allocated_risk_r = decision.final_risk_mult
            allowed_fw, fw_reason = self.firewall.evaluate_entry(
                candidate_signal=candidate,
                allocated_risk_r=allocated_risk_r,
                daily_realized_loss_r=float(self.state.get("daily_realized_loss_r", 0.0)),
                open_positions=open_positions,
            )
            self._archive_entry_context(entry_context, decision, news_check=news_check,
                news_available_at=news_available_at, firewall_result=(allowed_fw, fw_reason))

            if not allowed_fw:
                self.state["performance_summary"]["firewall_blocks_count"] += 1
                cycle_results["blocked"] += 1
                cycle_results["actions"].append({
                    "signal_id": candidate.signal_id,
                    "engine": candidate.engine_name,
                    "symbol": candidate.symbol,
                    "status": "FIREWALL_BLOCKED",
                    "action": "FIREWALL_BLOCKED",
                    "reason": fw_reason,
                })
                continue

            # 4. Approved Trade Execution (Shadow / MT5 Live)
            if decision.action == "CONFLUENCE_BOOST":
                self.state["performance_summary"]["confluence_boosts_count"] += 1
                cycle_results["confluences"] += 1

            cycle_results["approved"] += 1
            live_ticket = None
            live_res = None  # Shadow mode has no broker execution result.

            # If MT5 Live Mode is active, send real order to The 5%ers broker
            if self.mode == "mt5_live":
                now_utc = datetime.now(timezone.utc)
                sig_age_sec = (now_utc - candidate.entry_time).total_seconds()
                max_age = 14400 if sig_status == "PENDING_LIMIT" else 900
                if sig_age_sec > max_age:
                    print(f"[Megalord Live] 🛡️ Bayat Sinyal Koruması: {candidate.symbol} {candidate.direction} ({sig_age_sec/60:.1f} dk önce üretilmiş). Canlı piyasa emri iletilmedi.")
                    cycle_results["blocked"] += 1
                    cycle_results["actions"].append({
                        "signal_id": candidate.signal_id,
                        "engine": candidate.engine_name,
                        "symbol": candidate.symbol,
                        "status": "STALE_SKIPPED",
                        "action": "STALE_SKIPPED",
                        "reason": f"Signal is {sig_age_sec/60:.1f} minutes old",
                    })
                    continue

                # Query fresh broker bid/ask to calculate true live risk and reward distances
                market_ctx = self.mt5_executor.get_market_quality_context(candidate.symbol, candidate.direction)
                live_p = market_ctx.get("current_price", candidate.entry_price) if market_ctx else candidate.entry_price

                plan = execution_plan(candidate.engine_name, candidate.symbol, candidate.direction,
                    live_p, candidate.sl_price, candidate.tp1_price, candidate.tp2_price)
                sl_val, risk_dist, tp1_val, tp2_val = plan.stop, plan.risk_distance, plan.tp1, plan.tp2
                one_r_cash_at_entry = self.mt5_executor.get_one_r_cash_verified()

                # İkiz Emir (Twin Orders) Mimarisi: %50 TP1 Kâr Kilidi, %50 TP2 Runner
                live_res = self.mt5_executor.place_twin_market_orders(
                    symbol=candidate.symbol,
                    direction=candidate.direction,
                    sl_price=sl_val,
                    tp1_price=tp1_val,
                    tp2_price=tp2_val,
                    risk_mult=allocated_risk_r,
                    comment=f"MGL_{candidate.engine_name[:6]}"
                )

                if live_res and live_res.get("leg1_ticket"):
                    live_ticket = live_res.get("leg1_ticket")
                    leg2_info = f" | Leg2: #{live_res.get('leg2_ticket')}" if live_res.get("leg2_ticket") else ""
                    print(f"[Megalord Live] 🚀 İkiz Emir İletildi: Leg1: #{live_ticket}{leg2_info} {candidate.symbol} {candidate.direction} (Toplam Lot: {live_res.get('total_lot')})")
                else:
                    print(f"[Megalord Live] ❌ MT5 İkiz Emir İletimi Başarısız: {candidate.symbol} {candidate.direction}")
                    cycle_results["blocked"] += 1
                    cycle_results["actions"].append({
                        "signal_id": candidate.signal_id,
                        "engine": candidate.engine_name,
                        "symbol": candidate.symbol,
                        "status": "ORDER_FAILED",
                        "action": "ORDER_FAILED",
                        "reason": "MT5 broker rejected twin orders",
                    })
                    continue

            pos_record = {
                "signal_id": candidate.signal_id,
                "engine_name": candidate.engine_name,
                "math_family": candidate.math_family.value,
                "strategy_regime": candidate.strategy_regime.value,
                "symbol": candidate.symbol,
                "direction": candidate.direction,
                "entry_time": str(candidate.entry_time),
                "allocated_risk_r": allocated_risk_r,
                "action": decision.action,
                "arbiter_reason": decision.reason,
                "confluent_families": decision.confluent_families,
                "confluent_engines": decision.confluent_engines,
                "status": "OPEN",
                "raw_r_result": candidate.raw_r_result,
                "one_r_cash_at_entry": one_r_cash_at_entry if self.mode == "mt5_live" else None,
                "ticket": live_ticket,
                "leg1_ticket": live_res.get("leg1_ticket") if live_res else None,
                "leg1_vol": live_res.get("leg1_vol", 0.0) if live_res else 0.0,
                "leg1_tp": live_res.get("leg1_tp", tp1_val) if self.mode == "mt5_live" else candidate.tp1_price,
                "leg1_open": bool(live_res.get("leg1_ticket")) if live_res else True,
                "leg1_profit": 0.0,
                "leg2_ticket": live_res.get("leg2_ticket") if live_res else None,
                "leg2_vol": live_res.get("leg2_vol", 0.0) if live_res else 0.0,
                "leg2_tp": live_res.get("leg2_tp", tp2_val) if self.mode == "mt5_live" else candidate.tp2_price,
                "leg2_open": bool(live_res.get("leg2_ticket")) if live_res else False,
                "entry_price": (float(live_res.get("entry_price") or 0.0) if (live_res and float(live_res.get("entry_price") or 0.0) > 0) else float(candidate.entry_price)),
                "sl_price": sl_val if self.mode == "mt5_live" else candidate.sl_price,
                "risk_dist": risk_dist if self.mode == "mt5_live" else abs(candidate.entry_price - candidate.sl_price),
                "be_moved": False,
                "be_pending": False,
                "trailing_level": 0.0,
            }
            open_positions.append(pos_record)
            active_engine_trades.append(candidate)

            cycle_results["actions"].append({
                "signal_id": candidate.signal_id,
                "engine": candidate.engine_name,
                "symbol": candidate.symbol,
                "status": "APPROVED",
                "action": decision.action,
                "allocated_risk_r": allocated_risk_r,
                "reason": decision.reason,
                "ticket": live_ticket,
            })

            # Format and send Telegram Alert
            self._dispatch_alert(candidate, decision, allocated_risk_r, ticket=live_ticket, twin_info=live_res)

        # Position Lifecycle Management: Check open positions for TP1, TP2, SL, BE exits
        if self.mode == "mt5_live":
            import MetaTrader5 as mt5
            one_r_cash = self.mt5_executor.get_one_r_cash()
        else:
            one_r_cash = 49.30

        active_open_positions = []
        now_utc = datetime.now(timezone.utc)
        stream_id_map = {s.signal_id: s for s in self.stream_loader.load_all_signals()}
        for pos in open_positions:
            is_closed = False
            close_reason = ""
            net_pnl_r = 0.0

            # EOD Index Session Close Rule (Protect against overnight gap and illiquid spread slippage)
            sym_u = str(pos.get("symbol", "")).upper()
            is_index_sym = any(idx_name in sym_u for idx_name in ("NAS100", "US100", "SPX", "SP500", "GER40"))
            # 20:45 UTC to 01:00 UTC overnight protection window
            in_eod_window = (now_utc.hour == 20 and now_utc.minute >= 45) or (now_utc.hour >= 21) or (now_utc.hour < 1)
            if is_index_sym and in_eod_window:
                all_closed_ok = True
                fail_details = []
                if self.mode == "mt5_live":
                    t1 = pos.get("leg1_ticket") or pos.get("ticket")
                    t2 = pos.get("leg2_ticket")
                    if t1:
                        ok1, det1 = self.mt5_executor.close_position(t1, "EOD_INDEX_SESSION_CLOSE")
                        if not ok1:
                            all_closed_ok = False
                            fail_details.append(f"Leg1 #{t1}: {det1}")
                    if t2:
                        ok2, det2 = self.mt5_executor.close_position(t2, "EOD_INDEX_SESSION_CLOSE")
                        if not ok2:
                            all_closed_ok = False
                            fail_details.append(f"Leg2 #{t2}: {det2}")
                if all_closed_ok:
                    is_closed = True
                    close_reason = "EOD_INDEX_SESSION_CLOSE"
                    print(f"[EOD Guard] 🕒 {sym_u} seans sonu (20:45+ UTC) başarıyla kapatıldı (Gece kayma koruması aktif).")
                else:
                    is_closed = False
                    err_msg = "; ".join(fail_details)
                    print(f"[EOD Guard Alert] ⚠️ {sym_u} EOD kapatma BAŞARISIZ: {err_msg}! Gelecek döngüde tekrar denenecek.")

            # 1. CANLI MT5 HESAP TAKİBİ (Break-Even & İz Süren Stop & Çift Bacak Kapanış)
            if self.mode == "mt5_live" and (pos.get("leg1_ticket") or pos.get("ticket")):
                l1_tick = pos.get("leg1_ticket") or pos.get("ticket")
                l2_tick = pos.get("leg2_ticket")

                # SL Watchdog: Stop Loss Guard
                if l1_tick:
                    l1_chk = self.mt5_executor.get_open_position(l1_tick)
                    if l1_chk is not None and not (float(getattr(l1_chk, "sl", 0.0) or 0.0) > 0):
                        exp_sl = self._compute_be_price(pos, float(l1_chk.price_open or 0.0)) if pos.get("be_moved") else float(pos.get("sl_price", 0.0))
                        if exp_sl > 0:
                            print(f"[SL Watchdog] 🚨 ACİL: Leg 1 #{l1_tick} ({pos['symbol']}) stopsuz tespit edildi! Stop {exp_sl} seviyesine geri yükleniyor...")
                            self.mt5_executor.modify_position_sl(l1_tick, pos["symbol"], exp_sl, pos.get("leg1_tp", 0.0))

                if l2_tick:
                    l2_chk = self.mt5_executor.get_open_position(l2_tick)
                    if l2_chk is not None and not (float(getattr(l2_chk, "sl", 0.0) or 0.0) > 0):
                        exp_sl = self._compute_be_price(pos, float(l2_chk.price_open or 0.0)) if pos.get("be_moved") else float(pos.get("sl_price", 0.0))
                        if exp_sl > 0:
                            print(f"[SL Watchdog] 🚨 ACİL: Leg 2 #{l2_tick} ({pos['symbol']}) stopsuz tespit edildi! Stop {exp_sl} seviyesine geri yükleniyor...")
                            self.mt5_executor.modify_position_sl(l2_tick, pos["symbol"], exp_sl, pos.get("leg2_tp", 0.0))

                # Bacak 1 Kontrolü (TP1 - %50 Kâr Kilidi)
                if pos.get("leg1_open", True) and l1_tick:
                    l1_pos = self.mt5_executor.get_open_position(l1_tick)
                    if l1_pos is None:
                        # Bacak 1 MT5'te kapandı!
                        deal1 = self.mt5_executor.get_position_close_deal(l1_tick)
                        if deal1 is None:
                            pos["close_deal_pending"] = l1_tick
                            active_open_positions.append(pos)
                            continue
                        pos["leg1_open"] = False
                        pos.pop("close_deal_pending", None)
                        pos["leg1_close_evidence"] = deal1
                        profit1 = float(deal1.get("profit", 0.0)) if deal1 else 0.0
                        pos["leg1_profit"] = profit1
                        d_comm1 = deal1.get("comment", "") if deal1 else ""
                        print(f"[Lifecycle] 🎯 Bacak 1 Kapandı: #{l1_tick} Net Kâr: ${profit1:.2f} ({d_comm1})")

                        if not l2_tick:
                            # Tek bacaklı emir (Single Order) doğrudan kapandı!
                            is_closed = True
                            reference_r = float(pos.get("one_r_cash_at_entry") or one_r_cash)
                            pos["r_reference_verified"] = bool(pos.get("one_r_cash_at_entry"))
                            net_pnl_r = round(profit1 / reference_r, 2)
                            if "[tp" in d_comm1.lower() or (deal1.get("reason") is not None and deal1["reason"] == mt5.DEAL_REASON_TP):
                                close_reason = "TP1_HIT"
                                badge = "🏆 <b>TEK EMİR HEDEFLE KAPANDI (TP)</b>"
                            elif "[sl" in d_comm1.lower() or (deal1.get("reason") is not None and deal1["reason"] == mt5.DEAL_REASON_SL):
                                close_reason = "STOP_LOSS"
                                badge = "🔴 <b>STOP LOSS TETİKLENDİ</b>"
                            else:
                                close_reason = "POSITION_CLOSED"
                                badge = "🏁 <b>POZİSYON KAPANDI</b>"

                            self.notifier.send_alert(
                                f"{badge}\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"🎯 <b>Sembol:</b> <code>{pos['symbol']}</code>\n"
                                f"💰 <b>Realize Net Kâr/Zarar:</b> <b>${profit1:.2f}</b> ({net_pnl_r:+.2f}R)\n"
                                f"🏁 <b>Kapanış Nedeni:</b> <code>{close_reason}</code>"
                            )
                        elif profit1 > 0 or "[tp" in d_comm1.lower():
                            pos["be_pending"] = True
                            self.notifier.send_alert(
                                f"🎯 <b>MEGALORD — %50 TP1 ALINDI!</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"🎯 <b>Sembol:</b> <code>{pos['symbol']}</code> ({pos['direction']})\n"
                                f"💰 <b>Realize Net Kâr:</b> <b>+${profit1:.2f}</b> (%50 Parça)\n"
                                f"🛡️ <b>Aksiyon:</b> Kalan %50 Bacak için Stop Giriş+Komisyon (Break-Even) seviyesine taşınıyor..."
                            )

                # Bağımsız Break-Even Retry Döngüsü: Bacak 1 TP olduktan sonra Bacak 2'nin BE'ye çekilmesi
                # ilk denemede başarısız olsa bile her döngüde tekrar dener!
                if l2_tick and not pos.get("be_moved") and (pos.get("be_pending") or not pos.get("leg1_open", True)) and float(pos.get("leg1_profit", 0.0)) > 0:
                    l2_open_p = self.mt5_executor.get_open_position(l2_tick)
                    if l2_open_p is not None:
                        if pos.get("entry_price", 0.0) <= 0 and l2_open_p.price_open > 0:
                            pos["entry_price"] = float(l2_open_p.price_open)
                        be_p = self._compute_be_price(pos, float(l2_open_p.price_open or 0.0))
                        if be_p > 0:
                            mod_ok = self.mt5_executor.modify_position_sl(l2_tick, pos["symbol"], be_p, pos.get("leg2_tp", 0.0))
                            if mod_ok:
                                pos["be_moved"] = True
                                pos["be_pending"] = False
                                self.notifier.send_alert(
                                    f"🛡️ <b>BREAK-EVEN AKTİF (SIFIR RİSK):</b>\n"
                                    f"Kalan Bacak (#{l2_tick}) Stopu <code>{be_p}</code> (+0.05R BE) seviyesine kilitlendi!\n"
                                    f"🎯 <b>Hedef:</b> TP2 (<code>{pos.get('leg2_tp')}</code>)"
                                )

                # Proaktif +0.80R Erken Koruma & Kademeli İz Süren Stop (+1.50R -> +0.75R, +2.20R -> +1.40R)
                l1_live = self.mt5_executor.get_open_position(l1_tick) if (l1_tick and pos.get("leg1_open", True)) else None
                l2_live = self.mt5_executor.get_open_position(l2_tick) if l2_tick else None
                ref_pos = l2_live or l1_live
                if ref_pos is not None:
                    if pos.get("entry_price", 0.0) <= 0 and ref_pos.price_open > 0:
                        pos["entry_price"] = float(ref_pos.price_open)
                    tick_live = mt5.symbol_info_tick(pos["symbol"])
                    if tick_live and pos.get("risk_dist", 0.0) > 0 and pos.get("entry_price", 0.0) > 0:
                        cur_p = tick_live.bid if pos["direction"] == "LONG" else tick_live.ask
                        fav_dist = (cur_p - pos["entry_price"]) if pos["direction"] == "LONG" else (pos["entry_price"] - cur_p)
                        r_dist = float(pos["risk_dist"])
                        protection = protection_steps(pos["entry_price"], r_dist, pos["direction"], cur_p,
                            be_moved=bool(pos.get("be_moved")), trailing_level=float(pos.get("trailing_level", 0.0)))

                        # Aşama 1: Fiyat +0.80R'a ulaştıysa tüm açık bacakları Break-Even (+0.05R) seviyesine koru
                        if "EARLY_BE" in protection:
                            be_p = self._compute_be_price(pos)
                            if be_p > 0:
                                ok1 = (
                                    self.mt5_executor.modify_position_sl(l1_tick, pos["symbol"], be_p, pos.get("leg1_tp", 0.0))
                                    if (l1_live and self._is_sl_tightening(pos["direction"], float(l1_live.sl or 0.0), be_p))
                                    else True
                                )
                                ok2 = (
                                    self.mt5_executor.modify_position_sl(l2_tick, pos["symbol"], be_p, pos.get("leg2_tp", 0.0))
                                    if (l2_live and self._is_sl_tightening(pos["direction"], float(l2_live.sl or 0.0), be_p))
                                    else True
                                )
                                if ok1 and ok2:
                                    pos["be_moved"] = True
                                    pos["be_pending"] = False
                                    print(f"[Lifecycle] 🛡️ +0.80R Erken Koruma Aktif: {pos['symbol']} Stop -> {be_p}")

                        # Aşama 2: Fiyat +1.50R'a ulaştıysa Stopu +0.75R'a taşı
                        if "TRAIL_075" in protection:
                            trail_sl = protection["TRAIL_075"]
                            if trail_sl > 0 and l2_live and self._is_sl_tightening(pos["direction"], float(l2_live.sl or 0.0), trail_sl):
                                mod_trail = self.mt5_executor.modify_position_sl(l2_tick, pos["symbol"], trail_sl, pos.get("leg2_tp", 0.0))
                                if mod_trail:
                                    pos["trailing_level"] = 0.75
                                    pos["be_moved"] = True
                                    self.notifier.send_alert(
                                        f"⚡ <b>İZ SÜREN STOP AKTİF (+0.75R):</b>\n"
                                        f"Fiyat +1.50R hedefini aştı! Kârı kilitlemek için #{l2_tick} Stopu <code>{trail_sl}</code> (+0.75R) seviyesine kilitlendi."
                                    )

                        # Aşama 3: Fiyat +2.20R'a ulaştıysa Stopu +1.40R'a taşı
                        if "TRAIL_140" in protection:
                            trail_sl2 = protection["TRAIL_140"]
                            if trail_sl2 > 0 and l2_live and self._is_sl_tightening(pos["direction"], float(l2_live.sl or 0.0), trail_sl2):
                                mod_trail2 = self.mt5_executor.modify_position_sl(l2_tick, pos["symbol"], trail_sl2, pos.get("leg2_tp", 0.0))
                                if mod_trail2:
                                    pos["trailing_level"] = 1.40
                                    self.notifier.send_alert(
                                        f"🔥 <b>KADEMELİ İZ SÜREN STOP (+1.40R):</b>\n"
                                        f"Fiyat +2.20R hedefini aştı! #{l2_tick} Stopu <code>{trail_sl2}</code> (+1.40R) seviyesine yükseltildi."
                                    )

                # Bacak 2 Kapanış Kontrolü
                if l2_tick and pos.get("leg2_open", True):
                    l2_pos = self.mt5_executor.get_open_position(l2_tick)
                    if l2_pos is None:
                        deal2 = self.mt5_executor.get_position_close_deal(l2_tick)
                        if deal2 is None:
                            pos["close_deal_pending"] = l2_tick
                            active_open_positions.append(pos)
                            continue
                        pos["leg2_open"] = False
                        pos.pop("close_deal_pending", None)
                        pos["leg2_close_evidence"] = deal2
                        profit2 = float(deal2.get("profit", 0.0)) if deal2 else 0.0
                        pos["leg2_profit"] = profit2
                        d_comm2 = deal2.get("comment", "") if deal2 else ""
                        total_profit = float(pos.get("leg1_profit", 0.0)) + profit2

                        if "[tp" in d_comm2.lower() or (deal2.get("reason") is not None and deal2["reason"] == mt5.DEAL_REASON_TP):
                            close_reason = "TP2_HIT"
                            badge = "🏆 <b>TP2 ANA HEDEF VURULDU (FULL WIN)!</b>"
                        elif pos.get("trailing_level", 0.0) > 0:
                            close_reason = "TRAILING_STOP"
                            badge = "⚡ <b>İZ SÜREN STOP İLE KÂRDA KAPANDI</b>"
                        elif pos.get("be_moved"):
                            close_reason = "BREAK_EVEN" if profit2 >= 0 else "PROTECTED_STOP_LOSS"
                            badge = "🛡️ <b>KALAN PARÇA KORUMA STOPUNDA KAPANDI</b>"
                        else:
                            close_reason = "STOP_LOSS"
                            badge = "🔴 <b>STOP LOSS TETİKLENDİ</b>"

                        is_closed = True
                        reference_r = float(pos.get("one_r_cash_at_entry") or one_r_cash)
                        pos["r_reference_verified"] = bool(pos.get("one_r_cash_at_entry"))
                        net_pnl_r = round(total_profit / reference_r, 2)
                        self.notifier.send_alert(
                            f"{badge}\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"🎯 <b>Sembol:</b> <code>{pos['symbol']}</code>\n"
                            f"💰 <b>Bacak 2 Net:</b> <b>${profit2:.2f}</b> | <b>Toplam Net:</b> <b>${total_profit:.2f}</b> ({net_pnl_r:+.2f}R)\n"
                            f"🏁 <b>Kapanış Nedeni:</b> <code>{close_reason}</code>"
                        )

                # Eğer iki bacak da kapandıysa veya tek emir açılmış ve kapandıysa işlem tamamen bitti
                has_leg2 = bool(pos.get("leg2_ticket"))
                leg1_closed = not pos.get("leg1_open", True)
                leg2_closed = (not pos.get("leg2_open", True)) if has_leg2 else True

                is_closed = leg1_closed and leg2_closed
                if is_closed:
                    reference_r = float(pos.get("one_r_cash_at_entry") or one_r_cash)
                    pos["r_reference_verified"] = bool(pos.get("one_r_cash_at_entry"))
                    net_pnl_r = round((float(pos.get("leg1_profit", 0.0)) + float(pos.get("leg2_profit", 0.0))) / reference_r, 2)
                    if not close_reason:
                        close_reason = "POSITION_CLOSED"

            else:
                # 2. SİMÜLASYON / GÖLGE (SHADOW) MODU
                sig_obj = stream_id_map.get(pos.get("signal_id"))
                if sig_obj and sig_obj.metadata.get("status") == "CLOSED":
                    is_closed = True
                    raw_r = float(sig_obj.raw_r_result or 0.0)
                    alloc_r = float(pos.get("allocated_risk_r", 1.0))
                    comm_fee_r = 0.05 * alloc_r
                    net_pnl_r = round((raw_r * alloc_r) - comm_fee_r, 2)
                    if raw_r >= 1.5:
                        close_reason = "TP2_HIT"
                    elif raw_r >= 0.5:
                        close_reason = "TP1_HIT"
                    elif -0.15 <= raw_r <= 0.15:
                        close_reason = "BREAK_EVEN"
                    else:
                        close_reason = "STOP_LOSS"
                elif pos.get("status") in ("CLOSED", "TP1_HIT", "TP2_HIT", "STOP_LOSS", "BREAK_EVEN"):
                    is_closed = True
                    close_reason = pos.get("close_reason") or pos.get("status")
                else:
                    exit_time_str = pos.get("exit_time") or (str(sig_obj.exit_time) if (sig_obj and sig_obj.exit_time) else None)
                    if exit_time_str:
                        try:
                            exit_dt = pd.to_datetime(exit_time_str, utc=True)
                            if exit_dt <= datetime.now(timezone.utc):
                                is_closed = True
                                raw_r = float(sig_obj.raw_r_result) if (sig_obj and sig_obj.raw_r_result) else float(pos.get("raw_r_result", 0.0))
                                alloc_r = float(pos.get("allocated_risk_r", 1.0))
                                comm_fee_r = 0.05 * alloc_r
                                net_pnl_r = round((raw_r * alloc_r) - comm_fee_r, 2)
                                if raw_r >= 1.5:
                                    close_reason = "TP2_HIT"
                                elif raw_r >= 0.5:
                                    close_reason = "TP1_HIT"
                                elif -0.15 <= raw_r <= 0.15:
                                    close_reason = "BREAK_EVEN"
                                else:
                                    close_reason = "STOP_LOSS"
                        except Exception:
                            pass
            if is_closed:
                # Update Arbiter loss-streak tracker
                self.arbiter.record_trade_outcome(pos.get("engine_name", "Unknown"), net_pnl_r)

                # Update Performance Summary and Counters
                perf = self.state["performance_summary"]
                perf["total_trades"] += 1
                if net_pnl_r > 0:
                    perf["wins"] += 1
                elif net_pnl_r < 0:
                    perf["losses"] += 1
                perf["net_r"] = round(perf.get("net_r", 0.0) + net_pnl_r, 2)

                # Update Daily Realized PnL
                self.state["daily_realized_pnl_r"] = round(self.state.get("daily_realized_pnl_r", 0.0) + net_pnl_r, 2)
                if net_pnl_r < 0:
                    self.state["daily_realized_loss_r"] = round(self.state.get("daily_realized_loss_r", 0.0) + abs(net_pnl_r), 2)

                pos["status"] = "CLOSED"
                pos["close_reason"] = close_reason
                pos["net_pnl_r"] = net_pnl_r
                pos["closed_at"] = datetime.now(timezone.utc).isoformat()
                self.state.setdefault("closed_trades_history", []).append(pos)

                # Send Telegram Trade Closure Alert with Net PnL and Scoreboard Counters
                self._dispatch_close_alert(pos, close_reason, net_pnl_r)
            else:
                active_open_positions.append(pos)

        open_positions = active_open_positions

        self.state["open_positions"] = open_positions
        self.save_state()
        return cycle_results

    def _dispatch_alert(self, signal: TradeSignal, decision: ArbiterDecision, risk_r: float, ticket: Optional[int] = None, twin_info: Optional[Dict[str, Any]] = None) -> None:
        """Sends formatted Telegram and Console notification for approved executions."""
        side_icon = "🟢 LONG" if signal.direction == "LONG" else "🔴 SHORT"
        boost_badge = "🔥 [1.30x ORTHOGONAL CONFLUENCE]" if decision.action == "CONFLUENCE_BOOST" else ""
        if decision.action == "COUNTER_TREND_MR_DAMPED":
            boost_badge = "⚠️ [0.50x COUNTER-TREND MR DAMPED]"
        elif decision.action == "CLONE_CAPPED":
            boost_badge = "🛡️ [1.00x CLONE RISK CAPPED]"

        mode_badge = "🚀 <b>GERÇEK CANLI İŞLEM AÇILDI (THE 5%ERS)</b>" if self.mode == "mt5_live" else "🧪 <b>GÖLGE İŞLEM AÇILDI (TEST)</b>"

        html_msg = (
            f"👑 <b>MEGALORD ORKESTRATÖR — YENİ İŞLEM</b>\n"
            f"{mode_badge}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Sembol:</b> <code>{signal.symbol}</code>\n"
            f"📈 <b>Yön:</b> {side_icon}\n"
            f"⚙️ <b>Motor:</b> <code>{signal.engine_name}</code> ({signal.math_family.value})\n"
            f"📐 <b>Rejim:</b> <code>{signal.strategy_regime.value}</code>\n"
            f"🛡️ <b>Tahsis Edilen Risk:</b> <b>{risk_r:.2f} R</b> (%{0.50 * risk_r:.2f} Kasa) {boost_badge}\n"
        )
        if twin_info and twin_info.get("is_split"):
            html_msg += (
                f"📦 <b>İkiz Emir Yapısı (Twin Orders):</b>\n"
                f"  • Bacak 1 (%50): <code>#{twin_info.get('leg1_ticket')}</code> | TP1: <code>{twin_info.get('leg1_tp')}</code>\n"
                f"  • Bacak 2 (%50): <code>#{twin_info.get('leg2_ticket')}</code> | TP2: <code>{twin_info.get('leg2_tp')}</code> (Runner)\n"
            )
        elif ticket:
            html_msg += f"🎫 <b>MT5 Bilet No:</b> <code>#{ticket}</code>\n"

        html_msg += (
            f"⚖️ <b>Arbiter Kararı:</b> <code>{decision.action}</code>\n"
            f"📝 <b>Açıklama:</b> <i>{decision.reason}</i>\n"
        )
        if decision.confluent_engines:
            html_msg += f"🤝 <b>Konfluens Motorları:</b> <code>{', '.join(decision.confluent_engines)}</code>\n"
        html_msg += (
            f"⏰ <b>Zaman (UTC):</b> <code>{signal.entry_time}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>GÜNCEL HESAP & PORTFÖY DURUMU:</b>\n"
            f"• Hedef Hesap: <b>#{self.state.get('account_id')} (The 5%ers)</b>\n"
            f"• Açık Pozisyon Sayısı: <b>{len(self.state.get('open_positions', []))}</b>\n"
            f"• Günlük Realize Zarar: <b>-{self.state.get('daily_realized_loss_r', 0.0):.2f} R</b> / -2.50 R Fren"
        )

        self.notifier.send_alert(html_msg)

    def _dispatch_close_alert(self, pos: Dict[str, Any], reason: str, net_r: float) -> None:
        """Sends formatted Telegram notification when a trade hits TP1, TP2, SL or BE."""
        perf = self.state.get("performance_summary", {})
        total = perf.get("total_trades", 0)
        wins = perf.get("wins", 0)
        losses = perf.get("losses", 0)
        wr = round((wins / total * 100), 1) if total > 0 else 0.0
        net_total_r = perf.get("net_r", 0.0)

        if reason in ("TP2_HIT", "TP1_HIT") or net_r > 0:
            badge = "🎯 <b>HEDEF GÖRÜLDÜ (TAKE PROFIT)</b>"
            icon = "🟢"
        elif reason == "BREAK_EVEN" or (-0.15 <= net_r <= 0.15):
            badge = "🛡️ <b>BAŞABAŞ KAPANDI (BREAK EVEN)</b>"
            icon = "⚪"
        else:
            badge = "🛑 <b>STOP LOSS TETİKLENDİ</b>"
            icon = "🔴"

        msg = (
            f"👑 <b>MEGALORD ORKESTRATÖR — POZİSYON KAPANDI</b>\n"
            f"{badge}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Sembol:</b> <code>{pos.get('symbol')}</code> ({pos.get('direction')})\n"
            f"⚙️ <b>Motor:</b> <code>{pos.get('engine_name')}</code>\n"
            f"📌 <b>Kapanış Nedeni:</b> <code>{reason}</code>\n"
            f"{icon} <b>Net Kâr/Zarar (Komisyon/Swap Dahil):</b> <b>{net_r:+.2f} R</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>CANLI SKOR & SAYAÇLAR:</b>\n"
            f"• Toplam İşlem: <b>{total}</b> | Kazanma Oranı: <b>%{wr}</b>\n"
            f"• Kazanan: <b>{wins}</b> | Kaybeden: <b>{losses}</b>\n"
            f"• Toplam Kasa Net: <b>{net_total_r:+.2f} R</b>\n"
            f"• Günlük Realize Net: <b>{self.state.get('daily_realized_pnl_r', 0.0):+.2f} R</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ <b>Kapanış Zamanı:</b> <code>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</code>"
        )
        self.notifier.send_alert(msg)

    def dispatch_periodic_scoreboard(self, force: bool = False) -> None:
        """Sends daily/periodic scoreboard status summary to Telegram."""
        now_utc = datetime.now(timezone.utc)
        last_report_str = self.state.get("last_telegram_scoreboard_at")
        if not force and last_report_str:
            try:
                last_dt = pd.to_datetime(last_report_str, utc=True)
                if (now_utc - last_dt).total_seconds() < 86400:  # 24 saatte bir periyodik rapor
                    return
            except Exception:
                pass

        perf = self.state.get("performance_summary", {})
        total = perf.get("total_trades", 0)
        wins = perf.get("wins", 0)
        losses = perf.get("losses", 0)
        wr = round((wins / total * 100), 1) if total > 0 else 0.0
        net_total_r = perf.get("net_r", 0.0)
        open_pos = self.state.get("open_positions", [])

        msg = (
            "📊 <b>MEGALORD 30 GUNLUK GOLGE TESTI — DURUM RAPORU</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ <b>Test Baslangici:</b> <code>{self.state.get('test_started_at', '2026-10-24')}</code>\n"
            f"📅 <b>Hedef Bitis:</b> <code>{self.state.get('test_ends_at', '2026-11-24')}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📈 <b>SKOR TABLOSU:</b>\n"
            f"• Toplam Islem: <b>{total}</b>\n"
            f"• Kazanc / Kayip: <b>{wins}W / {losses}L</b> (%{wr} Kazanma Orani)\n"
            f"• <b>Net Kasa Getirisi:</b> <b>{net_total_r:+.2f} R</b>\n"
            f"• Acik Pozisyon Sayisi: <b>{len(open_pos)}</b>\n"
            f"• Guvenlik Blokajlari: <b>{perf.get('firewall_blocks_count', 0)}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ <i>Mod: SHADOW PAPER (Gercek MT5 hesabi bagli degildir).</i>\n"
            f"🕒 <code>{now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}</code>"
        )
        if self.notifier.send_alert(msg):
            self.state["last_telegram_scoreboard_at"] = now_utc.isoformat()
            self.save_state()

    def print_dashboard(self) -> None:
        """Prints high-visibility executive command center status."""
        perf = self.state.get("performance_summary", {})
        open_pos = self.state.get("open_positions", [])
        total_open_risk = sum(
            0.0 if (p.get("be_moved") or float(p.get("trailing_level", 0.0)) > 0) else p.get("allocated_risk_r", 1.0)
            for p in open_pos
        )

        print("\n" + "=" * 80)
        print(f"👑 MEGALORD META-ALLOCATOR & ORCHESTRATOR COMMAND CENTER [Mode: {self.mode.upper()}]")
        print(f"Hedef Hesap: #{self.state.get('account_id')} | Gün: {self.state.get('current_date_utc')} | Zaman: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
        print("=" * 80)
        print(f"Günlük Gerçekleşen Kayıp : -{self.state.get('daily_realized_loss_r', 0.0):.2f} R / -2.50 R (Fren Limiti)")
        print(f"Açık Pozisyon Sayısı    : {len(open_pos)} | Toplam Açık Risk: {total_open_risk:.2f} R")
        print(f"Ortogonal Konfluens      : {perf.get('confluence_boosts_count', 0)} Kez Tetiklendi (1.30x Boost)")
        print(f"Engellenen / Vetolanan   : {perf.get('firewall_blocks_count', 0)} Firewall | {perf.get('vetoes_count', 0)} HTF Çatışma Veto")
        now_utc = datetime.now(timezone.utc)
        if self.arbiter.quality_filter.is_weekend_session(now_utc):
            print("-" * 80)
            print("⚡ HAFTASONU SEANSI AKTİF (Cuma 21:00 UTC - Pazar 21:00 UTC)")
            print("   Yalnızca 7/24 Kripto Pariteleri: XRPUSD, BTCUSD, ETHUSD, LTCUSD, SOLUSD, DOGUSD, BTCEUR, ETHEUR")
        print("-" * 80)
        print("AKTİF MOTOR MATRİSİ (FAZ 4 SAFRA TEMİZLİĞİ SONRASI):")
        engine_weights = self.config.get("allocator", {}).get("base_engine_weights", {})
        for eng, wt in engine_weights.items():
            status = "🟢 AKTİF (1.0x)" if wt >= 1.0 else (f"🟢 AKTİF ({wt:.2f}x)" if wt > 0 else "⚪ PASİF / PUSULA")
            print(f"  • {eng:<18} : Ağırlık = {wt:<5} | Durum = {status}")
        print("-" * 80)
        if open_pos:
            print("AÇIK POZİSYONLAR:")
            for p in open_pos[-5:]:
                dir_icon = "LONG " if p["direction"] == "LONG" else "SHORT"
                be_tag = " [BE/TRAIL KORUMALI]" if (p.get("be_moved") or float(p.get("trailing_level", 0.0)) > 0) else ""
                print(f"  [{p['engine_name']:<16}] {p['symbol']:<10} {dir_icon} | Risk: {p['allocated_risk_r']:.2f}R | {p['action']}{be_tag}")
        else:
            print("Açık pozisyon bulunmuyor (Tüm limitler güvende).")
        print("=" * 80 + "\n")


def parse_args():
    parser = argparse.ArgumentParser(description="Megalord Multi-Strategy Orchestrator Daemon")
    parser.add_argument("--mode", choices=["shadow", "mt5_live"], default="shadow", help="Execution mode (default: shadow)")
    parser.add_argument("--watch", action="store_true", help="Run 24/7 background loop")
    parser.add_argument("--interval-sec", type=int, default=15, help="Loop interval in seconds (default: 15)")
    parser.add_argument("--once", action="store_true", help="Execute single arbitration cycle and exit")
    return parser.parse_args()


def main():
    args = parse_args()
    daemon = MegalordDaemon(mode=args.mode)
    print(f"[Megalord Daemon] Başlatılıyor... Mod: {args.mode.upper()}")
    daemon.print_dashboard()

    if args.once or not args.watch:
        print("[Megalord Daemon] Tek döngü (--once) çalıştırılıyor...")
        results = daemon.execute_cycle()
        print(f"[Megalord Daemon] Döngü tamamlandı: {results['processed']} sinyal incelendi, {results['approved']} onaylandı, {results['blocked']} engellendi.")
        daemon.print_dashboard()
        return

    print(f"[Megalord Daemon] 24/7 Gözetim Döngüsü Devrede (Periyot: {args.interval_sec} sn). Çıkmak için Ctrl+C.")
    try:
        while True:
            res = daemon.execute_cycle()
            daemon.dispatch_periodic_scoreboard()
            if res["processed"] > 0:
                print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Döngü: +{res['approved']} Onay | -{res['blocked']} Blokaj")
                daemon.print_dashboard()
            time.sleep(args.interval_sec)
    except KeyboardInterrupt:
        print("\n[Megalord Daemon] Kullanıcı tarafından durduruldu. Durum kaydedildi.")
        daemon.save_state()


if __name__ == "__main__":
    main()
