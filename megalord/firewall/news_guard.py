"""The 5%ers & Institutional Economic Calendar News Guard.

Strictly protects prop-firm accounts from high-impact red folder news events.
The 5%ers High Stakes Rule:
- Opening orders is PROHIBITED 2 minutes before and 2 minutes after high-impact news.
- Megalord Conservative Buffer: We freeze execution 5 minutes before and 5 minutes after.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request

logger = logging.getLogger("Megalord.NewsGuard")

DEFAULT_CONFIG_PATH = Path(r"C:\Users\piard\Desktop\MegalordProject\config\news_guard_config.json")


class MegalordNewsGuard:
    """Detects upcoming or ongoing high-impact economic news events and freezes trade executions."""

    def __init__(self, config_path: Path | str = DEFAULT_CONFIG_PATH):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self._last_refresh_utc: Optional[datetime] = None

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Could not load news guard config: {e}")
        return {
            "default_freeze_minutes_before": 5,
            "default_freeze_minutes_after": 5,
            "scheduled_events": []
        }

    def save_config(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(json.dumps(self.config, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save news guard config: {e}")

    def fetch_live_forexfactory_events(self) -> List[Dict[str, Any]]:
        """Optionally refreshes high-impact red folder calendar from standard public economic feeds."""
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    red_events = []
                    for item in data:
                        if item.get("impact") in ("High", "Holiday") or "CPI" in item.get("title", "") or "NFP" in item.get("title", "") or "FOMC" in item.get("title", "") or "Interest Rate" in item.get("title", ""):
                            red_events.append({
                                "id": f"{item.get('country')}_{item.get('title')}",
                                "name": item.get("title"),
                                "currency": item.get("country"),
                                "impact": "CRITICAL" if item.get("impact") == "High" else "HIGH",
                                "event_time_utc": item.get("date"),
                                "affects_all_symbols": item.get("country") in ("USD", "EUR") and ("Rate" in item.get("title", "") or "CPI" in item.get("title", "") or "NFP" in item.get("title", ""))
                            })
                    if red_events:
                        self.config["scheduled_events"] = red_events
                        self.save_config()
                        return red_events
        except Exception as e:
            logger.debug(f"Live calendar fetch skipped: {e}")
        return self.config.get("scheduled_events", [])

    def check_news_freeze(self, symbol: str) -> Dict[str, Any]:
        """
        Evaluates whether the candidate symbol is currently inside a red-folder news freeze window.
        Returns:
            {
                "is_frozen": bool,
                "reason": str,
                "event_name": Optional[str],
                "minutes_away": Optional[float]
            }
        """
        now_utc = datetime.now(timezone.utc)
        if self._last_refresh_utc is None or (now_utc - self._last_refresh_utc).total_seconds() > 21600:
            self._last_refresh_utc = now_utc
            self.fetch_live_forexfactory_events()
        events = self.config.get("scheduled_events", [])
        freeze_before = self.config.get("default_freeze_minutes_before", 5)
        freeze_after = self.config.get("default_freeze_minutes_after", 5)

        clean_sym = symbol.replace("/", "").replace("-", "").replace("_", "").upper()

        for ev in events:
            ev_time_str = ev.get("event_time_utc")
            if not ev_time_str:
                continue

            try:
                # Support standard ISO or Z format
                clean_time_str = ev_time_str.replace("Z", "+00:00")
                if "+" not in clean_time_str and "-" not in clean_time_str[-6:]:
                    ev_time = datetime.fromisoformat(clean_time_str).replace(tzinfo=timezone.utc)
                else:
                    ev_time = datetime.fromisoformat(clean_time_str)

                # Check if this currency/symbol is affected
                affects_all = ev.get("affects_all_symbols", False)
                ev_curr = str(ev.get("currency", "USD")).upper()

                symbol_affected = affects_all or (ev_curr in clean_sym)
                if not symbol_affected:
                    continue

                c_before = ev.get("freeze_minutes_before", freeze_before)
                c_after = ev.get("freeze_minutes_after", freeze_after)

                window_start = ev_time - timedelta(minutes=c_before)
                window_end = ev_time + timedelta(minutes=c_after)

                if window_start <= now_utc <= window_end:
                    diff_min = round((ev_time - now_utc).total_seconds() / 60.0, 1)
                    phase = "ÖNCESİ" if diff_min > 0 else "SONRASI"
                    diff_abs = abs(diff_min)
                    return {
                        "is_frozen": True,
                        "event_name": ev.get("name", "Kritik Haber"),
                        "currency": ev_curr,
                        "minutes_away": diff_abs,
                        "phase": phase,
                        "reason": f"🛑 The 5%ers Kırmızı Haber Yasağı: {ev.get('name')} ({ev_curr}) {diff_abs} dk {phase}. Yeni emir açılamaz!"
                    }
            except Exception:
                continue

        return {
            "is_frozen": False,
            "event_name": None,
            "currency": None,
            "minutes_away": 0.0,
            "phase": None,
            "reason": "Haber penceresi temiz. Giriş yapılabilir."
        }
