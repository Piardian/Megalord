"""Cash reconciliation and actual daemon lifecycle tests with an inert broker."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd
import pytest

from megalord.live.deal_accounting import summarize_closed_position
from megalord.live.position_policy import execution_plan, protection_steps
from megalord.research.event_import import import_event_document
from megalord.research.producer_events import broker_position_events
from megalord.research.trade_contract import ENGINES

NOW = pd.Timestamp("2026-10-23T10:00Z")


def deals():
    common = dict(position_id=100, order=10, price=100., comment="fixture", time=1792749600)
    return [dict(common, ticket=1, entry=0, volume=1., profit=0., commission=-2., fee=-.5, swap=0.),
            dict(common, ticket=2, entry=1, volume=.4, price=101., profit=4., commission=-.8, fee=-.2, swap=-.1, time=1792753200),
            dict(common, ticket=3, entry=1, volume=.6, price=101.5, profit=9., commission=-1.2, fee=-.3, swap=.5, time=1792756800)]


def test_full_deal_cash_includes_entry_charges_and_every_partial_exit():
    result = summarize_closed_position(deals(), 100)
    assert result["profit"] == pytest.approx(8.4)
    assert result["gross_profit"] == 13
    assert result["commission"] == -4 and result["fee"] == -1
    assert result["deal_tickets"] == [1, 2, 3]
    assert not result["unattributed_account_charges_covered"]


@pytest.mark.parametrize("fault", ["partial", "no_entry", "missing_fee", "duplicate", "reversal"])
def test_partial_or_incomplete_position_cannot_be_certified_closed(fault):
    d = deals()
    if fault == "partial": d.pop()
    elif fault == "no_entry": d.pop(0)
    elif fault == "missing_fee": d[0].pop("fee")
    elif fault == "duplicate": d.append(deepcopy(d[-1]))
    elif fault == "reversal": d[0]["entry"] = 2
    assert summarize_closed_position(d, 100) is None


@pytest.mark.parametrize("engine", ENGINES)
def test_real_deal_schema_connects_every_engine_to_common_cash_ledger(engine):
    summary = summarize_closed_position(deals(), 100)
    row = broker_position_events(summary, trade_id="fixture-only", engine=engine, symbol="XAUUSD",
        direction="LONG", initial_stop=99., initial_risk_cash=10., account_currency="USD",
        source_hash="a" * 64, observed_at=NOW + pd.Timedelta(hours=3), conversion_treatment="not_applicable")
    result = import_event_document(json.dumps({"schema_version": 1, "source_kind": "broker_export", "trades": [row]}).encode())
    assert not result["rejected"] and len(result["closed"]) == 1
    evidence = result["closed"][0]["evidence"]
    assert evidence["gross_r"] - evidence["cost_r"] == pytest.approx(.84)
    assert not row["unattributed_account_charges_covered"]


def test_shared_plan_keeps_current_target_policy_and_protection_levels():
    p = execution_plan("Begonya00", "EURUSD", "LONG", 1.1, 1.09, 1.12, 1.11)
    assert p.tp1 == 1.11 and p.tp2 == 1.125
    p2 = execution_plan("Pragma", "EURUSD", "LONG", 1.1, 1.09, 1.119, 1.13)
    assert p2.tp1 == 1.119 and p2.tp2 == 1.13
    assert protection_steps(1.1, .01, "LONG", 1.1221, be_moved=False, trailing_level=0) == {
        "EARLY_BE": 1.1005, "TRAIL_075": 1.1075, "TRAIL_140": 1.114}
    assert protection_steps(1.1, .01, "SHORT", 1.0779, be_moved=False, trailing_level=0) == {
        "EARLY_BE": 1.0995, "TRAIL_075": 1.0925, "TRAIL_140": 1.086}


def test_executor_returns_complete_net_history_and_verified_cash_has_no_fallback(monkeypatch):
    from megalord.live import mt5_executor as module
    e = module.MegalordMT5Executor(target_account=0, base_risk_pct=.5)
    e.ensure_connected = lambda: True
    monkeypatch.setattr(module.mt5, "history_deals_get", lambda **kw: [SimpleNamespace(**d) for d in deals()])
    assert e.get_position_close_deal(100)["profit"] == pytest.approx(8.4)
    monkeypatch.setattr(module.mt5, "account_info", lambda: SimpleNamespace(equity=10000.))
    assert e.get_one_r_cash_verified() == 50.
    e.ensure_connected = lambda: False
    assert e.get_one_r_cash_verified() is None
    assert e.get_one_r_cash() == 50.  # Compatibility fallback is explicitly unverified.


class Broker:
    def __init__(self):
        self.positions = {1: SimpleNamespace(price_open=1.1, sl=1.09, tp=1.11),
                          2: SimpleNamespace(price_open=1.1, sl=1.09, tp=1.13)}
        self.closed = {}
        self.fail = set()
        self.modifications = []
    def get_one_r_cash(self): return 999.  # Later equity must not change entry R.
    def get_open_position(self, ticket): return self.positions.get(ticket)
    def get_position_close_deal(self, ticket): return self.closed.get(ticket)
    def modify_position_sl(self, ticket, symbol, stop, target):
        self.modifications.append((ticket, stop))
        if ticket in self.fail: return False
        self.positions[ticket].sl = stop
        return True


@pytest.mark.parametrize('engine',ENGINES)
@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('path',[[.81,1.01,1.51,.74],[-1.5],[3.1],[.81,.04]])
def test_quote_replay_matches_actual_daemon_twin_leg_protection_and_exit(tmp_path, monkeypatch,engine,direction,path):
    from megalord.research.quote_replay import Quote, TwinPosition
    d, broker, tick = daemon_fixture(tmp_path, monkeypatch)
    sign=1 if direction=='LONG' else -1
    stop=1.1-sign*.01;tp1=1.1+sign*.01;tp2=1.1+sign*.03
    d.state['open_positions'][0].update(engine_name=engine,direction=direction,sl_price=stop,leg1_tp=tp1,leg2_tp=tp2)
    broker.positions[1].sl=broker.positions[2].sl=stop
    broker.positions[1].tp,broker.positions[2].tp=tp1,tp2
    replay = TwinPosition(engine=engine,symbol='EURUSD',direction=direction,
        quote=Quote(NOW,1.1,1.1),stop=stop,tp1=tp1,tp2=tp2)
    for level in path:
        mark=1.1+sign*level*.01
        tick.bid=tick.ask=mark
        # Broker-resident SL/TP fills precede the daemon's next poll.
        for ticket, p in list(broker.positions.items()):
            if sign*(mark-p.sl)<=0 or sign*(mark-p.tp)>=0:
                is_tp=sign*(mark-p.tp)>=0
                price=p.tp if is_tp else mark
                profit=.5*sign*(price-1.1)/.01*50.
                close(broker,ticket,profit,'[tp]' if is_tp else '[sl]',5 if is_tp else 4)
        replay.advance(Quote(NOW,mark,mark))
        d.execute_cycle()
        for ticket,p in broker.positions.items():
            assert p.sl == pytest.approx(replay.stops[ticket-1])
        assert len(broker.positions)==sum(replay.open)
    closed,=d.state['closed_trades_history']
    assert not replay.summary()['open_quantity']
    assert closed['net_pnl_r']==pytest.approx(round(replay.summary()['realized_quote_r'],2))


def daemon_fixture(tmp_path, monkeypatch):
    from megalord import megalord_daemon as module
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.to_pydatetime() if tz else NOW.to_pydatetime().replace(tzinfo=None)
    monkeypatch.setattr(module, "datetime", Clock)
    tick = SimpleNamespace(bid=1.1, ask=1.1001)
    monkeypatch.setitem(sys.modules, "MetaTrader5", SimpleNamespace(symbol_info_tick=lambda symbol: tick,
                        DEAL_REASON_TP=5, DEAL_REASON_SL=4))
    cfg = tmp_path / "config.json"; cfg.write_text("{}")
    d = module.MegalordDaemon(cfg, tmp_path / "state.json", notifier=SimpleNamespace(send_alert=lambda *a: False))
    # This only switches a test object with all I/O replaced, never a real daemon.
    d.mode = "mt5_live"
    d.mt5_executor = broker = Broker()
    d.reconcile_mt5_positions = lambda: None
    d.fetch_htf_macro_gates = lambda: {}
    d.poll_new_incoming_signals = lambda: []
    d._dispatch_close_alert = lambda *a: None
    d.state["open_positions"] = [{"signal_id": "fixture-only", "engine_name": "Pragma", "symbol": "EURUSD",
        "direction": "LONG", "math_family": "FAMILY_AMT_VWAP", "strategy_regime": "TREND_MOMENTUM",
        "entry_time": NOW.isoformat(), "entry_price": 1.1, "sl_price": 1.09, "risk_dist": .01,
        "leg1_ticket": 1, "leg2_ticket": 2, "leg1_open": True, "leg2_open": True,
        "leg1_tp": 1.11, "leg2_tp": 1.13, "allocated_risk_r": 1., "one_r_cash_at_entry": 50.,
        "be_moved": False, "leg1_profit": 0., "trailing_level": 0.}]
    return d, broker, tick


def close(broker, ticket, profit, comment="[sl]", reason=4):
    broker.positions.pop(ticket, None)
    broker.closed[ticket] = {"profit": profit, "comment": comment, "reason": reason}


def test_failed_protection_ack_does_not_claim_both_legs_are_risk_free(tmp_path, monkeypatch):
    d, broker, tick = daemon_fixture(tmp_path, monkeypatch)
    tick.bid = 1.1081
    broker.fail.add(1)
    d.execute_cycle()
    assert not d.state["open_positions"][0]["be_moved"]
    broker.fail.clear()
    d.execute_cycle()
    assert d.state["open_positions"][0]["be_moved"]
    assert broker.positions[1].sl == broker.positions[2].sl == 1.1005


def test_missing_close_history_waits_instead_of_recording_zero_profit(tmp_path, monkeypatch):
    d, broker, tick = daemon_fixture(tmp_path, monkeypatch)
    broker.positions.pop(1)
    d.execute_cycle()
    assert len(d.state["open_positions"]) == 1
    assert d.state["open_positions"][0]["leg1_open"]
    assert not d.state["closed_trades_history"]


def test_runner_closing_first_cannot_orphan_the_first_leg(tmp_path, monkeypatch):
    d, broker, tick = daemon_fixture(tmp_path, monkeypatch)
    close(broker, 2, 10., "[tp]", 5)
    d.execute_cycle()
    assert len(d.state["open_positions"]) == 1 and not d.state["closed_trades_history"]
    close(broker, 1, 5., "[tp]", 5)
    d.execute_cycle()
    assert not d.state["open_positions"]
    assert d.state["closed_trades_history"][0]["net_pnl_r"] == .3
    assert d.state["closed_trades_history"][0]["r_reference_verified"]


def test_positive_trailing_stop_is_not_counted_as_tp2(tmp_path, monkeypatch):
    d, broker, tick = daemon_fixture(tmp_path, monkeypatch)
    d.state["open_positions"][0].update(trailing_level=1.4, be_moved=True)
    close(broker, 1, 5., "[tp]", 5)
    close(broker, 2, .5, "[sl]", 4)
    d.execute_cycle()
    trade = d.state["closed_trades_history"][0]
    assert trade["close_reason"] == "TRAILING_STOP"
    assert trade["net_pnl_r"] == .11


def test_single_order_keeps_actual_broker_target_instead_of_replacing_it_with_tp1(tmp_path, monkeypatch):
    from megalord.types import TradeSignal, MathFamily, StrategyRegime
    d, broker, tick = daemon_fixture(tmp_path, monkeypatch)
    d.state["open_positions"] = []
    s = TradeSignal("new-fixture", "Pragma", MathFamily.FAMILY_AMT_VWAP, StrategyRegime.TREND_MOMENTUM,
        "EURUSD", "LONG", NOW, None, entry_price=1.1, sl_price=1.09, tp1_price=1.111, tp2_price=1.13)
    d.poll_new_incoming_signals = lambda: [s]
    d.fetch_htf_macro_gates = lambda: {"EURUSD": "LONG_ONLY"}
    d.news_guard.check_news_freeze = lambda symbol: {"is_frozen": False}
    d._dispatch_alert = lambda *a, **k: None
    broker.get_market_quality_context = lambda *a: {"current_price": 1.1, "spread_price": .0001}
    broker.get_one_r_cash_verified = lambda: 50.
    broker.place_twin_market_orders = lambda **kw: {"leg1_ticket": 1, "leg1_vol": .01,
        "leg1_tp": kw["tp2_price"], "leg2_ticket": None, "leg2_vol": 0.,
        "leg2_tp": kw["tp2_price"], "entry_price": 1.1, "total_lot": .01, "is_split": False}
    d.execute_cycle()
    assert d.state["open_positions"][0]["leg1_tp"] == 1.13
    assert d.state["open_positions"][0]["one_r_cash_at_entry"] == 50.
