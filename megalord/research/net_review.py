"""Predeclared net-evidence review. Quote-model wins cannot pass this gate."""
import math
import numpy as np
import pandas as pd

REQUIRED_COSTS={'commission','carry','spread','slippage','conversion','operating_costs'}


def assess_net_evidence(document, *, as_of):
    result=dict(verdict='INSUFFICIENT_EVIDENCE',net_edge_proven=False,live_approved=False,reasons=[])
    required={'started_at','daily_equity_cash_change','daily_stress_equity_cash_change',
        'closed_trade_count','broker_fills_reconciled','all_intended_decisions_recorded',
        'open_positions_marked_to_market','cost_components_verified','raw_evidence_sha256'}
    missing=required-set(document)
    if missing:
        result['reasons']=['Missing net evidence fields: '+', '.join(sorted(missing))];return result
    start=pd.Timestamp(document['started_at']);end=pd.Timestamp(as_of)
    if start.tzinfo is None or end.tzinfo is None:raise ValueError('Aware protocol times required')
    if end<start+pd.Timedelta(days=180):result['reasons'].append('Fixed 180-day review horizon not reached')
    if document['closed_trade_count']<100:result['reasons'].append('Fewer than 100 reconciled closed trades')
    for flag in ('broker_fills_reconciled','all_intended_decisions_recorded','open_positions_marked_to_market'):
        if document[flag] is not True:result['reasons'].append(flag+' not verified')
    costs=document['cost_components_verified']
    if set(costs)!=REQUIRED_COSTS or any(costs.get(k) is not True for k in REQUIRED_COSTS):
        result['reasons'].append('Complete costs not verified')
    hashes=document['raw_evidence_sha256']
    if not hashes or any(not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h) for h in hashes):
        result['reasons'].append('Raw evidence hashes missing or malformed')
    series=[]
    for name in ('daily_equity_cash_change','daily_stress_equity_cash_change'):
        values=document[name]
        dates=pd.date_range(start,periods=180,freq='D')
        try:
            s=pd.Series({pd.Timestamp(k):v for k,v in values.items()},dtype=float).reindex(dates)
        except (TypeError,ValueError):s=pd.Series(index=dates,dtype=float)
        if s.isna().any() or not np.isfinite(s.to_numpy()).all():
            result['reasons'].append(name+': incomplete daily calendar including zero-trade days')
        series.append(s)
    if result['reasons']:return result
    rng=np.random.default_rng(20261023)
    for label,s in zip(('base','stress_1_5x'),series):
        values=s.to_numpy()
        # Whole weekly clusters retain within-week dependence and simultaneous
        # portfolio positions. The trailing five days remain in total PnL but
        # do not masquerade as an extra full independent weekly observation.
        weeks=values[:175].reshape(25,7).sum(axis=1)
        draws=rng.choice(weeks,size=(20000,len(weeks)),replace=True).mean(axis=1)
        lo,hi=np.quantile(draws,[.025,.975])
        sd=weeks.std(ddof=1)
        result[label]=dict(total_net_cash=float(values.sum()),weekly_mean_cash=float(weeks.mean()),
            weekly_t_stat=float(weeks.mean()/(sd/math.sqrt(len(weeks)))) if sd>0 else None,
            weekly_mean_95ci=[float(lo),float(hi)],weekly_blocks=len(weeks))
    positive=all(result[k]['total_net_cash']>0 and result[k]['weekly_mean_95ci'][0]>0 for k in ('base','stress_1_5x'))
    result['verdict']='POSITIVE_NET_EVIDENCE_REQUIRES_AUDIT' if positive else 'NET_EDGE_NOT_ESTABLISHED'
    # Supplied flags/hashes alone cannot prove provenance or authorize capital.
    result['reasons']=['Independent provenance, risk and venue/demo-to-live review still required']
    return result
