"""Read-only existing-account evidence. This module cannot send orders.

The user selected their existing The5ers account. It is an observed prop
account, not silently relabelled as the separate free demo account.
"""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
import argparse
import json
import time

from megalord.research.demo_journal import reconcile_linked_deals

ROOT=Path(__file__).resolve().parents[2]
TERMINAL=Path('C:/Program Files/Five Percent Online MetaTrader 5/terminal64.exe')


def verify_observed_account(account,profile):
    if account is None:return False
    return (type(profile.get('expected_login')) is int and profile['expected_login']>0 and
        account.login==profile['expected_login'] and account.server==profile.get('expected_server'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch',action='store_true')
    args=parser.parse_args()
    import MetaTrader5 as mt5
    if not mt5.initialize(str(TERMINAL),timeout=10000):raise RuntimeError('The5ers read-only connection failed')
    account=mt5.account_info()
    if account is None or account.server!='FivePercentOnline-Real':
        mt5.shutdown();raise ValueError('Expected attached The5ers account not found')
    at=datetime.now(timezone.utc)
    home=ROOT/'audit/account_observer'/at.strftime('%Y%m%dT%H%M%S_%fZ')
    home.mkdir(parents=True,exist_ok=False)
    profile=dict(expected_login=int(account.login),expected_server=account.server,
        terminal_path=str(TERMINAL),connection_mode='READ_ONLY',orders_enabled=False,
        account_classification='USER_SELECTED_THE5ERS_PROP_ACCOUNT',independent_demo_verified=False)
    (home/'profile.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
    (home/'links.json').write_text('{"schema_version":1,"links":[]}',encoding='utf-8')
    registry=dict(home=str(home),started_at=at.isoformat(),orders_enabled=False,connection_mode='READ_ONLY')
    (ROOT/'audit/account_observer/current.json').write_text(json.dumps(registry,indent=2),encoding='utf-8')
    portfolio=json.loads((ROOT/'audit/forward_portfolio/current.json').read_text())
    start=datetime.fromisoformat(portfolio['started_at'])
    print(str(home),flush=True)
    try:
        while not (home/'STOP').exists():
            observed=datetime.now(timezone.utc);account=mt5.account_info()
            if not verify_observed_account(account,profile):raise ValueError('Account identity changed; observation stopped')
            # Preserve raw broker times. Quote clock correction is not proof
            # of the broker DEAL clock. Query an overlap; do not backdate fills.
            deals=mt5.history_deals_get(start-timedelta(days=1),observed+timedelta(days=1))
            positions=mt5.positions_get()
            if deals is None or positions is None:raise RuntimeError('Broker history/positions unavailable')
            rows=[d._asdict() for d in deals]
            raw=json.dumps(dict(observed_at=observed.isoformat(),deals=rows,
                open_positions=[p._asdict() for p in positions]),allow_nan=False).encode()
            digest=sha256(raw).hexdigest()
            dest=home/'raw'/f'{observed.strftime("%Y%m%dT%H%M%S_%f")}.json'
            dest.parent.mkdir(exist_ok=True);dest.write_bytes(raw)
            links=json.loads((home/'links.json').read_text(encoding='utf-8'))
            report=reconcile_linked_deals(rows,links.get('links',[]))
            report.update(observed_at=observed.isoformat(),connected=True,server=account.server,
                mt5_trade_mode=account.trade_mode,independent_demo_verified=False,
                connection_mode='READ_ONLY',orders_sent=0,raw_file=str(dest.relative_to(home)),
                raw_sha256=digest,broker_deal_clock_parity_verified=False,
                account_open_positions=len(positions),all_account_deals_observed=len(rows),
                megalord_portfolio_home=portfolio['home'],
                note='Other account trades are not Megalord forward results; explicit broker position links required.')
            temp=home/'status.json.tmp';temp.write_text(json.dumps(report,indent=2),encoding='utf-8');temp.replace(home/'status.json')
            print(json.dumps(dict(at=observed.isoformat(),connected=True,orders_sent=0,
                account_deals=len(rows),linked_closed=len(report['closed_linked_signals']))),flush=True)
            if not args.watch:break
            time.sleep(60)
    finally:mt5.shutdown()


if __name__=='__main__':main()
