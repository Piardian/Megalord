"""Prospective evidence recorder. No orders, notifications or PnL claims.

This is the collection stage, not a broker demo or a portfolio simulator.
Each session starts now; restarting creates a new segment with a visible gap.
"""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from pathlib import Path
import argparse
import gzip
import json
import math
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def utcnow():
    return datetime.now(timezone.utc)


def append(path, row):
    with Path(path).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, default=str, allow_nan=False) + '\n')


def tick_evidence(tick, *, offset, observed_at):
    """Keep stale quotes visible; never silently relabel them as current."""
    reported_ms = int(getattr(tick, 'time_msc', 0) or int(tick.time) * 1000)
    actual = pd.Timestamp(reported_ms - offset * 1000, unit='ms', tz='UTC')
    bid, ask = float(tick.bid), float(tick.ask)
    valid = all(math.isfinite(x) and x > 0 for x in (bid, ask)) and ask >= bid
    age = (pd.Timestamp(observed_at) - actual).total_seconds()
    return dict(reported_time_msc=reported_ms, quote_time_utc=actual.isoformat(),
                observed_at=observed_at.isoformat(), bid=bid if math.isfinite(bid) else None,
                ask=ask if math.isfinite(ask) else None, age_seconds=age,
                executable_quote_valid=valid, fresh=valid and -5 <= age <= 60)


def candidate_evidence(signal, *, observed_at, started_at):
    origin = pd.Timestamp(signal.entry_time)
    if origin.tzinfo is None or pd.isna(origin):
        raise ValueError('Explicit signal time required')
    origin = origin.tz_convert('UTC')
    # No source result or inferred fill enters prospective opportunity evidence.
    return dict(signal_id=signal.signal_id, engine=signal.engine_name,
                symbol=signal.symbol, direction=signal.direction,
                source_entry_time=origin.isoformat(), observed_at=observed_at.isoformat(),
                prospective_origin=bool(pd.Timestamp(started_at) <= origin <= pd.Timestamp(observed_at)),
                source_already_closed=signal.exit_time is not None,
                entry=signal.entry_price, original_stop=signal.sl_price,
                tp1=signal.tp1_price, tp2=signal.tp2_price,
                strategy_regime=signal.strategy_regime.value,
                sub_strategy=signal.sub_strategy, broker_fill_verified=False,
                upstream_causality_verified=False)


def source_paths(loader):
    paths = {k: v for k, v in vars(loader).items() if isinstance(v, Path) and
             k in {'pragma_live_path', 'trawma_paper_path', 'stigma_live_path',
                   'stigma_whitelist_path', 'trawma_whitelist_path', 'checklist_live_path'}}
    paths['stigma_pending'] = loader.stigma_live_path.with_suffix('.pending.json')
    paths.update(macro_shared=ROOT.parent/'Begonya00/shared/macro_bias_gate.json',
                 macro_gateway=ROOT.parent/'Begonya00/macro_engine/gateways/macro_bias_gate.json',
                 news_config=ROOT/'config/news_guard_config.json')
    return paths


