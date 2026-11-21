"""Cost-explicit offline closed-outcome replay on one account's risk budget.

Uses supplied outcomes, not tick/fill simulation. Costs must already match the
gross accounting basis; slippage included in gross fills must not be charged twice.
"""
from dataclasses import dataclass
from math import isfinite

from megalord.research.trade_contract import ENGINES, MeasuredTrade, utc_time


@dataclass(frozen=True)
class ReplayTrade:
    trade_id: str
    evidence: MeasuredTrade


def replay(trades, *, engines=ENGINES, initial_equity=10000., risk_fraction=.005,
           max_open_risk_fraction=.01, cost_multiplier=1., allow_same_instant=False):
    engines = tuple(engines)
    if not engines or len(set(engines)) != len(engines) or not set(engines) <= set(ENGINES):
        raise ValueError("Explicit unique engine set required")
    if not all(isfinite(v) for v in (initial_equity, risk_fraction, max_open_risk_fraction, cost_multiplier)):
        raise ValueError("Finite account parameters required")
    if initial_equity <= 0 or not 0 < risk_fraction <= max_open_risk_fraction < 1 or cost_multiplier < 1:
        raise ValueError("Invalid account risk or cost stress")
    if len({trade.trade_id for trade in trades}) != len(trades):
        raise ValueError("Duplicate trade IDs")
    selected = [trade for trade in trades if trade.evidence.engine in engines]
    if cost_multiplier > 1 and any(trade.evidence.requires_native_cost_replay for trade in selected):
        raise ValueError("Embedded fill costs require native producer re-execution; cash-cost scaling would omit them")
    # Tie-breaking is fixed by ID, never by future outcome or engine score.
    events = []
    for trade in selected:
        item = trade.evidence
        if item.exit_time < item.entry_time or (item.exit_time == item.entry_time and not allow_same_instant):
            raise ValueError("Strictly positive holding time required for account replay")
        close_phase = 2 if item.exit_time == item.entry_time else 0
        events.extend([(item.entry_time, 1, trade.trade_id, trade),
                       (item.exit_time, close_phase, trade.trade_id, trade)])
    events.sort(key=lambda event: event[:3])
    equity, peak, max_dd = initial_equity, initial_equity, 0.
    active, accepted, rejected, closes = {}, [], [], []
    for timestamp, kind, trade_id, trade in events:
        if kind in (0, 2):
            if trade_id not in active:
                continue
            capital_risk = active.pop(trade_id)
            pnl = capital_risk * (trade.evidence.gross_r - cost_multiplier * trade.evidence.cost_r)
            equity += pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
            closes.append({"trade_id": trade_id, "exit_time": timestamp.isoformat(),
                           "pnl": pnl, "equity": equity, "capital_risk": capital_risk})
        else:
            risk = equity * risk_fraction
            if equity <= 0 or sum(active.values()) + risk > equity * max_open_risk_fraction + 1e-10:
                rejected.append({"trade_id": trade_id, "reason": "shared_open_risk_budget"})
                continue
            active[trade_id] = risk
            accepted.append(trade_id)
    return {"scope": "cost_explicit_closed_outcome_replay", "live_approved": False,
            "engines": engines, "initial_equity": initial_equity, "ending_equity": equity,
            "same_instant_policy": ("All simultaneous entries reserve risk before instant exits settle" if allow_same_instant else "Rejected"),
            "net_pnl": equity - initial_equity, "max_closed_equity_drawdown": max_dd,
            "limitations": ["No mark-to-market drawdown or broker fill simulation.",
                            "Costs and gross outcomes are supplied evidence, not independently verified here.",
                            "No daily-loss rule or currency cluster allocation in this neutral baseline."],
            "accepted_ids": accepted, "rejected": rejected, "closes": closes}


def comparison_family(trades, *, window_start, window_end, **kwargs):
    """13 fixed comparisons. Never silently drop a missing engine."""
    start, end = utc_time(window_start), utc_time(window_end)
    if end <= start:
        raise ValueError("Invalid common comparison window")
    if any(trade.evidence.entry_time < start or trade.evidence.exit_time > end for trade in trades):
        raise ValueError("Input trades must fit the declared common comparison window")
    missing = sorted(set(ENGINES) - {trade.evidence.engine for trade in trades})
    if missing:
        raise ValueError(f"Six-engine comparison missing measured evidence: {missing}")
    results = {f"solo:{engine}": replay(trades, engines=(engine,), **kwargs) for engine in ENGINES}
    results["all_six"] = replay(trades, **kwargs)
    for engine in ENGINES:
        results[f"without:{engine}"] = replay(trades, engines=tuple(e for e in ENGINES if e != engine), **kwargs)
    return results
