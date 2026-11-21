"""Read-only, temporary observers of native producers in an isolated worker.

Prices and cash are native per-unit quote-price model units, not broker lots
or real account dollars. Model reconciliation never certifies venue costs.
"""
import ast
from contextlib import contextmanager
from dataclasses import asdict
import inspect
import textwrap

import pandas as pd

from megalord.research.fill_ledger import FillEvent, FillLedger


class NativeTrace:
    def __init__(self, engine, symbol, source_hash, case_id, minutes):
        self.engine, self.symbol, self.source_hash = engine, symbol, source_hash
        self.case_id, self.minutes = case_id, minutes
        self.trades, self.reconciliations = [], []
        self.ledger = None
        self.row = None
        self.counter = 0

    def start(self, direction, entry, stop, risk, time, treatment, basis, groups=None):
        if self.ledger is not None:
            raise ValueError("Native trace attempted overlapping positions")
        basis = "NATIVE MODEL ONLY; quote-price PnL per one model unit; no account FX/lot conversion. " + basis
        self.ledger = FillLedger(engine=self.engine, symbol=self.symbol, direction=direction,
            initial_stop=float(stop), initial_risk_cash=float(risk), account_currency="NATIVE_MODEL_PRICE_UNIT",
            source_sha256=self.source_hash, cost_treatment=treatment, cost_basis=basis, cost_groups=groups)
        self.row = {"trade_id": f"{self.case_id}:{len(self.trades)}", "engine": self.engine,
            "symbol": self.symbol, "direction": direction, "initial_stop": float(stop),
            "initial_risk_cash": float(risk), "account_currency": "NATIVE_MODEL_PRICE_UNIT",
            "cost_treatment": treatment, "cost_basis": basis, "events": []}
        if groups:
            self.row["cost_groups"] = groups
        self.counter = 0
        self.emit("ENTRY", time, quantity=1., price=float(entry))

    def emit(self, kind, time, **kwargs):
        self.counter += 1
        timestamp = pd.Timestamp(time).isoformat()
        event = FillEvent(str(self.counter), kind, timestamp, timestamp, **kwargs)
        self.ledger.append(event)
        self.row["events"].append(asdict(event))

    def exit(self, time, price, quantity):
        entry = self.ledger.summary()["entry_vwap"]
        sign = 1 if self.ledger.direction == "LONG" else -1
        self.emit("EXIT", time, price=float(price), quantity=float(quantity),
                  gross_cash=sign * (float(price) - entry) * float(quantity))

    def finish(self, native_r, time, *, tolerance=1e-9):
        # Native models with no charge during the holding window explicitly
        # supply zero for declared cash components, never for unknown venue costs.
        documented = {event.component for event in self.ledger.events if event.kind == "COST"}
        for component, treatment in self.ledger.cost_treatment.items():
            if treatment == "cash_event" and component not in documented:
                self.emit("COST", time, component=component, cost_cash=0.)
        evidence = self.ledger.closed_evidence()
        delta = evidence.net_r - float(native_r)
        if abs(delta) > tolerance:
            raise ValueError(f"Native/event accounting mismatch {self.case_id}: {delta}")
        self.reconciliations.append({"trade_id": self.row["trade_id"], "native_r": float(native_r),
                                     "ledger_r": evidence.net_r, "difference_r": delta})
        self.trades.append(self.row)
        self.ledger, self.row = None, None

    def document(self):
        return {"schema_version": 1, "source_kind": "native_replay", "trades": self.trades,
                "native_reconciliation": self.reconciliations,
                "limitations": ["Native model accounting, not verified broker costs or fills.",
                                "Price-unit cash is used only to recover dimensionless R.",
                                "Actual account lot sizing, FX conversion and external macro parity are unproven."],
                "live_approved": False}


@contextmanager
def observe_stigma(module, trace, costs):
    fill, advance, finish = module.fill_next_open, module.advance_position, module.finish_position

    def partial(pos, time):
        if pos["tp1_hit"] and trace.ledger.summary()["open_quantity"] > .5 + 1e-10:
            trace.exit(time, pos["tp1_exit_price"], .5)

    def wrapped_fill(signal, candle, **kwargs):
        position = fill(signal, candle, **kwargs)
        treatment = {key: "cash_event" if key in costs else "not_applicable"
                     for key in ("spread", "slippage", "commission", "carry", "conversion")}
        trace.start(position["side"], position["entry"], position["initial_stop_loss"],
                    position["initial_risk"], candle["time"], treatment,
                    "Source spread/commission/slippage price charges at close; source model has no additional carry.")
        return position

    def wrapped_finish(pos, price, reason, time):
        result = finish(pos, price, reason, time)
        available = pd.Timestamp(time) + pd.Timedelta(minutes=0 if "gap" in reason or reason == "weekend_close" else trace.minutes)
        partial(pos, available)
        trace.exit(available, price, trace.ledger.summary()["open_quantity"])
        for component, cost in costs.items():
            trace.emit("COST", available, component=component, cost_cash=float(cost))
        trace.finish(result["return_r"], available)
        return result

    def wrapped_advance(pos, candle, **kwargs):
        result = advance(pos, candle, **kwargs)
        if result is None:
            partial(pos, pd.Timestamp(candle["time"]) + pd.Timedelta(minutes=trace.minutes))
        return result

    module.fill_next_open, module.advance_position, module.finish_position = wrapped_fill, wrapped_advance, wrapped_finish
    try:
        yield
    finally:
        module.fill_next_open, module.advance_position, module.finish_position = fill, advance, finish


