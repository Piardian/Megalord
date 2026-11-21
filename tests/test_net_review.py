import pandas as pd

from megalord.research.net_review import assess_net_evidence,REQUIRED_COSTS


START=pd.Timestamp('2026-10-23T15:00:00Z')


def evidence():
    days=pd.date_range(START,periods=180,freq='D')
    return dict(started_at=str(START),daily_equity_cash_change={str(t):2. for t in days},
        daily_stress_equity_cash_change={str(t):1. for t in days},closed_trade_count=100,
        broker_fills_reconciled=True,all_intended_decisions_recorded=True,
        open_positions_marked_to_market=True,cost_components_verified={k:True for k in REQUIRED_COSTS},
        raw_evidence_sha256=['a'*64])


def test_profitable_quote_only_output_never_passes_net_review():
    result=assess_net_evidence({'realized_weighted_quote_r':1000},as_of=START)
    assert result['verdict']=='INSUFFICIENT_EVIDENCE'
    assert not result['net_edge_proven']


def test_unknown_commission_blocks_positive_claim_despite_all_positive_days():
    doc=evidence();doc['cost_components_verified']['commission']=None
    result=assess_net_evidence(doc,as_of=START+pd.Timedelta(days=180))
    assert result['verdict']=='INSUFFICIENT_EVIDENCE'
    assert 'Complete costs not verified' in result['reasons']


def test_early_success_cannot_shorten_fixed_horizon():
    result=assess_net_evidence(evidence(),as_of=START+pd.Timedelta(days=30))
    assert result['verdict']=='INSUFFICIENT_EVIDENCE'


def test_zero_trade_days_cannot_be_omitted_to_inflate_weekly_mean():
    doc=evidence();del doc['daily_equity_cash_change'][str(START)]
    result=assess_net_evidence(doc,as_of=START+pd.Timedelta(days=180))
    assert result['verdict']=='INSUFFICIENT_EVIDENCE'


def test_cost_stress_failure_prevents_positive_evidence_and_never_approves_live():
    doc=evidence();doc['daily_stress_equity_cash_change']={k:-1. for k in doc['daily_stress_equity_cash_change']}
    result=assess_net_evidence(doc,as_of=START+pd.Timedelta(days=180))
    assert result['verdict']=='NET_EDGE_NOT_ESTABLISHED'
    assert not result['live_approved']


def test_synthetic_positive_data_is_only_an_audit_candidate_not_proven_edge():
    result=assess_net_evidence(evidence(),as_of=START+pd.Timedelta(days=180))
    assert result['verdict']=='POSITIVE_NET_EVIDENCE_REQUIRES_AUDIT'
    assert result['base']['weekly_blocks']==25
    assert not result['net_edge_proven'] and not result['live_approved']
