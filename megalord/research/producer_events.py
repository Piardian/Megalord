"""Observe the two Python SMC paper trackers and convert CLT native outcomes.

These are MODEL fills, never reconstructed user/broker executions. Missing venue
costs stay unknown. Observation methods do not change producer decisions.
"""
from copy import deepcopy
from dataclasses import asdict

from megalord.research.fill_ledger import FillEvent, FillLedger
from megalord.research.trade_contract import utc_time


UNKNOWN_COSTS = {"spread": "unknown", "slippage": "unknown", "commission": "unknown",
                 "carry": "unknown", "conversion": "not_applicable"}


class PaperEventObserver:
    def __init__(self, tracker, engine, source_hash):
        if engine not in ("Begonya", "Begonya00"):
            raise ValueError("Explicit native Python SMC producer required")
        self.tracker, self.engine, self.source_hash = tracker, engine, source_hash
        self.rows, self.ledgers, self.completed, self.unfilled = {}, {}, [], []
        self.reconciliation = []

    def _start(self, trade, when):
        identity = trade["id"]
        if identity in self.rows:
            return
        risk = abs(float(trade["entry"]) - float(trade["original_sl"]))
        coverage = dict(UNKNOWN_COSTS)
        groups = None
        if self.engine == "Begonya00":
            coverage.update(commission="bundled_cash", slippage="bundled_cash")
            groups = {"native_fee": ["commission", "slippage"]}
        basis = ("Native paper model, one quote-price unit; no broker lot/FX validation. "
                 "Observed modeled fills and partial exits, not user trades. "
                 "Spread penetration is an eligibility condition, not a separately measured spread PnL charge.")
        ledger = FillLedger(engine=self.engine, symbol=trade["symbol"], direction=trade["direction"],
            initial_stop=float(trade["original_sl"]), initial_risk_cash=risk,
            account_currency="NATIVE_MODEL_PRICE_UNIT", source_sha256=self.source_hash,
            cost_treatment=coverage, cost_groups=groups, cost_basis=basis)
        self.rows[identity] = {"trade_id": identity, "engine": self.engine, "symbol": trade["symbol"],
            "direction": trade["direction"], "initial_stop": ledger.initial_stop,
            "initial_risk_cash": risk, "account_currency": ledger.account_currency,
            "cost_treatment": coverage, "cost_groups": groups, "cost_basis": basis,
            "events": [], "fill_model": trade.get("fill_model", "NATIVE_REGISTERED_OPEN_MODEL"),
            "time_precision": trade.get("fill_time_precision", "OBSERVER_CLOCK"),
            "order_created_at": trade.get("opened_at")}
        self.ledgers[identity] = ledger
        self._emit(identity, "ENTRY", when, quantity=1., price=float(trade["entry"]))

    def _emit(self, identity, kind, when, **values):
        ledger, row = self.ledgers[identity], self.rows[identity]
        event = FillEvent(str(len(row["events"]) + 1), kind, utc_time(when).isoformat(),
                          utc_time(when).isoformat(), **values)
        ledger.append(event)
        row["events"].append(asdict(event))

    def _exit(self, identity, price, quantity, when):
        ledger = self.ledgers[identity]
        cash = (float(price) - ledger.summary()["entry_vwap"]) * quantity * (1 if ledger.direction == "LONG" else -1)
        self._emit(identity, "EXIT", when, quantity=quantity, price=float(price), gross_cash=cash)

    def register(self, signal, observed_at, *args, **kwargs):
        now = utc_time(observed_at)
        before = {t["id"] for t in self.tracker.active_trades}
        result = self.tracker.register_trade(signal, *args, **kwargs)
        for trade in self.tracker.active_trades:
            if trade["id"] not in before and trade["status"] == "OPEN":
                self._start(trade, now)
        return result

    def step(self, symbol, bar, observed_at):
        now = utc_time(observed_at)
        prior = {t["id"]: deepcopy(t) for t in self.tracker.active_trades}
        already_closed = {t["id"] for t in self.tracker.closed_trades}
        result = self.tracker.check_trade_progress(symbol, bar)
        fresh_closed = [t for t in self.tracker.closed_trades if t["id"] not in already_closed]
        for trade in [*self.tracker.active_trades, *fresh_closed]:
            identity = trade["id"]
            if identity not in prior or trade["symbol"] != symbol:
                continue
            if identity not in self.rows and trade.get("filled_at"):
                if prior[identity].get("status") != "PENDING_LIMIT":
                    raise ValueError("Observer attached after a fill; original entry is not reconstructible")
                self._start(trade, utc_time(trade["filled_at"]))
            if identity not in self.rows:
                if trade in fresh_closed:
                    self.unfilled.append({"trade_id": identity, "reason": trade.get("close_reason")})
                elif trade["status"] != "PENDING_LIMIT":
                    raise ValueError("Observer attached after a fill; original entry is not reconstructible")
                continue
            ledger = self.ledgers[identity]
            partial_done = ledger.summary()["open_quantity"] < 1.
            if not partial_done and (trade["status"] in ("TP1_HIT", "TRAILING_ACTIVE") or trade.get("close_reason") == "TP2_HIT"):
                self._exit(identity, trade["tp1"], .5, now)
            if trade in fresh_closed:
                reason = trade["close_reason"]
                if "exit_price" in trade:
                    price = trade["exit_price"]
                elif reason == "TP2_HIT":
                    price = trade["tp2"]
                elif reason in ("STOP_LOSS", "BREAK_EVEN", "TRAILING_STOP"):
                    price = prior[identity]["current_sl"]
                else:
                    raise ValueError(f"Unobserved native exit price for {reason}")
                self._exit(identity, price, ledger.summary()["open_quantity"], now)
                if self.engine == "Begonya00":
                    if "fee_r" not in trade:
                        raise ValueError("Native fee evidence missing")
                    self._emit(identity, "COST", now, component="native_fee",
                               cost_cash=float(trade["fee_r"]) * ledger.initial_risk_cash)
                state = ledger.summary()
                gross = state["price_gross_cash"] / ledger.initial_risk_cash
                native_gross = float(trade.get("gross_r", trade["r_result"]))
                native_net = trade.get("net_r")
                net = state["net_cash"] / ledger.initial_risk_cash
                tolerance = 5.1e-7 if self.engine == "Begonya00" else .005000001
                self.reconciliation.append({"trade_id": identity, "gross_r": gross,
                    "native_gross_r": native_gross, "difference_gross_r": gross - native_gross,
                    "observed_model_net_r": net, "native_net_r": native_net,
                    "reconciled": abs(gross - native_gross) <= tolerance and
                        (native_net is None or abs(net - float(native_net)) <= tolerance)})
                self.completed.append(self.rows[identity])
        return result

    def document(self):
        return {"schema_version": 1, "source_kind": "paper", "trades": deepcopy(self.completed),
            "native_reconciliation": deepcopy(self.reconciliation), "unfilled": deepcopy(self.unfilled),
            "open_trade_ids": sorted(set(self.rows) - {r["trade_id"] for r in self.completed}),
            "broker_cost_coverage_proven": False, "live_approved": False}


