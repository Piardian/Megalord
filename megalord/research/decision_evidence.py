"""Archive and replay entry gates without passing future trade outcomes to them.

No broker, network, wall clock, or notifier calls. This is decision evidence,
not a fill simulator or a performance estimator.
"""
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.firewall.risk_firewall import MegalordRiskFirewall
from megalord.research.trade_contract import ENGINES, utc_time
from megalord.types import MathFamily, StrategyRegime, TradeSignal


SIGNAL_FIELDS = (
    "signal_id", "engine_name", "symbol", "direction", "entry_price",
    "sl_price", "tp1_price", "tp2_price", "risk_r", "sub_strategy",
)
# These are the metadata read by current quality/arbiter gates. Outcomes and
# arbitrary producer payloads must not enter decision replay by accident.
METADATA_FIELDS = (
    "grade", "status", "spread_price", "spread_points", "point",
    "current_price", "pool_type", "live_active", "quality_context", "htf_bias", "hurst",
)
POSITION_FIELDS = (
    "signal_id", "engine_name", "symbol", "direction", "math_family",
    "allocated_risk_r", "risk_r", "risk_mult", "is_breakeven", "be_moved", "trailing_level",
)
CONFIG_FIELDS = ("allocator", "signal_quality_filter", "weekend_trading", "firewall")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def gate_code_hashes():
    root = Path(__file__).resolve().parents[1]
    names = ("arbiter/signal_arbiter.py", "filters/signal_quality_filter.py",
             "firewall/risk_firewall.py", "types.py")
    return {name: sha256((root / name).read_bytes()).hexdigest() for name in names}


def capture_entry_context(config, candidate, *, decided_at, active_positions,
                          daily_realized_loss_r, loss_streaks, macro_bias):
    """Capture the exact in-memory gate inputs; no future outcomes are copied.

    available_at attests to when the caller supplied this snapshot, not to the
    point-in-time correctness of the upstream market/macro data itself.
    """
    now = utc_time(decided_at)
    origin = utc_time(candidate.entry_time)
    if candidate.exit_time is not None:
        raise ValueError("Completed outcome is not an entry snapshot")
    if candidate.engine_name not in ENGINES:
        raise ValueError("Only the six trading engines enter this audit")
    if origin > now:
        raise ValueError("Signal origin is later than decision availability")
    if daily_realized_loss_r < 0:
        raise ValueError("Daily realized loss must be nonnegative")
    signal = {field: deepcopy(getattr(candidate, field)) for field in SIGNAL_FIELDS}
    signal.update(entry_time=origin.isoformat(), math_family=candidate.math_family.value,
                  strategy_regime=candidate.strategy_regime.value,
                  metadata={k: deepcopy(v) for k, v in (candidate.metadata or {}).items() if k in METADATA_FIELDS})
    cfg = {k: deepcopy(config[k]) for k in CONFIG_FIELDS if k in config}
    record = {
        "schema_version": 1, "source_kind": "entry_gate_snapshot",
        "gate_code_sha256": gate_code_hashes(),
        "available_at": now.isoformat(), "config": cfg,
        "config_sha256": sha256(canonical(cfg).encode()).hexdigest(),
        "signal": signal,
        "active_positions": [{k: deepcopy(p[k]) for k in POSITION_FIELDS if k in p} for p in active_positions],
        "daily_realized_loss_r": daily_realized_loss_r,
        "engine_loss_streaks": deepcopy(loss_streaks), "macro_bias": macro_bias,
        "upstream_causality_verified": False,
    }
    return json.loads(canonical(record))


def replay_entry_context(record):
    """Run actual quality, arbiter, recorded news result, and actual firewall.

    News classification is supplied as observed evidence, not fetched from a
    present-day calendar. Gate rejection does not require later-stage inputs.
    """
    if record.get("schema_version") != 1 or record.get("source_kind") != "entry_gate_snapshot":
        raise ValueError("Explicit entry gate snapshot required")
    if record.get("gate_code_sha256") != gate_code_hashes():
        raise ValueError("Gate source changed; replay requires the recorded source revision")
    cfg = record["config"]
    if sha256(canonical(cfg).encode()).hexdigest() != record["config_sha256"]:
        raise ValueError("Configuration fingerprint mismatch")
    now = utc_time(record["available_at"])
    sig = deepcopy(record["signal"])
    if set(sig) - set(SIGNAL_FIELDS) - {"entry_time", "math_family", "strategy_regime", "metadata"}:
        raise ValueError("Unexpected signal fields; future outcomes are forbidden")
    if set(sig.get("metadata", {})) - set(METADATA_FIELDS):
        raise ValueError("Unexpected decision metadata")
    sig["entry_time"] = utc_time(sig["entry_time"])
    if sig["entry_time"] > now or sig["engine_name"] not in ENGINES:
        raise ValueError("Invalid signal origin or engine")
    sig["math_family"] = MathFamily(sig["math_family"])
    sig["strategy_regime"] = StrategyRegime(sig["strategy_regime"])
    candidate = TradeSignal(**sig, exit_time=None, raw_r_result=0.0)
    arbiter = MegalordSignalArbiter(deepcopy(cfg))
    arbiter.engine_loss_streaks = deepcopy(record["engine_loss_streaks"])
    positions = deepcopy(record["active_positions"])
    if any(set(p) - set(POSITION_FIELDS) for p in positions):
        raise ValueError("Unexpected position fields; future outcomes are forbidden")
    if record["daily_realized_loss_r"] < 0:
        raise ValueError("Daily realized loss must be nonnegative")
    decision = arbiter.evaluate_signal(candidate, positions,
        macro_htf_bias=record["macro_bias"], now_utc=now, allow_historical=False)
    result = {"arbiter": asdict(decision), "allowed": decision.allowed,
              "stage": "ARBITER", "action": decision.action, "reason": decision.reason}
    if not decision.allowed:
        return result
    news = record.get("news_check")
    if not isinstance(news, dict) or type(news.get("is_frozen")) is not bool:
        raise ValueError("Observed news result required; missing does not mean unfrozen")
    if utc_time(news["available_at"]) < now:
        raise ValueError("News check predates this decision snapshot")
    if news["is_frozen"]:
        result.update(allowed=False, stage="NEWS", action="NEWS_GUARD_BLOCKED", reason=news.get("reason"))
        return result
    ok, reason = MegalordRiskFirewall(cfg).evaluate_entry(candidate, decision.final_risk_mult,
        record["daily_realized_loss_r"], positions)
    result.update(allowed=ok, stage="FIREWALL", action="APPROVED" if ok else "FIREWALL_BLOCKED", reason=reason)
    return result


