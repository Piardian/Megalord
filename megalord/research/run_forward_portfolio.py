"""Start a prospective coupled shadow portfolio. Never sends broker orders.

Each start creates a separate segment. No historical entry is backfilled and
no existing account/daemon state is adopted as a prospective portfolio.
"""
from datetime import datetime,timezone,timedelta
from pathlib import Path
from hashlib import sha256
import json
import sys
import time

import pandas as pd

from megalord.research.forward_capture import freeze_sources,verify_sources
from megalord.research.forward_sources import write_json,append,forbidden
from megalord.research.forward_stream import load_forward_intents
from megalord.research.forward_portfolio import ForwardPortfolio
from megalord.research.quote_replay import Quote
from megalord.research.broker_clock import observed_offset_seconds

ROOT=Path(__file__).resolve().parents[2]


def now():return datetime.now(timezone.utc)


def main():
    import MetaTrader5 as mt5
    from megalord.megalord_daemon import MegalordDaemon
    from megalord.firewall.news_guard import MegalordNewsGuard
    registry=json.loads((ROOT/'audit/forward_sources/current.json').read_text())
    source_home=Path(registry['home'])
    started=now();home=ROOT/'audit/forward_portfolio'/started.strftime('%Y%m%dT%H%M%S_%fZ')
    home.mkdir(parents=True,exist_ok=False)
    # One portfolio per active source session; lock survives only while running.
    import msvcrt
    lock=(source_home/'portfolio.lock').open('a+b')
    if lock.tell()==0:lock.write(b'0');lock.flush()
    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    manifest=freeze_sources(home)
    config=json.loads((home/'policy.json').read_text())
    portfolio=ForwardPortfolio(config,started_at=started)
    protocol=dict(started_at=started.isoformat(),cutoff=(started+timedelta(days=180)).isoformat(),
        source_home=str(source_home),mode='COUPLED_QUOTE_SHADOW',orders_enabled=False,notifications_enabled=False,
        risk_unit='Existing allocator R weights; no cash capital or broker lots inferred',
        selection='All four currently enabled native engines; two disabled engines unchanged',
        primary_review='Fixed 180 calendar days, at least 100 cost-complete broker-verified closed trades; day 30 infrastructure checkpoint only',
        no_early_success_stop=True,missing_costs='Unknown; net stays null',
        unknown_cost_components=['commission','carry','additional_slippage','lot_rounding','cash_conversion','operating_costs'],
        admission_policy='Actual quality/arbiter/news/firewall; recorded inputs replayed without historical bypass',
        venue_fill_model='Ideal twin-leg quote fills; no broker acknowledgement or actual fill certification',
        positive_edge_rule='Complete coverage and reconciled costs; weekly-block 95% interval above zero at both base and 1.5x transaction costs; quote models cannot certify venue net edge',
        live_approved=False)
    write_json(home/'protocol.json',protocol)
    # Extend source collection administratively before the first portfolio
    # trade. Preserve the old 30-day infrastructure protocol verbatim.
    source_protocol=source_home/'protocol.json'
    prior=source_protocol.read_bytes()
    (home/'source_protocol_before_extension.json').write_bytes(prior)
    source_doc=json.loads(prior)
    source_doc['collection_cutoff']=protocol['cutoff']
    source_doc['extension_reason']='User-authorized prospective profitability validation; before any coupled portfolio trade'
    source_doc['extension_observed_at']=started.isoformat()
    write_json(source_protocol,source_doc)
    write_json(home/'source_extension.json',dict(observed_at=started.isoformat(),
        previous_sha256=sha256(prior).hexdigest(),new_sha256=sha256(source_protocol.read_bytes()).hexdigest(),
        strategy_parameters_changed=False,source_collection_cutoff=protocol['cutoff'],
        capture_segment_note='Original standalone capture remains a 30-day segment; portfolio records its own inputs/ticks/equity.'))
    write_json(ROOT/'audit/forward_portfolio/current.json',dict(home=str(home),started_at=started.isoformat(),source_home=str(source_home)))
    # Isolated news cache: native refresh must never overwrite root production config.
    news_path=home/'news_guard.json';news_path.write_bytes((ROOT/'config/news_guard_config.json').read_bytes())
    news=MegalordNewsGuard(news_path)
    macro_reader=object.__new__(MegalordDaemon);macro_reader.state={}
    if not mt5.initialize():raise RuntimeError('Read-only market connection unavailable')
    account=mt5.account_info();pinned=(account.login,account.server)
    for name in ('order_send','order_check','login','symbol_select'):setattr(mt5,name,forbidden)
    cursors={};event_cursor=0;context_previous=None;issue_count=0
    print(str(home),flush=True)
    try:
        while not (home/'STOP').exists() and now()<started+timedelta(days=180):
            verify_sources(manifest)
            verify_sources(json.loads((source_home/'source_manifest.json').read_text()))
            account=mt5.account_info()
            if account is None or (account.login,account.server)!=pinned:raise RuntimeError('Attached account changed')
            clock=now();offset=observed_offset_seconds(mt5,now=clock)
            source_health={}
            for engine in ('Trawma','Pragma','Stigma','ChecklistTrigger'):
                file=source_home/engine/'heartbeat.json'
                hb=json.loads(file.read_text()) if file.exists() else {}
                age=(clock-datetime.fromisoformat(hb['completed_at'])).total_seconds() if hb else None
                source_health[engine]=bool(hb.get('ok')) and age is not None and 0<=age<120
            signals=load_forward_intents(source_home,observed_at=clock)
            symbols=sorted({s.symbol for s in signals if s.signal_id not in portfolio.processed}|
                           {r['admission']['symbol'] for r in portfolio.positions.values()})
            issues=[];quotes={};points={};batches=[]
            for symbol in symbols:
                last=cursors.get(symbol,pd.Timestamp(started))
                ticks=mt5.copy_ticks_range(symbol,last.to_pydatetime()+timedelta(seconds=offset),
                    clock+timedelta(seconds=offset),mt5.COPY_TICKS_ALL)
                if ticks is None:
                    issues.append(symbol+': tick query failed; open position remains unresolved')
                elif len(ticks):
                    frame=pd.DataFrame(ticks)
                    frame['utc_time_msc']=frame['time_msc']-offset*1000
                    frame=frame.loc[frame['utc_time_msc']>last.timestamp()*1000]
                    frame['symbol']=symbol
                    if len(frame):
                        batches.append(frame)
                        cursors[symbol]=pd.Timestamp(frame['utc_time_msc'].max(),unit='ms',tz='UTC')
                tick=mt5.symbol_info_tick(symbol);info=mt5.symbol_info(symbol)
                if tick is not None and info is not None:
                    ts=pd.Timestamp((tick.time_msc or tick.time*1000)-offset*1000,unit='ms',tz='UTC')
                    quote=Quote(ts,float(tick.bid),float(tick.ask))
                    age=(pd.Timestamp(now())-ts).total_seconds()
                    if 0<=age<=60:
                        quote.validate();quotes[symbol]=quote;points[symbol]=float(info.point)
                    else:issues.append(symbol+': stale/future current quote')
            if batches:
                frame=pd.concat(batches,ignore_index=True).sort_values('utc_time_msc',kind='stable')
                file=home/'ticks'/f'{clock.strftime("%Y%m%dT%H%M%S_%f")}.parquet'
                file.parent.mkdir(exist_ok=True);frame.to_parquet(file,index=False)
                append(home/'tick_manifest.jsonl',dict(observed_at=now().isoformat(),file=str(file.relative_to(home)),sha256=sha256(file.read_bytes()).hexdigest()))
                for row in frame.itertuples():
                    q=Quote(pd.Timestamp(row.utc_time_msc,unit='ms',tz='UTC'),float(row.bid),float(row.ask))
                    try:portfolio.advance(row.symbol,q,poll=False)
                    except ValueError:issues.append(row.symbol+': invalid tick; cannot certify complete execution')
            for symbol,quote in quotes.items():portfolio.advance(symbol,quote,poll=True)
            gates=macro_reader.fetch_htf_macro_gates()
            for signal in sorted(signals,key=lambda s:(s.entry_time,s.signal_id)):
                if signal.signal_id in portfolio.processed:continue
                if not all(source_health.values()):
                    append(home/'decisions.jsonl',dict(observed_at=now().isoformat(),signal_id=signal.signal_id,status='SOURCE_HEALTH_RETRY'))
                    continue
                at=now()
                # Missing broker quote does not consume the signal or invent a fill.
                check=news.check_news_freeze(signal.symbol) if quotes.get(signal.symbol) else None
                news_at=now()
                result=portfolio.admit(signal,at=at,quote=quotes.get(signal.symbol),point=points.get(signal.symbol,0),
                    macro_bias=gates.get(signal.symbol),news_check=check,news_available_at=news_at)
                append(home/'decisions.jsonl',dict(observed_at=news_at.isoformat(),**result))
            context=dict(macro_gates=gates,news_config=news.config,news_refreshed_at=str(news._last_refresh_utc),
                source_health=source_health,calendar_completeness_verified=False,upstream_causality_verified=False)
            digest=sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
            if digest!=context_previous:
                append(home/'contexts.jsonl',dict(observed_at=now().isoformat(),sha256=digest,**context));context_previous=digest
            for event in portfolio.events[event_cursor:]:append(home/'events.jsonl',dict(observed_at=now().isoformat(),**event))
            event_cursor=len(portfolio.events)
            issue_count+=len(issues)
            snapshot=portfolio.snapshot();snapshot.update(observed_at=now().isoformat(),source_health=source_health,
                source_pipeline_ready=all(source_health.values()),coupled_quote_tracking_started=True,
                demo_execution_started=False,issues=issues,cumulative_data_quality_issues=issue_count,
                net_edge_proven=False,live_approved=False)
            snapshot['cost_readiness']=dict(spread='Included in quote model',commission=None,carry=None,
                additional_slippage=None,lot_rounding=None,cash_conversion=None,operating_costs=None,
                broker_fills_reconciled=False)
            write_json(home/'state.json',snapshot);append(home/'equity.jsonl',dict(observed_at=now().isoformat(),**snapshot['summary']))
            append(home/'health.jsonl',dict(observed_at=now().isoformat(),source_health=source_health,issues=issues,cumulative_data_quality_issues=issue_count))
            print(json.dumps(dict(at=now().isoformat(),sources_ok=all(source_health.values()),**snapshot['summary'])),flush=True)
            time.sleep(15)
    except Exception as exc:
        write_json(home/'stopped.json',dict(observed_at=now().isoformat(),reason=f'{type(exc).__name__}: {exc}',
            open_positions_unresolved=bool(portfolio.positions),net_edge_proven=False,live_approved=False))
        raise
    finally:
        write_json(home/'final_state.json',portfolio.snapshot())
        mt5.shutdown();lock.close()


if __name__=='__main__':main()