def checklist_events(candidate, outcome, source_hash, *, bar_minutes):
    """Translate explicit native CLT entry/exit fields; never infer from TP labels."""
    import pandas as pd
    if outcome["status"] != "COMPLETED" or outcome.get("entryTriggeredAt") is None:
        raise ValueError("Closed filled native outcome required")
    if not isinstance(bar_minutes, int) or bar_minutes <= 0:
        raise ValueError("Explicit positive candle duration required")
    entry, exit_price = float(outcome["entryPrice"]), float(outcome["exitPrice"])
    plan = outcome["plan"]
    risk = abs(entry - float(plan["stopPrice"]))
    sign = 1 if candidate["tradeDirection"] == "long" else -1
    if abs(risk - float(plan["riskDistance"])) > max(1e-10, risk * 1e-9):
        raise ValueError("Native initial price geometry mismatch")
    gross = sign * (exit_price - entry)
    if abs(gross / risk - float(outcome["rrAchieved"])) > 1e-8:
        raise ValueError("Native price outcome mismatch")
    entry_time = utc_time(outcome["entryTriggeredAt"], unit="ms")
    exit_time = utc_time(outcome["outcome"]["timestamp"], unit="ms")
    row = {"trade_id": candidate.get("signalId") or candidate.get("uniqueKey"),
        "parent_source_sha256": source_hash,
        "engine": "ChecklistTrigger", "symbol": candidate["symbol"], "direction": candidate["tradeDirection"].upper(),
        "initial_stop": plan["stopPrice"], "initial_risk_cash": risk, "account_currency": "NATIVE_MODEL_PRICE_UNIT",
        "cost_treatment": dict(UNKNOWN_COSTS),
        "cost_basis": "Native OHLC gross model, candle-label occurrence and close availability; no verified venue costs",
        "events": [
            {"event_id": "entry", "kind": "ENTRY", "occurred_at": entry_time.isoformat(),
             "available_at": (entry_time + pd.Timedelta(minutes=bar_minutes)).isoformat(), "quantity": 1., "price": entry},
            {"event_id": "exit", "kind": "EXIT", "occurred_at": exit_time.isoformat(),
             "available_at": (exit_time + pd.Timedelta(minutes=bar_minutes)).isoformat(),
             "quantity": 1., "price": exit_price, "gross_cash": gross}]}
    if not row["trade_id"]:
        raise ValueError("Stable native signal ID required")
    return row


