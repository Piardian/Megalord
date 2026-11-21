from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from megalord.research.forward_portfolio import ForwardPortfolio
from megalord.research.quote_replay import Quote
from megalord.types import TradeSignal,MathFamily,StrategyRegime

AT=pd.Timestamp('2026-10-23T14:45:00Z')
CONFIG=json.loads((Path(__file__).resolve().parents[1]/'config/megalord_config.json').read_text())


def signal(key='one'):
    return TradeSignal(key,'Trawma',MathFamily.FAMILY_ORDERFLOW,StrategyRegime.TREND_MOMENTUM,
        'BTCUSD','LONG',AT,None,100.,99.,101.,102.,metadata={'live_active':True})


def admit(p,s=None,**overrides):
    kwargs=dict(at=AT,quote=Quote(AT,99.99,100.),point=.01,macro_bias=None,
        news_check={'is_frozen':False,'reason':'Synthetic test observation'},news_available_at=AT)
    kwargs.update(overrides)
    return p.admit(s or signal(),**kwargs)


def test_same_family_opportunities_share_clone_budget_instead_of_independent_r_sum():
    p=ForwardPortfolio(CONFIG,started_at=AT)
    assert admit(p)['status']=='MODEL_APPROVED'
    assert admit(p,signal('two'))['status']=='MODEL_APPROVED'
    third=admit(p,signal('three'))
    assert third['status']=='ARBITER_REJECTED'
    assert third['evidence']['observed_arbiter']['action']=='CLONE_CAPPED'
    assert sum(x['allocated_risk_r'] for x in p.active_positions())==pytest.approx(.6)


def test_loss_and_open_spread_affect_weighted_equity_but_never_imply_known_net():
    p=ForwardPortfolio(CONFIG,started_at=AT);admit(p)
    assert p.summary()['unrealized_weighted_quote_r']<0
    p.advance('BTCUSD',Quote(AT+pd.Timedelta(seconds=1),98.9,98.91),poll=False)
    summary=p.summary()
    assert summary['closed_model_trades']==1
    assert summary['realized_weighted_quote_r']==pytest.approx(-.495)
    assert summary['daily_realized_quote_loss_r']==pytest.approx(.495)
    assert summary['max_drawdown_quote_r']>=.495-1e-10
    assert summary['engine_loss_streaks']['Trawma']==1
    assert summary['complete_venue_net_r'] is None
    assert summary['complete_operating_net_cash'] is None
    assert not summary['net_edge_proven']


def test_partial_tp_is_realized_once_and_runner_gap_uses_executable_quote():
    p=ForwardPortfolio(CONFIG,started_at=AT);admit(p)
    p.advance('BTCUSD',Quote(AT+pd.Timedelta(seconds=1),101.,101.01),poll=True)
    assert p.summary()['realized_weighted_quote_r']==pytest.approx(.225)
    assert p.summary()['closed_model_trades']==0
    p.advance('BTCUSD',Quote(AT+pd.Timedelta(seconds=2),100.,100.01),poll=False)
    assert p.summary()['realized_weighted_quote_r']==pytest.approx(.225)
    assert p.summary()['closed_model_trades']==1
    assert len(p.closed[0]['events'])==2
    p.advance('BTCUSD',Quote(AT+pd.Timedelta(seconds=3),99.,99.01),poll=True)
    assert p.summary()['realized_weighted_quote_r']==pytest.approx(.225)


def test_news_missing_or_stale_quote_does_not_consume_opportunity_or_create_fill():
    p=ForwardPortfolio(CONFIG,started_at=AT)
    assert admit(p,news_check=None)['status']=='MISSING_OBSERVED_NEWS_RETRY'
    assert not p.processed and not p.positions
    assert admit(p,quote=Quote(AT-pd.Timedelta(seconds=61),99.99,100.))['status']=='STALE_QUOTE_RETRY'
    assert not p.processed and not p.positions


def test_risk_budget_checks_current_positions_and_closed_loss():
    p=ForwardPortfolio(CONFIG,started_at=AT)
    p.daily_date=AT.date();p.daily_realized_loss_r=2.4
    result=admit(p)
    assert result['status']=='FIREWALL_REJECTED'
    assert result['evidence']['gate_parity_equal']
    assert not p.positions


def test_day_reset_keeps_overnight_open_exposure():
    p=ForwardPortfolio(CONFIG,started_at=AT);admit(p)
    p.daily_realized_loss_r=1.
    p.roll_day(AT+pd.Timedelta(days=1))
    assert p.daily_realized_loss_r==0
    assert len(p.active_positions())==1


def test_signal_before_start_is_never_backfilled_as_forward_trade():
    p=ForwardPortfolio(CONFIG,started_at=AT+pd.Timedelta(seconds=1))
    assert admit(p)['status']=='BEFORE_PROSPECTIVE_START'
    assert not p.positions
