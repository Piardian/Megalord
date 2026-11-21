import pandas as pd
import pytest
from megalord.research.quote_replay import Quote, TwinPosition


def position(direction='LONG', symbol='BTCUSD'):
    return TwinPosition(engine='Stigma',symbol=symbol,direction=direction,
        quote=Quote('2026-10-01T10:00Z',100.,100.),stop=99. if direction=='LONG' else 101.,
        tp1=101. if direction=='LONG' else 99.,tp2=102.5 if direction=='LONG' else 97.5)


@pytest.mark.parametrize('direction,adverse', [('LONG',97.),('SHORT',103.)])
def test_gap_stop_fills_at_executable_quote_and_unknown_cost_never_becomes_net(direction,adverse):
    p=position(direction)
    p.advance(Quote('2026-10-01T10:01Z',adverse,adverse))
    r=p.summary()
    assert r['closed'] and r['realized_quote_r']==-3
    assert r['modeled_after_extra_cost_r'] is None and r['complete_venue_net_r'] is None


def test_tp1_runner_protection_and_trailing_are_causal():
    p=position()
    p.advance(Quote('2026-10-01T10:01Z',100.81,100.81))
    assert p.stops==[100.05,100.05]
    p.advance(Quote('2026-10-01T10:02Z',101.,101.))
    assert p.open==[False,True]
    p.advance(Quote('2026-10-01T10:03Z',101.51,101.51))
    assert p.stops[1]==100.75
    p.advance(Quote('2026-10-01T10:04Z',100.7,100.7))
    assert p.summary(total_extra_cost_price=.1)['modeled_after_extra_cost_r']==pytest.approx(.75)


def test_no_poll_cannot_move_a_daemon_managed_stop():
    p=position()
    p.advance(Quote('2026-10-01T10:01Z',100.9,100.9),poll=False)
    assert p.stops==[99.,99.]
    p.advance(Quote('2026-10-01T10:02Z',98.5,98.5),poll=False)
    assert p.summary()['realized_quote_r']==-1.5


def test_entry_spread_and_short_exit_ask_are_charged_through_prices():
    p=TwinPosition(engine='Pragma',symbol='EURUSD',direction='SHORT',
        quote=Quote('2026-10-01T10:00Z',100.,100.1),stop=101.,tp1=99.,tp2=98.)
    p.advance(Quote('2026-10-01T10:01Z',100.9,101.1))
    assert p.summary()['realized_quote_r']==pytest.approx(-1.1)


def test_invalid_geometry_time_and_quote_cannot_enter_model():
    p=position()
    with pytest.raises(ValueError,match='backwards'):
        p.advance(Quote('2026-10-01T09:59Z',100.,100.))
    with pytest.raises(ValueError,match='bid/ask'):
        p.advance(Quote('2026-10-01T10:01Z',101.,100.))
    with pytest.raises(ValueError,match='stop geometry'):
        TwinPosition(engine='Stigma',symbol='BTCUSD',direction='LONG',
                     quote=Quote('2026-10-01T10:00Z',100.,100.),stop=100.,tp1=101.,tp2=102.)
