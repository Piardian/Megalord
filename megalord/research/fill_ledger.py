"""Engine-neutral fill and cash ledger; never reconstructs fills from TP labels.

Cash amounts and initial risk must already be in the same declared account
currency. FX conversion is evidence supplied by the producer, not inferred here.
"""
from dataclasses import dataclass
from math import isfinite

from megalord.research.trade_contract import ENGINES, MeasuredTrade, utc_time

COMPONENTS = frozenset(("spread", "slippage", "commission", "carry", "conversion"))
TREATMENTS = frozenset(("in_fill_pnl", "cash_event", "bundled_cash", "not_applicable", "unknown"))


@dataclass(frozen=True)
class FillEvent:
    event_id: str
    kind: str
    occurred_at: object
    available_at: object
    quantity: float = 0.
    price: float | None = None
    gross_cash: float | None = None
    cost_cash: float | None = None
    component: str | None = None


class FillLedger:
    def __init__(self, *, engine, symbol, direction, initial_stop, initial_risk_cash,
                 account_currency, source_sha256, cost_treatment, cost_basis, cost_groups=None):
        if engine not in ENGINES or not symbol or direction not in ("LONG", "SHORT"):
            raise ValueError("Engine, symbol and direction required")
        if not all(isfinite(v) and v > 0 for v in (initial_stop, initial_risk_cash)):
            raise ValueError("Positive finite initial stop and initial account-currency risk required")
        if len(source_sha256) != 64 or any(c not in '0123456789abcdef' for c in source_sha256):
            raise ValueError("Source hash required")
        if not account_currency or not cost_basis.strip():
            raise ValueError("Explicit account currency and cost basis required")
        if not isinstance(cost_treatment, dict) or set(cost_treatment) != COMPONENTS or not set(cost_treatment.values()) <= TREATMENTS:
            raise ValueError("Every cost component needs an explicit treatment")
        groups = dict(cost_groups or {})
        members = []
        for group, components in groups.items():
            if not isinstance(group, str) or not group or group in COMPONENTS or not isinstance(components, list) or not components:
                raise ValueError("Explicit named nonempty cost group required")
            if not all(component in COMPONENTS for component in components):
                raise ValueError("Unknown bundled component")
            members.extend(components)
        if len(members) != len(set(members)) or set(members) != {component for component, treatment in cost_treatment.items() if treatment == "bundled_cash"}:
            raise ValueError("Bundled coverage must match distinct declared components exactly")
        self.engine, self.symbol, self.direction = engine, symbol, direction
        self.initial_stop, self.initial_risk_cash = initial_stop, initial_risk_cash
        self.account_currency, self.source_sha256 = account_currency, source_sha256
        self.cost_treatment, self.cost_basis = dict(cost_treatment), cost_basis
        self.cost_groups = groups
        self.events = []

    def append(self, event):
        occurred, available = utc_time(event.occurred_at), utc_time(event.available_at)
        if not event.event_id or any(old.event_id == event.event_id for old in self.events):
            raise ValueError("Unique nonempty event ID required")
        if available < occurred:
            raise ValueError("Event cannot be available before it happened")
        if self.events and (occurred < utc_time(self.events[-1].occurred_at)
                            or available < utc_time(self.events[-1].available_at)):
            raise ValueError("Out-of-order lifecycle events")
        if event.kind not in ("ENTRY", "EXIT", "COST"):
            raise ValueError("Unknown lifecycle event kind")
        state = self.summary()
        if event.kind in ("ENTRY", "EXIT"):
            if not isfinite(event.quantity) or event.quantity <= 0 or event.price is None or not isfinite(event.price) or event.price <= 0:
                raise ValueError("Fill needs positive finite quantity and price")
            if event.cost_cash is not None or event.component is not None:
                raise ValueError("Cash costs require their own explicit event")
            if event.kind == "ENTRY":
                if state["has_exits"] or event.gross_cash is not None:
                    raise ValueError("Entry after exit or entry with supplied profit is invalid")
                if (event.price - self.initial_stop) * (1 if self.direction == "LONG" else -1) <= 0:
                    raise ValueError("Initial stop must be adverse to actual entry")
            else:
                if event.quantity > state["open_quantity"] + 1e-10:
                    raise ValueError("Exit exceeds filled quantity")
                if event.gross_cash is None or not isfinite(event.gross_cash):
                    raise ValueError("Exit requires explicit gross account-currency cash PnL")
        else:
            if not state["entered_quantity"] or event.component not in COMPONENTS | self.cost_groups.keys():
                raise ValueError("Cost needs a filled trade and known component")
            if event.component not in self.cost_groups and self.cost_treatment[event.component] != "cash_event":
                raise ValueError("Cannot charge an embedded, unknown or inapplicable cost twice")
            if event.cost_cash is None or not isfinite(event.cost_cash):
                raise ValueError("Cost cash must be explicit and finite, including zero")
            if event.quantity != 0 or event.price is not None or event.gross_cash is not None:
                raise ValueError("Cash event cannot masquerade as a fill")
        self.events.append(event)

    def summary(self):
        entries = [event for event in self.events if event.kind == "ENTRY"]
        exits = [event for event in self.events if event.kind == "EXIT"]
        costs = [event for event in self.events if event.kind == "COST"]
        entered = sum(event.quantity for event in entries)
        gross = sum(event.gross_cash for event in exits)
        debits = sum(max(0., event.cost_cash) for event in costs)
        credits = sum(max(0., -event.cost_cash) for event in costs)
        return {"entered_quantity": entered, "open_quantity": entered - sum(event.quantity for event in exits),
                "has_exits": bool(exits), "price_gross_cash": gross, "debits_cash": debits,
                "credits_cash": credits, "net_cash": gross - debits + credits,
                "entry_vwap": sum(event.price * event.quantity for event in entries) / entered if entered else None}

    def closed_evidence(self):
        state = self.summary()
        if not state["entered_quantity"] or not state["has_exits"] or abs(state["open_quantity"]) > 1e-10:
            raise ValueError("Only fully closed filled trades enter the net-performance sample")
        if "unknown" in self.cost_treatment.values():
            raise ValueError("Unresolved cost coverage")
        documented = {event.component for event in self.events if event.kind == "COST"}
        if any(treatment == "cash_event" and component not in documented for component, treatment in self.cost_treatment.items()):
            raise ValueError("Declared cash costs need explicit evidence, including zero charges")
        if not set(self.cost_groups) <= documented:
            raise ValueError("Bundled cash costs need an explicit group event")
        entries = [event for event in self.events if event.kind == "ENTRY"]
        basis = (self.cost_basis + f"; currency={self.account_currency}; coverage={dict(sorted(self.cost_treatment.items()))}; bundles={self.cost_groups}; fill PnL includes declared embedded costs; "
                 "gross R includes separately recorded credits; credits are not amplified by cost stress; "
                 "exit timestamp is final net-event availability, not an invented intrabar fill time")
        return MeasuredTrade(
            self.engine, self.symbol, self.direction, utc_time(entries[0].occurred_at),
            max(utc_time(event.available_at) for event in self.events),
            state["entry_vwap"], self.initial_stop,
            (state["price_gross_cash"] + state["credits_cash"]) / self.initial_risk_cash,
            state["debits_cash"] / self.initial_risk_cash, self.source_sha256, basis,
            requires_native_cost_replay="in_fill_pnl" in self.cost_treatment.values(),
        )
