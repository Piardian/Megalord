"""Import declared fill events from any of the six producers without broker I/O.

This validates accounting and declared coverage, not the truth of a producer's
fills or expenses. Native/paper evidence remains labelled as such.
"""
import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from megalord.research.fill_ledger import FillEvent, FillLedger


def import_event_document(raw):
    document = json.loads(raw)
    source_hash = sha256(raw).hexdigest()
    if not isinstance(document, dict) or document.get("schema_version") != 1 or document.get("source_kind") not in ("native_replay", "paper", "broker_export"):
        raise ValueError("Explicit event schema version and evidence source kind required")
    if not isinstance(document.get("trades"), list):
        raise ValueError("Trade event list required")
    closed, rejected = [], []
    counts = Counter(row.get("trade_id") for row in document["trades"]
                     if isinstance(row, dict) and isinstance(row.get("trade_id"), str))
    duplicates = {trade_id for trade_id, count in counts.items() if count > 1}
    seen = set()
    for index, row in enumerate(document["trades"]):
        if not isinstance(row, dict):
            rejected.append({"trade_id": None, "record_index": index, "reason": "Trade record must be an object"})
            continue
        trade_id = row.get("trade_id")
        try:
            if not isinstance(trade_id, str) or not trade_id or trade_id in seen:
                raise ValueError("Unique nonempty trade ID required")
            if trade_id in duplicates:
                raise ValueError("Duplicate trade ID; all conflicting copies rejected")
            seen.add(trade_id)
            ledger = FillLedger(**{key: row[key] for key in (
                "engine", "symbol", "direction", "initial_stop", "initial_risk_cash",
                "account_currency", "cost_treatment", "cost_basis")}, source_sha256=source_hash,
                cost_groups=row.get("cost_groups"))
            for event in row["events"]:
                ledger.append(FillEvent(**event))
            evidence = asdict(ledger.closed_evidence())
            evidence["entry_time"], evidence["exit_time"] = evidence["entry_time"].isoformat(), evidence["exit_time"].isoformat()
            closed.append({"trade_id": trade_id, "source_kind": document["source_kind"], "evidence": evidence})
        except (ValueError, TypeError, KeyError) as error:
            rejected.append({"trade_id": trade_id, "record_index": index, "reason": str(error)})
    return {"source_sha256": source_hash, "source_kind": document["source_kind"], "closed": closed,
            "rejected": rejected, "duplicate_trade_ids": sorted(duplicates), "live_approved": False,
            "scope": "Fill/cash accounting and declared coverage validation; not proof of broker fills or strategy edge"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = import_event_document(args.input.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, indent=2, ensure_ascii=False, allow_nan=False)
    print(f"Closed: {len(result['closed'])}; rejected: {len(result['rejected'])}; live approval: false")