def freeze_sources(out):
    """Freeze source and policy, never credentials, terminal or account state."""
    sources = list((ROOT/'megalord').rglob('*.py'))
    sources.extend((ROOT/'megalord').rglob('*.cjs'))
    for project, package in [('TrawmaProject', 'pragma'), ('PragmaProject', 'pragma'),
                             ('StigmaProject', 'stigma')]:
        sources.extend((ROOT.parent/project/package).rglob('*.py'))
        sources.extend((ROOT.parent/project/'config').rglob('*.py'))
        entry=ROOT.parent/project/'run_pipeline.py'
        if entry.exists():sources.append(entry)
        whitelist = ROOT.parent/project/'reports/whitelist.json'
        if whitelist.exists(): sources.append(whitelist)
    clt = Path('C:/Users/piard/.gemini/antigravity/scratch/swing-bos-core')
    sources.extend((clt/'server').rglob('*.ts'))
    sources.extend((clt/'src').rglob('*.ts'))
    manifest = {}
    for index, path in enumerate(sorted(set(sources))):
        data = path.read_bytes()
        dest = out/'sources'/f'{index:04d}_{path.name}'
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        manifest[str(path)] = dict(sha256=sha256(data).hexdigest(), snapshot=str(dest.relative_to(out)))
        # Native workers execute immutable copies, so unrelated work in the
        # user's other project checkouts cannot hot-swap this experiment.
        native_roots={name:ROOT.parent/name for name in ('TrawmaProject','PragmaProject','StigmaProject')}
        native_roots['ChecklistTrigger']=clt
        for name,base in native_roots.items():
            if path.is_relative_to(base):
                runtime=out/'native'/name/path.relative_to(base)
                runtime.parent.mkdir(parents=True,exist_ok=True);runtime.write_bytes(data)
                manifest[str(path)]['verify_path']=str(runtime.resolve())
                break
    cfg = json.loads((ROOT/'config/megalord_config.json').read_text(encoding='utf-8'))
    cfg.pop('telegram', None)
    cfg['target_account'] = 0
    (out/'policy.json').write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    manifest['POLICY'] = {'sha256': sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()}
    (out/'source_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def verify_sources(manifest):
    changed = [p for p, m in manifest.items() if p != 'POLICY' and
               (not Path(m.get('verify_path',p)).exists() or sha256(Path(m.get('verify_path',p)).read_bytes()).hexdigest() != m['sha256'])]
    cfg = json.loads((ROOT/'config/megalord_config.json').read_text(encoding='utf-8'))
    cfg.pop('telegram', None)
    cfg['target_account'] = 0
    if sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest() != manifest['POLICY']['sha256']:
        changed.append('POLICY')
    if changed: raise ValueError('Frozen sources/policy changed: ' + ', '.join(changed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=15)
    parser.add_argument('--source-home', type=Path, help='Isolated four-engine native forward source directory')
    args = parser.parse_args()
    if args.interval < 5: raise ValueError('Minimum interval is 5 seconds')
    # Whitelisted read methods only. No executor/daemon construction, login,
    # order_send, symbol_select, notifier or producer restart exists here.
    import MetaTrader5 as mt5
    from megalord.adapters.stream_loader import ProjectStreamLoader
    from megalord.research.broker_clock import observed_offset_seconds
    started = utcnow()
    out = ROOT/'audit/forward'/started.strftime('%Y%m%dT%H%M%S_%fZ')
    out.mkdir(parents=True, exist_ok=False)
    manifest = freeze_sources(out)
    protocol = dict(started_at=started.isoformat(), stage='PROSPECTIVE_CAPTURE_ONLY',
        collection_cutoff=(started + timedelta(days=30)).isoformat(),
        interval_seconds=args.interval, orders_enabled=False, notifications_enabled=False,
        broker_demo_started=False, portfolio_forward_test_complete=False,
        net_edge_proven=False, live_approved=False,
        review_rule='No performance-based stopping; first 30 calendar days are infrastructure review only.',
        missing_costs='Unknown; no zero-cost substitution and no net performance claim.',
        baseline='Existing policy; four active engines, two disabled; no optimization.',
        limitation='Native producer availability and upstream causality must be audited; this recorder does not create missing signals.')
    (out/'protocol.json').write_text(json.dumps(protocol, indent=2), encoding='utf-8')
    print(str(out), flush=True)
    loader = ProjectStreamLoader()
    paths = source_paths(loader)
    if args.source_home:
        for engine in ('Trawma','Pragma','Stigma','ChecklistTrigger'):
            paths[engine+'_forward_intents']=args.source_home/engine/'intents.jsonl'
            paths[engine+'_heartbeat']=args.source_home/engine/'heartbeat.json'
    seen, previous, cursors = set(), {}, {}
    if not mt5.initialize(): raise RuntimeError('Existing MT5 terminal unavailable')
    account = mt5.account_info()
    pinned = (account.login, account.server) if account else None
    symbols = ['EURUSD', 'GBPUSD', 'AUDUSD', 'USDJPY', 'CADCHF', 'NAS100',
               'BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD', 'LTCUSD', 'DOGUSD', 'BTCEUR', 'ETHEUR', 'XAUUSD']
    try:
        while True:
            now = utcnow()
            if (out/'STOP').exists() or now >= started + timedelta(days=30):
                append(out/'events.jsonl', dict(observed_at=now.isoformat(), event='COLLECTION_STOPPED',
                    reason='STOP file or predeclared 30-day capture cutoff'))
                break
            status = dict(observed_at=now.isoformat(), stage='PROSPECTIVE_CAPTURE_ONLY',
                net_return=None, live_approved=False, errors=[], sources={}, quotes={})
            try:
                verify_sources(manifest)
                current = mt5.account_info()
                if current is None or (current.login, current.server) != pinned:
                    raise ValueError('Attached terminal account changed; capture stopped')
                offset = observed_offset_seconds(mt5, now=now)
                status['broker_server'] = current.server
                status['reported_epoch_offset_seconds'] = offset
                tick_batches = []
                for name, path in paths.items():
                    if not path.exists():
                        status['sources'][name] = {'exists': False}
                        continue
                    data = path.read_bytes()
                    digest = sha256(data).hexdigest()
                    status['sources'][name] = dict(exists=True, sha256=digest,
                        modified_age_seconds=now.timestamp()-path.stat().st_mtime)
                    if previous.get(name) != digest:
                        dest = out/'observations'/f'{name}_{digest}.json.gz'
                        dest.parent.mkdir(exist_ok=True)
                        with gzip.open(dest, 'wb') as stream: stream.write(data)
                        append(out/'source_observations.jsonl', dict(name=name,
                            observed_at=utcnow().isoformat(), sha256=digest, file=str(dest.relative_to(out))))
                        previous[name] = digest
                # Complete inter-poll tick batches are kept, not just the latest quote.
                for symbol in symbols:
                    tick = mt5.symbol_info_tick(symbol)
                    if tick is None:
                        status['quotes'][symbol] = {'available': False}
                        continue
                    quote_observed = utcnow()
                    evidence = tick_evidence(tick, offset=offset, observed_at=quote_observed)
                    append(out/'quotes.jsonl', dict(symbol=symbol, **evidence))
                    status['quotes'][symbol] = evidence
                    beginning = cursors.get(symbol, started)
                    ticks = mt5.copy_ticks_range(symbol,
                        beginning + pd.Timedelta(seconds=offset),
                        now + pd.Timedelta(seconds=offset), mt5.COPY_TICKS_ALL)
                    if ticks is None:
                        status['errors'].append(f'{symbol}: tick batch unavailable')
                    elif len(ticks):
                        frame = pd.DataFrame(ticks)
                        frame['utc_time_msc'] = frame['time_msc'] - offset*1000
                        frame['symbol'] = symbol
                        frame['observed_at'] = utcnow().isoformat()
                        tick_batches.append(frame)
                        cursors[symbol] = now
                    else:
                        status['errors'].append(f'{symbol}: empty tick batch; no fill inferred')
                        cursors[symbol] = now
                if tick_batches:
                    frame = pd.concat(tick_batches, ignore_index=True)
                    dest = out/'ticks'/(now.strftime('%Y%m%dT%H%M%S_%f')+'.parquet')
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    frame.to_parquet(dest, index=False)
                    append(out/'tick_manifest.jsonl', dict(observed_at=utcnow().isoformat(),
                        query_to=now.isoformat(), rows=len(frame), file=str(dest.relative_to(out)),
                        sha256=sha256(dest.read_bytes()).hexdigest()))
                methods=[('Trawma', loader.load_trawma_whitelist),
                        ('Pragma', loader.load_pragma), ('Stigma', loader.load_stigma_whitelist),
                        ('ChecklistTrigger', loader.load_checklist_trigger)]
                if args.source_home:
                    from megalord.research.forward_stream import load_forward_intents
                    methods=[('IsolatedNativeForward',lambda:load_forward_intents(args.source_home,observed_at=utcnow()))]
                    health={}
                    for engine in ('Trawma','Pragma','Stigma','ChecklistTrigger'):
                        file=args.source_home/engine/'heartbeat.json'
                        hb=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
                        age=(utcnow()-datetime.fromisoformat(hb['completed_at'])).total_seconds() if hb else None
                        health[engine]=dict(ok=bool(hb.get('ok')) and age is not None and 0<=age<120,
                            age_seconds=age,error=hb.get('error'))
                    status['forward_producers']=health
                    status['source_pipeline_ready']=all(h['ok'] for h in health.values())
                for engine, method in methods:
                    try:
                        for signal in method():
                            if signal.signal_id in seen: continue
                            row = candidate_evidence(signal, observed_at=utcnow(), started_at=started)
                            append(out/'opportunities.jsonl', row)
                            seen.add(signal.signal_id)
                    except Exception as exc:
                        status['errors'].append(f'{engine}: {type(exc).__name__}: {exc}')
                status['observed_unique_source_signals'] = len(seen)
                status['portfolio_trial_ready'] = False
                status['readiness_blockers'] = [
                    'Capture stage does not yet execute a coupled prospective portfolio',
                    'Broker fills and complete fees/carry are not measured']
                if not status.get('source_pipeline_ready'):
                    status['readiness_blockers'].append('Four-engine native forward source health not confirmed')
                status['recording'] = True
            except Exception as exc:
                status['recording'] = False
                status['errors'].append(f'{type(exc).__name__}: {exc}')
            append(out/'health.jsonl', status)
            (out/'status.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
            print(json.dumps(dict(at=now.isoformat(), recording=status['recording'],
                errors=len(status['errors']), seen=len(seen))), flush=True)
            if args.once or not status['recording']: break
            time.sleep(args.interval)
    finally:
        mt5.shutdown()


if __name__ == '__main__':
    main()