def finish_entry_context(record, *, decision, news_check=None, news_available_at=None,
                         firewall_result=None):
    """Freeze the actual result alongside input, then verify it in isolation."""
    row = deepcopy(record)
    row["observed_arbiter"] = asdict(decision)
    if news_check is not None:
        row["news_check"] = {"is_frozen": news_check["is_frozen"], "reason": news_check.get("reason"),
                             "available_at": utc_time(news_available_at).isoformat()}
    if decision.allowed and news_check is None:
        raise ValueError("Allowed arbiter requires observed subsequent news stage")
    if not decision.allowed:
        row["observed_allowed"], row["observed_stage"] = False, "ARBITER"
    elif news_check["is_frozen"]:
        row["observed_allowed"], row["observed_stage"] = False, "NEWS"
    else:
        if firewall_result is None:
            raise ValueError("Unfrozen entry requires observed firewall result")
        row["observed_allowed"], row["observed_stage"] = bool(firewall_result[0]), "FIREWALL"
        row["observed_firewall_reason"] = firewall_result[1]
    replayed = replay_entry_context(row)
    row["replayed"] = replayed
    row["gate_parity_equal"] = parity_equal(row, replayed)
    row["live_approved"] = False
    return json.loads(canonical(row))


def parity_equal(record, replayed=None):
    """Recompute parity; do not trust a stored success flag or cached replay."""
    replayed = replay_entry_context(record) if replayed is None else replayed
    # Native sets can have different iteration order in different processes.
    def comparable(dec):
        value = deepcopy(dec)
        for key in ("confluent_engines", "confluent_families"):
            value[key] = sorted(value[key], key=str)
        return value
    return (
        comparable(replayed["arbiter"]) == comparable(record["observed_arbiter"])
        and replayed["allowed"] == record["observed_allowed"]
        and replayed["stage"] == record["observed_stage"]
        and (record["observed_stage"] != "FIREWALL" or replayed["reason"] == record["observed_firewall_reason"]))


def append_entry_evidence(path, record):
    """Write one complete JSON line. Caller decides how archive failures behave."""
    target = Path(path)
    line = canonical(record) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)


def audit_entry_archive(path):
    """Check each line, including retries; reject partial or corrupted records."""
    target = Path(path)
    report = {"path": str(target), "exists": target.is_file(), "records": 0,
        "parity_equal": 0, "mismatches": [], "invalid": [],
        "engines": {name: {"records": 0, "allowed": 0, "unique_signals": 0} for name in ENGINES},
        "live_approved": False, "performance_evaluated": False}
    if not target.is_file():
        return report
    raw = target.read_bytes()
    report["sha256"] = sha256(raw).hexdigest()
    identities = {name: set() for name in ENGINES}
    for line_no, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        report["records"] += 1
        try:
            record = json.loads(line)
            equal = parity_equal(record)
            engine, identity = record["signal"]["engine_name"], record["signal"]["signal_id"]
            report["engines"][engine]["records"] += 1
            report["engines"][engine]["allowed"] += int(record["observed_allowed"])
            identities[engine].add(identity)
            if equal:
                report["parity_equal"] += 1
            else:
                report["mismatches"].append({"line": line_no, "signal_id": identity})
        except (ValueError, TypeError, KeyError) as error:
            report["invalid"].append({"line": line_no, "reason": str(error)})
    for name in ENGINES:
        report["engines"][name]["unique_signals"] = len(identities[name])
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_entry_archive(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    print(f"Records: {result['records']}; parity: {result['parity_equal']}; invalid: {len(result['invalid'])}; no performance claim")
