"""Read-only six-engine source inventory; never runs strategy or broker code.

Usage: python -m megalord.research.inventory --output <new JSON file>
Field counts are schema observations, not proof of correctness or net edge.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from megalord.adapters.stream_loader import ProjectStreamLoader


def inspect_source(path):
    path = Path(path)
    result = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return result
    raw = path.read_bytes()
    result.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as error:
        result["parse_error"] = str(error)
        return result
    result["top_level_type"] = type(payload).__name__
    lists = {"root": payload} if isinstance(payload, list) else {
        key: value for key, value in payload.items() if isinstance(value, list)
    } if isinstance(payload, dict) else {}
    # Begonya stores its period-specific samples one object deeper.
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, dict) and isinstance(value.get("trades"), list):
                lists[f"{key}.trades"] = value["trades"]
    result["record_sets"] = {}
    for name, rows in lists.items():
        records = [row for row in rows if isinstance(row, dict)]
        keys = sorted(set().union(*(row.keys() for row in records))) if records else []
        result["record_sets"][name] = {
            "count": len(rows), "object_count": len(records), "fields": keys,
            "symbols": sorted({str(row["symbol"]) for row in records if row.get("symbol")}),
            "missing_explicit_exit_time": sum(not row.get("exit_time") for row in records),
            "numeric_millisecond_timestamp": sum(
                isinstance(row.get("timestamp"), (int, float))
                and 1e12 <= row["timestamp"] < 1e14 for row in records),
            "checklist_imputed_tp_r_candidates": sum(
                (row.get("actualOutcome") or row.get("outcome")) == "TP"
                and (row.get("realizedR") or row.get("r_multiple", 0)) == 0
                for row in records),
        }
    return result


def build_inventory():
    loader = ProjectStreamLoader()  # Only declares paths; no load_* calls.
    specs = {
        "Trawma": ("paper_and_cache", [loader.trawma_paper_path, loader.trawma_cache_path]),
        "Pragma": ("live_signal_and_cache", [loader.pragma_live_path, loader.pragma_cache_path]),
        "Stigma": ("paper_and_cache", [loader.stigma_live_path, loader.stigma_cache_path]),
        "Begonya00": ("active_and_ledger", [loader.begonya00_active_path, loader.begonya00_path]),
        "ChecklistTrigger": ("historical_audit_with_adapter_assumptions", [loader.checklist_path]),
        "Begonya": ("historical_comparison_archive", [loader.begonya_path]),
    }
    config_path = Path(__file__).resolve().parents[2] / "config" / "megalord_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    toggles = config.get("allocator", {}).get("engine_execution_enabled", {})
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "source evidence inventory, not a performance evaluation",
        "live_approved": False,
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "scope": "all six engines, all recorded symbols; Convexity is a separate macro input",
        "limitations": [
            "Field-name counts do not decode alternative exit fields or validate fills.",
            "Cost, signal causality, cross-engine independence and live parity remain unproven.",
            "Current execution toggles describe configuration, not actual process activity.",
        ],
        "engines": {name: {
            "source_mode": mode,
            "configured_execution_enabled": toggles.get(name),
            "sources": [inspect_source(path) for path in paths],
        } for name, (mode, paths) in specs.items()},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, ensure_ascii=False, allow_nan=False)
    print(f"Inventoried {len(report['engines'])} engines: {args.output}")