def broker_position_events(summary, *, trade_id, engine, symbol, direction,
                           initial_stop, initial_risk_cash, account_currency, source_hash,
                           observed_at, conversion_treatment):
    """Convert complete broker position deals for ANY of the six engines.

    observed_at is export/capture availability, not an invented historical
    receipt timestamp. Venue cash statement charges outside these deals need
    separate reconciliation before whole-account net profitability is claimed.
    """
    from megalord.live.deal_accounting import summarize_closed_position
    checked = summarize_closed_position(summary["deals"], summary["position_id"])
    if checked is None:
        raise ValueError("Complete balanced broker deal history required")
    if conversion_treatment not in ("in_fill_pnl", "not_applicable", "unknown"):
        raise ValueError("Explicit account-currency conversion treatment required")
    available = utc_time(observed_at)
    treatments = {"spread": "in_fill_pnl", "slippage": "in_fill_pnl", "commission": "cash_event",
                  "carry": "cash_event", "conversion": conversion_treatment}
    ledger = FillLedger(engine=engine, symbol=symbol, direction=direction,
        initial_stop=initial_stop, initial_risk_cash=initial_risk_cash, account_currency=account_currency,
        source_sha256=source_hash, cost_treatment=treatments,
        cost_basis="Broker position-deal cash in declared account currency; commission includes explicit broker fee. "
                   "Spread/slippage already in fills. Export availability does not prove original receipt latency. "
                   "Separate account-level charges are not certified by this position ledger.")
    events = []
    for d in sorted(checked["deals"], key=lambda d: (d.get("time_msc", d["time"] * 1000), d["ticket"])):
        when = utc_time(d["time_msc"], unit="ms") if d.get("time_msc") else utc_time(d["time"], unit="s")
        if when > available:
            raise ValueError("Broker deal postdates its capture")
        kind = "ENTRY" if d["entry"] == 0 else "EXIT"
        if kind == "ENTRY" and d["profit"] != 0:
            raise ValueError("Entry with nonzero cash profit requires explicit attribution")
        ev = FillEvent(str(d["ticket"]) + ":fill", kind, when.isoformat(), available.isoformat(),
            quantity=d["volume"], price=d["price"], gross_cash=d["profit"] if kind == "EXIT" else None)
        ledger.append(ev); events.append(asdict(ev))
        for component, cash in (("commission", -d["commission"] - d["fee"]), ("carry", -d["swap"])):
            ev = FillEvent(str(d["ticket"]) + ":" + component, "COST", when.isoformat(), available.isoformat(),
                           component=component, cost_cash=cash)
            ledger.append(ev); events.append(asdict(ev))
    if abs(ledger.summary()["net_cash"] - checked["profit"]) > 1e-8:
        raise ValueError("Broker cash reconciliation mismatch")
    return {"trade_id": trade_id, "engine": engine, "symbol": symbol, "direction": direction,
            "initial_stop": initial_stop, "initial_risk_cash": initial_risk_cash,
            "account_currency": account_currency, "cost_treatment": treatments,
            "cost_basis": ledger.cost_basis, "events": events,
            "parent_source_sha256": source_hash, "unattributed_account_charges_covered": False}
