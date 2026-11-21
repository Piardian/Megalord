"""Quote-driven twin-leg model using the production execution/protection rules.

Requires ordered executable bid/ask. OHLC-derived paths must be explicitly
labelled synthetic by their caller. Broker acknowledgement, lot rounding and
latency are not certified by this model. Unknown fees never produce net R.
"""
from dataclasses import dataclass
from math import isfinite

from megalord.live.position_policy import execution_plan, protection_steps, stop_at_r
from megalord.research.trade_contract import utc_time


@dataclass(frozen=True)
class Quote:
    time: object
    bid: float
    ask: float

    def validate(self):
        utc_time(self.time)
        if not all(isfinite(x) and x > 0 for x in (self.bid, self.ask)) or self.ask < self.bid:
            raise ValueError('Valid positive executable bid/ask required')


class TwinPosition:
    def __init__(self, *, engine, symbol, direction, quote, stop, tp1, tp2):
        quote.validate()
        if direction not in ('LONG', 'SHORT'):
            raise ValueError('LONG or SHORT required')
        entry = quote.ask if direction == 'LONG' else quote.bid
        sign = 1 if direction == 'LONG' else -1
        # Research does not invoke the production fallback for missing risk.
        if not isfinite(stop) or stop <= 0 or sign * (entry-stop) <= 0:
            raise ValueError('Original stop geometry required; cannot infer missing risk')
        self.plan = execution_plan(engine, symbol, direction, entry, stop, tp1, tp2)
        self.symbol, self.direction, self.sign = symbol, direction, sign
        self.entry_time = utc_time(quote.time)
        self.last_time = self.entry_time
        self.stops = [self.plan.stop, self.plan.stop]
        self.targets = [self.plan.tp1, self.plan.tp2]
        if any(sign*(target-entry) <= 0 for target in self.targets):
            raise ValueError('Targets must be on profit side')
        self.open = [True, True]
        self.be_moved = False
        self.trailing_level = 0.
        self.events = []

    def _tighten(self, leg, price):
        if self.open[leg] and self.sign*(price-self.stops[leg]) > 0:
            self.stops[leg] = price

    def _close(self, leg, price, reason, time):
        self.open[leg] = False
        self.events.append(dict(leg=leg+1, time=utc_time(time).isoformat(), price=float(price),
            quantity=.5, reason=reason,
            gross_r=.5*self.sign*(price-self.plan.entry)/self.plan.risk_distance))

    def advance(self, quote, *, poll=True):
        quote.validate()
        time = utc_time(quote.time)
        if time < self.last_time:
            raise ValueError('Quote time went backwards')
        self.last_time = time
        mark = quote.bid if self.direction == 'LONG' else quote.ask
        # Broker-resident orders execute before the next daemon poll. A stop
        # crossed by a gap fills at the actual quote; TP limit fills at target.
        for i in range(2):
            if not self.open[i]: continue
            if self.sign*(mark-self.stops[i]) <= 0:
                self._close(i, mark, 'STOP', time)
            elif self.sign*(mark-self.targets[i]) >= 0:
                self._close(i, self.targets[i], 'TP', time)
        if not poll or not any(self.open): return
        index = any(s in self.symbol.upper() for s in ('NAS100','US100','SPX','SP500','GER40'))
        eod = time.hour < 1 or time.hour >= 21 or time.hour == 20 and time.minute >= 45
        if index and eod:
            for i in range(2):
                if self.open[i]: self._close(i, mark, 'EOD_INDEX_SESSION_CLOSE', time)
            return
        first_tp = any(e['leg'] == 1 and e['reason'] == 'TP' for e in self.events)
        if first_tp and self.open[1] and not self.be_moved:
            self._tighten(1, stop_at_r(self.plan.entry, self.plan.risk_distance, self.direction, .05))
            self.be_moved = True
        changes = protection_steps(self.plan.entry, self.plan.risk_distance, self.direction, mark,
                                   be_moved=self.be_moved, trailing_level=self.trailing_level)
        if 'EARLY_BE' in changes:
            for i in range(2): self._tighten(i, changes['EARLY_BE'])
            self.be_moved = True
        if 'TRAIL_075' in changes and self.open[1]:
            self._tighten(1, changes['TRAIL_075'])
            self.trailing_level = .75
            self.be_moved = True
        if 'TRAIL_140' in changes and self.open[1]:
            self._tighten(1, changes['TRAIL_140'])
            self.trailing_level = 1.4

    def summary(self, *, total_extra_cost_price=None):
        closed = not any(self.open)
        gross = sum(e['gross_r'] for e in self.events)
        if total_extra_cost_price is not None and (not isfinite(total_extra_cost_price) or total_extra_cost_price < 0):
            raise ValueError('Explicit nonnegative extra modeled cost required')
        return dict(entry=self.plan.entry, initial_stop=self.plan.stop, risk_distance=self.plan.risk_distance,
            entry_time=self.entry_time.isoformat(), closed=closed, open_quantity=.5*sum(self.open),
            exit_time=self.events[-1]['time'] if closed else None, events=self.events,
            realized_quote_r=gross,
            modeled_after_extra_cost_r=gross-total_extra_cost_price/self.plan.risk_distance
                if closed and total_extra_cost_price is not None else None,
            complete_venue_net_r=None, venue_parity_proven=False)