@contextmanager
def observe_pragma(module, trace):
    constructor, price_function, funding, manage = module._OpenTrade, module._adverse_price, module._apply_funding, module._manage_open_trade

    def new_position(*args, **kwargs):
        pos = constructor(*args, **kwargs)
        treatment = {"spread": "in_fill_pnl", "slippage": "in_fill_pnl", "commission": "in_fill_pnl",
                     "carry": "cash_event", "conversion": "not_applicable"}
        trace.start(pos.direction, pos.entry_price, pos.initial_stop_loss, pos.initial_risk,
                    pos.entry_time + pd.Timedelta(minutes=1), treatment,
                    "Native adverse-price aggregate already embedded in fill PnL. Additional modeled funding separately observed; omitted venue costs remain unverified.")
        return pos

    def adverse(*args, **kwargs):
        result = price_function(*args, **kwargs)
        context = inspect.currentframe().f_back
        if context.f_code is manage.__code__:
            values = context.f_locals
            pos = values["position"]
            if not pos.tp1_taken and args[0] == pos.tp1 and pos.tp1 != pos.tp2:
                quantity = pos.tp1_close_pct
            else:
                quantity = trace.ledger.summary()["open_quantity"]
            trace.exit(values["timestamp"] + pd.Timedelta(minutes=1), result, quantity)
        elif context.f_code.co_name == "run_backtest" and not bool(args[2]):
            trace.exit(context.f_locals["final_time"] + pd.Timedelta(minutes=1), result,
                       trace.ledger.summary()["open_quantity"])
        return result

    def apply_funding(pos, timestamp):
        old = pos.funding_cost_r
        funding(pos, timestamp)
        trace.emit("COST", timestamp + pd.Timedelta(minutes=1), component="carry",
                   cost_cash=(pos.funding_cost_r - old) * pos.initial_risk)

    def manager(pos, bar, timestamp, *args, **kwargs):
        result = manage(pos, bar, timestamp, *args, **kwargs)
        if result is not None:
            trace.finish(result.r_multiple, timestamp + pd.Timedelta(minutes=1))
        return result

    module._OpenTrade, module._adverse_price, module._apply_funding, module._manage_open_trade = new_position, adverse, apply_funding, manager
    try:
        yield
    finally:
        module._OpenTrade, module._adverse_price, module._apply_funding, module._manage_open_trade = constructor, price_function, funding, manage


class InlineTrawmaHooks:
    def __init__(self, trace):
        self.trace = trace

    def entry(self, context):
        pos = context["pos"]
        treatment = {"spread": "not_applicable", "slippage": "not_applicable", "commission": "cash_event",
                     "carry": "cash_event", "conversion": "not_applicable"}
        self.trace.start(pos.side, pos.entry, pos.stop, pos.risk, context["df"].index[pos.entry_index], treatment,
                         "Source fee and funding rates retained. Source model omits additional spread/slippage; this is not a complete broker cost model.")

    def partial(self, context):
        pos, trace = context["pos"], self.trace
        quantity = trace.ledger.summary()["open_quantity"] - pos.qty_remaining
        if quantity > 1e-10:
            time = context["ts"] + pd.Timedelta(minutes=trace.minutes)
            trace.exit(time, pos.tp1, quantity)
            trace.emit("COST", time, component="commission", cost_cash=context["fee"] * (pos.entry + pos.tp1) * quantity)

    def carry(self, context):
        pos, trace = context["pos"], self.trace
        trace.emit("COST", context["ts"] + pd.Timedelta(minutes=trace.minutes), component="carry",
                   cost_cash=context["funding_rate"] * pos.entry * pos.qty_remaining)

    def close(self, context):
        pos, trace, trade = context["pos"], self.trace, context["trades"][-1]
        reason = trade["exit_reason"]
        price = pos.stop if reason == "SL" else pos.tp2 if reason == "TP2" else context["exit_price"]
        time = context["ts"] + pd.Timedelta(minutes=trace.minutes)
        quantity = trace.ledger.summary()["open_quantity"]
        trace.exit(time, price, quantity)
        trace.emit("COST", time, component="commission", cost_cash=context["fee"] * (pos.entry + price) * quantity)
        trace.finish(trade["net_r"], time)


def instrument_inline_function(function, hooks):
    """Insert observation calls after existing statements; retain trading AST.

    No price/branch expression is replaced. Only the returned isolated function
    uses hooks; the source module function and file remain untouched.
    """
    source = textwrap.dedent(inspect.getsource(function))
    tree = ast.parse(source)
    counts = {name: 0 for name in ("entry", "partial", "carry", "close")}

    class Instrument(ast.NodeTransformer):
        def visit(self, node):
            node = super().visit(node)
            if not isinstance(node, ast.stmt):
                return node
            method = None
            if isinstance(node, ast.Assign):
                if any(isinstance(target, ast.Name) and target.id == "pos" for target in node.targets) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "Position":
                    method = "entry"
                elif any(isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "pos" and target.attr == "qty_remaining" for target in node.targets):
                    method = "partial"
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Attribute) and node.target.attr == "funding_events":
                method = "carry"
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == "trades" and node.value.func.attr == "append":
                method = "close"
            if method:
                counts[method] += 1
                extra = ast.parse(f"_native_observer_hooks.{method}(locals())").body[0]
                return [node, extra]
            return node

    transformed = Instrument().visit(tree)
    if any(count == 0 for count in counts.values()):
        raise ValueError(f"Native inline layout changed; observer refuses incomplete capture: {counts}")
    namespace = dict(function.__globals__, _native_observer_hooks=hooks)
    exec(compile(ast.fix_missing_locations(transformed), inspect.getsourcefile(function), "exec"), namespace)
    return namespace[function.__name__], counts
