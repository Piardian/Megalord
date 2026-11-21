"""Read-only dedicated demo cash journal. No login, orders or inferred fills."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json

from megalord.research.demo_preflight import demo_account_check
from megalord.live.deal_accounting import summarize_closed_position


def reconcile_linked_deals(deals, links):
    """Only explicit signal-to-position links qualify; never match by price/time."""
    records=[dict(d) if isinstance(d,dict) else d._asdict() for d in deals]
    if any(not isinstance(d.get('ticket'),int) or d['ticket']<=0 for d in records):
        raise ValueError('Broker deal tickets required')
    if len({d['ticket'] for d in records})!=len(records):raise ValueError('Duplicate broker deals')
    signals=set();positions=set();closed=[];unresolved=[]
    for link in links:
        sid=link.get('signal_id');ids=link.get('position_ids')
        if not isinstance(sid,str) or not sid or sid in signals:
            raise ValueError('Unique explicit signal IDs required')
        if not isinstance(ids,list) or not ids or any(type(p) is not int or p<=0 for p in ids):
            raise ValueError('Explicit positive broker position IDs required')
        if len(set(ids))!=len(ids) or positions.intersection(ids):
            raise ValueError('A broker position cannot be assigned twice')
        signals.add(sid);positions.update(ids)
        parts=[]
        for pid in ids:
            rows=[d for d in records if d.get('position_id')==pid]
            part=summarize_closed_position(rows,pid)
            if part is None:
                unresolved.append(dict(signal_id=sid,position_id=pid,
                    reason='Open, missing, reversed or incomplete broker cash evidence'))
            else:parts.append(part)
        if len(parts)==len(ids):
            closed.append(dict(signal_id=sid,position_ids=ids,
                broker_deal_cash=sum(p['profit'] for p in parts),
                commission=sum(p['commission'] for p in parts),swap=sum(p['swap'] for p in parts),
                fee=sum(p['fee'] for p in parts),gross_profit=sum(p['gross_profit'] for p in parts),
                deal_tickets=[t for p in parts for t in p['deal_tickets']],
                attribution_basis='Explicit caller-supplied links; must be checked against order acknowledgements',
                complete_operating_net_cash=None))
    unlinked=[d for d in records if d.get('position_id') not in positions]
    return dict(closed_linked_signals=closed,unresolved=unresolved,
        unlinked_deals=unlinked,unlinked_deal_count=len(unlinked),
        broker_deal_cash_subtotal=sum(t['broker_deal_cash'] for t in closed),
        complete_venue_net_cash=None,complete_operating_net_cash=None,
        operating_costs_verified=False,signal_attribution_independently_verified=False,
        net_edge_proven=False,live_approved=False)


def check_profile(profile):
    path=Path(profile.get('terminal_path','')).resolve()
    if path.name.lower()!='terminal64.exe' or 'five percent' in str(path).casefold():
        raise ValueError('Separate MetaTrader executable required; existing prop terminal excluded')
    if type(profile.get('expected_login')) is not int or profile['expected_login']<=0:
        raise ValueError('Dedicated demo login has not been pinned')
    if profile.get('expected_server')!='MetaQuotes-Demo':
        raise ValueError('Selected free demo server must be MetaQuotes-Demo')
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',required=True,type=Path)
    parser.add_argument('--links',required=True,type=Path)
    parser.add_argument('--since',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    profile=json.loads(args.profile.read_text(encoding='utf-8'));terminal=check_profile(profile)
    start=datetime.fromisoformat(args.since.replace('Z','+00:00'))
    if start.tzinfo is None:raise ValueError('Explicit UTC-aware history start required')
    if not terminal.is_file():raise ValueError('Dedicated terminal not installed at pinned path')
    links=json.loads(args.links.read_text(encoding='utf-8'))
    if links.get('schema_version')!=1:raise ValueError('Explicit signal-position link schema required')
    import MetaTrader5 as mt5
    if not mt5.initialize(str(terminal),timeout=10000):raise RuntimeError('Dedicated demo terminal connection failed')
    try:
        check=demo_account_check(mt5.account_info(),expected_login=profile['expected_login'],
            expected_server=profile['expected_server'])
        if not check['eligible_account_profile']:raise ValueError(check['reason'])
        end=datetime.now(timezone.utc)
        deals=mt5.history_deals_get(start,end)
        if deals is None:raise RuntimeError('Broker history query failed; empty history not inferred')
        raw=json.dumps([d._asdict() for d in deals],allow_nan=False).encode()
        result=reconcile_linked_deals(deals,links.get('links',[]))
        result.update(observed_at=end.isoformat(),history_since=start.isoformat(),
            source_kind='BROKER_DEMO_HISTORY',raw_history_sha256=sha256(raw).hexdigest(),
            links_sha256=sha256(args.links.read_bytes()).hexdigest(),orders_sent=0)
        args.output.mkdir(parents=True,exist_ok=False)
        (args.output/'broker_deals.json').write_bytes(raw)
        (args.output/'reconciliation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(dict(closed=len(result['closed_linked_signals']),
            unresolved=len(result['unresolved']),unlinked=result['unlinked_deal_count'],
            complete_operating_net_cash=None,orders_sent=0)))
    finally:mt5.shutdown()


if __name__=='__main__':main()
