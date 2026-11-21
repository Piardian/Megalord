"""Bounded historical-outcome diagnostic, explicitly NOT a full system backtest.

Uses the current allocator configuration with recorded native outcomes. It
cannot recreate missing entry decisions, broker costs, or the current exits.
"""
from collections import Counter
from math import isfinite

from megalord.research.trade_contract import ENGINES, utc_time
from megalord.simulator.master_engine import MegalordSimulator


def recorded_month(signals, *, config_path, start, end):
    start, end = utc_time(start), utc_time(end)
    if end <= start:
        raise ValueError("Positive explicit comparison window required")
    ids = [s.signal_id for s in signals]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate signal IDs require reconciliation before replay")
    for sig in signals:
        utc_time(sig.entry_time)
        if sig.exit_time is not None and utc_time(sig.exit_time) < utc_time(sig.entry_time):
            raise ValueError("Exit precedes entry")
        if not isfinite(sig.raw_r_result):
            raise ValueError("Finite recorded outcome required")
    in_window = [s for s in signals if start <= utc_time(s.entry_time) < end]
    # Unknown exits are not secretly turned into flat or winning trades. The
    # restricted closed-record sample is explicitly reported as incomplete.
    closed_records = [s for s in in_window if s.exit_time is not None]
    unresolved = [s.signal_id for s in in_window if s.exit_time is None]
    sim = MegalordSimulator(config_path)
    result = sim.run_orchestration(closed_records)
    accepted = result["executed_trades"]
    realized = sorted([s for s in accepted if utc_time(s["exit_time"]) <= end],
                      key=lambda s: (utc_time(s["exit_time"]), s["signal_id"]))
    values = [r["effective_r"] for r in realized]
    positive, negative = sum(v for v in values if v > 0), -sum(v for v in values if v < 0)
    equity = peak = drawdown = 0.
    for value in values:
        equity += value; peak = max(peak, equity); drawdown = max(drawdown, peak - equity)
    breakdown = {}
    for engine in ENGINES:
        rows = [t for t in realized if t["engine_name"] == engine]
        breakdown[engine] = {
            "configured_enabled": bool(sim.config["allocator"]["engine_execution_enabled"].get(engine, False)),
            "loaded_month_records": sum(s.engine_name == engine for s in in_window),
            "accepted_closed_by_cutoff": len(rows),
            "positive": sum(t["effective_r"] > 0 for t in rows),
            "negative": sum(t["effective_r"] < 0 for t in rows),
            "zero": sum(t["effective_r"] == 0 for t in rows),
            "allocated_recorded_r": sum(t["effective_r"] for t in rows),
            "full_scan_coverage_proven": False,
        }
    accepted_ids = {t["signal_id"] for t in accepted}
    geometry_missing = [s.signal_id for s in closed_records if s.signal_id in accepted_ids and
                        (s.entry_price <= 0 or s.sl_price <= 0 or s.entry_price == s.sl_price)]
    return {
        "status": "DIAGNOSTIC_ONLY_FULL_BACKTEST_UNAVAILABLE",
        "window_start": start.isoformat(), "window_end_exclusive_for_entries": end.isoformat(),
        "start_state": "FLAT_AT_START_CONDITIONAL_DIAGNOSTIC",
        "known_carry_in_excluded": sum(utc_time(s.entry_time) < start and
            (s.exit_time is None or utc_time(s.exit_time) > start) for s in signals),
        "loaded_records": len(signals), "month_records": len(in_window),
        "closed_records_replayed": len(closed_records), "unknown_exit_ids_excluded": unresolved,
        "accepted_records": len(accepted), "realized_by_cutoff": len(realized),
        "accepted_not_closed_by_cutoff": len(accepted) - len(realized),
        "positive": sum(v > 0 for v in values), "negative": sum(v < 0 for v in values),
        "zero": sum(v == 0 for v in values),
        "allocated_recorded_r": sum(values), "positive_fraction": sum(v > 0 for v in values) / len(values) if values else None,
        "recorded_outcome_profit_factor": positive / negative if negative else None,
        "closed_record_drawdown_r": drawdown,
        "expectancy_allocated_recorded_r": sum(values) / len(values) if values else None,
        "per_engine": breakdown,
        "rejections": dict(Counter(r["action"] for r in result["rejected_trades"])),
        "accepted_missing_entry_geometry_ids": geometry_missing,
        "trades_closed_by_cutoff": realized, "rejected_records": result["rejected_trades"],
        "historical_quality_bypass": True, "current_exit_policy_replayed": False,
        "complete_venue_costs_proven": False, "account_equity_drawdown_measured": False,
        "macro_policy": "Recorded signal metadata only; missing historical gates are not invented from current macro",
        "all_six_full_backtest_complete": False, "net_profitability_proven": False, "live_approved": False,
    }
