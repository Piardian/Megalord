"""Strict evidence contract for measured trades, separate from live adapters.

No inferred exit times, default winning R, implicit epoch units, or unmeasured
costs are allowed into a net-performance sample.
"""
from dataclasses import dataclass
from math import isfinite

import pandas as pd

ENGINES = ("Trawma", "Pragma", "Stigma", "Begonya00", "ChecklistTrigger", "Begonya")


def utc_time(value, *, unit=None):
    if value is None or value == "":
        raise ValueError("Timestamp evidence missing")
    if isinstance(value, (int, float)) and unit not in ("s", "ms", "us", "ns"):
        raise ValueError("Numeric timestamps require an explicit epoch unit")
    timestamp = pd.to_datetime(value, unit=unit, utc=True) if unit else pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError("Timestamp must have an explicit timezone")
    return timestamp.tz_convert("UTC")


@dataclass(frozen=True)
class MeasuredTrade:
    engine: str
    symbol: str
    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    initial_stop: float
    gross_r: float
    cost_r: float
    source_sha256: str
    cost_basis: str
    requires_native_cost_replay: bool = False

    def __post_init__(self):
        if self.engine not in ENGINES:
            raise ValueError("Unknown research engine")
        if not self.symbol or self.direction not in ("LONG", "SHORT"):
            raise ValueError("Invalid symbol or direction")
        entry, exit = utc_time(self.entry_time), utc_time(self.exit_time)
        if exit < entry:
            raise ValueError("Exit precedes entry")
        object.__setattr__(self, "entry_time", entry)
        object.__setattr__(self, "exit_time", exit)
        if not all(isfinite(v) for v in (self.entry_price, self.initial_stop, self.gross_r, self.cost_r)):
            raise ValueError("Prices, outcome and costs must be measured finite numbers")
        if self.entry_price <= 0 or self.initial_stop <= 0 or self.cost_r < 0:
            raise ValueError("Invalid prices or costs")
        if (self.entry_price - self.initial_stop) * (1 if self.direction == "LONG" else -1) <= 0:
            raise ValueError("Initial stop is on the wrong side")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise ValueError("Raw evidence SHA256 required")
        if not self.cost_basis.strip():
            raise ValueError("Explicit cost accounting basis required")

    @property
    def net_r(self):
        return self.gross_r - self.cost_r
