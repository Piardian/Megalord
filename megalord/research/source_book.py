"""Six-engine research import preserving missing evidence instead of inventing it.

This is an observation book, not a broker fill ledger. Native R values with
unknown embedded costs never become certified net R.
"""
from collections import Counter
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path

import pandas as pd

from megalord.adapters.stream_loader import ProjectStreamLoader, norm_direction, norm_symbol
from megalord.research.trade_contract import ENGINES, utc_time


def first(row, *keys):
    for key in keys:
        if key in row and row[key] is not None and row[key] != "":
            return row[key]
    return None


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
        return parsed if isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def observation(engine, row, source_hash, index, *, legacy_timezone=None):
    if engine not in ENGINES:
        raise ValueError("Unknown engine")
    issues = []
    time_notes = []

    def time_value(value, field):
        if value is None:
            issues.append(f"missing_{field}")
            return None
        try:
            # Only Checklist's documented timestamp field is epoch milliseconds.
            if engine == "ChecklistTrigger" and isinstance(value, (int, float)):
                result = utc_time(value, unit="ms")
            else:
                parsed = pd.Timestamp(value)
                if parsed.tzinfo is None and legacy_timezone:
                    parsed = parsed.tz_localize(legacy_timezone)
                    time_notes.append(f"{field}:explicit_legacy_timezone={legacy_timezone}")
                result = utc_time(parsed)
            return result.isoformat()
        except (ValueError, TypeError, OverflowError):
            issues.append(f"invalid_or_ambiguous_{field}")
            return None

    entry = time_value(first(row, "filled_at", "entry_time", "opened_at", "timestamp"), "entry_time")
    exit = time_value(first(row, "exit_time", "closed_at", "exitTimestamp"), "exit_time")
    direction = norm_direction(first(row, "direction", "side", "type", "tradeDirection") or "")
    price = number(first(row, "entry_price", "entry", "entryPrice"))
    stop = number(first(row, "initial_stop", "original_sl", "initial_stop_loss", "sl_price", "stopPrice"))
    native_r = number(first(row, "realizedR", "realized_r", "r_multiple", "raw_r_result", "r_result"))
    gross = number(row.get("gross_r"))
    fee = number(row.get("fee_r"))
    net = number(row.get("net_r"))
    if native_r is None:
        issues.append("missing_native_outcome")
    if direction not in ("LONG", "SHORT"):
        issues.append("invalid_direction")
    if price is None or stop is None or min(price, stop) <= 0:
        issues.append("missing_initial_geometry")
    elif (price - stop) * (1 if direction == "LONG" else -1) <= 0:
        issues.append("invalid_initial_stop")
    if entry and exit and pd.Timestamp(exit) < pd.Timestamp(entry):
        issues.append("exit_precedes_entry")
    if engine == "Begonya":
        # The archive producer writes opened_at when an order is created,
        # including PENDING_LIMIT, and never records its later fill timestamp.
        issues.extend(["entry_fill_time_unrecorded", "exit_bar_availability_unverified"])
    if gross is not None and fee is not None and net is not None:
        if fee < 0 or abs(gross - fee - net) > 1e-5:
            issues.append("inconsistent_cost_identity")
    else:
        issues.append("missing_cost_decomposition")
    # A fee field or arithmetic identity does not certify spread, slippage,
    # swaps and commission against the actual venue and fill methodology.
    issues.append("full_venue_cost_coverage_unverified")
    return {
        "engine": engine, "record_id": str(first(row, "signal_id", "tradeId", "id") or index),
        "symbol": norm_symbol(row.get("symbol", "")), "direction": direction,
        "entry_time": entry, "exit_time": exit, "entry_price": price, "initial_stop": stop,
        "native_r": native_r, "reported_gross_r": gross, "reported_cost_r": fee,
        "reported_net_r": net, "source_sha256": source_hash,
        "status": first(row, "status", "actualOutcome", "outcome"),
        "time_notes": time_notes, "issues": sorted(set(issues)),
        "net_performance_eligible": False,
    }


def source_specs():
    loader = ProjectStreamLoader()
    return {
        "Trawma": [(loader.trawma_paper_path, "root")],
        "Pragma": [(loader.pragma_cache_path, "symbol_lists")],
        "Stigma": [(loader.stigma_cache_path, "root")],
        "Begonya00": [(loader.begonya00_path, "root")],
        "ChecklistTrigger": [(loader.checklist_path, "root")],
        "Begonya": [(loader.begonya_path, "one_year")],
    }


def import_sources(specs=None):
    records, manifest = [], []
    for engine, sources in (specs or source_specs()).items():
        for path, layout in sources:
            path = Path(path)
            if not path.is_file():
                manifest.append({"engine": engine, "path": str(path), "missing": True})
                continue
            raw = path.read_bytes()
            digest = sha256(raw).hexdigest()
            data = json.loads(raw.decode("utf-8-sig"))
            if layout == "one_year":
                rows = data.get("3. SON 1 YIL", {}).get("trades", [])
            elif layout == "symbol_lists":
                rows = [row for value in data.values() if isinstance(value, list) for row in value]
            else:
                rows = data if isinstance(data, list) else data.get("audit_trades", [])
            manifest.append({"engine": engine, "path": str(path), "sha256": digest,
                             "layout": layout, "records": len(rows)})
            for index, row in enumerate(rows):
                # Legacy Begonya00 local timestamps are explicitly documented
                # by sfp_orb_engine/execution_policy.py (TRT, UTC+3).
                timezone = {"Begonya00": "Etc/GMT-3", "Begonya": "UTC"}.get(engine)
                # Begonya's archive producer converts MT5 epoch seconds with
                # utc=True before formatting without offsets. This declaration
                # is source-specific, never a default for other naive records.
                records.append(observation(engine, row, digest, index, legacy_timezone=timezone))
    coverage = {}
    for engine in ENGINES:
        rows = [row for row in records if row["engine"] == engine]
        coverage[engine] = {
            "records": len(rows),
            "with_entry_and_exit": sum(bool(row["entry_time"] and row["exit_time"]) for row in rows),
            "with_native_outcome": sum(row["native_r"] is not None for row in rows),
            "net_eligible": sum(row["net_performance_eligible"] for row in rows),
            "issues": dict(Counter(issue for row in rows for issue in row["issues"])),
        }
    return {"sources": manifest, "coverage": coverage, "observations": records,
            "live_approved": False, "purpose": "source reconciliation, not performance ranking"}
