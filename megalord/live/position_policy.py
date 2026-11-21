"""Pure Megalord execution-plan/protection rules, shared by live and replay.

Constants reproduce the existing daemon policy; they are not tuned here.
Broker fills, rounding to a venue tick size, and acknowledgement are separate.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionPlan:
    entry: float
    stop: float
    tp1: float
    tp2: float
    risk_distance: float


def execution_plan(engine, symbol, direction, entry, stop, tp1, tp2):
    stop = stop or 0.
    fallback = .20 if "JPY" in symbol else .0020
    if stop <= 0:
        stop = entry - fallback if direction == "LONG" else entry + fallback
    risk = abs(entry - stop)
    if risk <= 0:
        risk = fallback
    first_distance = abs(tp1 - entry) if tp1 > 0 else 0.
    upper = risk * (1.05 if engine == "Begonya00" else 2.)
    if tp1 <= 0 or first_distance < risk or first_distance > upper:
        tp1 = stop_at_r(entry, risk, direction, 1.)
    second_distance = abs(tp2 - entry) if tp2 > 0 else 0.
    if tp2 <= 0 or second_distance < risk * 1.5:
        tp2 = stop_at_r(entry, risk, direction, 2.5)
    return ExecutionPlan(entry, stop, tp1, tp2, risk)


def stop_at_r(entry, risk_distance, direction, level):
    return round(entry + risk_distance * level * (1 if direction == "LONG" else -1), 5)


def protection_steps(entry, risk_distance, direction, mark, *, be_moved, trailing_level):
    if risk_distance <= 0 or entry <= 0:
        return {}
    favorable = (mark - entry) * (1 if direction == "LONG" else -1)
    steps = {}
    # 1R'de %50 Kâr Al + Breakeven
    if favorable >= risk_distance * 1.00 and not be_moved:
        steps["EARLY_BE"] = stop_at_r(entry, risk_distance, direction, .05)
    # 2R'de %25 Kâr Al + 1.00R Trailing Kilit
    if favorable >= risk_distance * 2.00 and trailing_level < 1.00:
        steps["TRAIL_100"] = stop_at_r(entry, risk_distance, direction, 1.00)
    elif favorable > risk_distance * 2.20:
        runner_trail = (favorable - risk_distance * 1.0) / risk_distance
        if runner_trail > trailing_level:
            steps["TRAIL_RUNNER"] = stop_at_r(entry, risk_distance, direction, runner_trail)
    return steps
