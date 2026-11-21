"""Isolated native producer workers. Read-only broker; no notifications/orders.

Each engine runs in a separate interpreter (Trawma and Pragma share a package
name). State/output lives exclusively in the specified research directory.
"""
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ENGINES = ('Trawma', 'Pragma', 'Stigma', 'ChecklistTrigger')
MINUTES = {'M1': 1, 'M5': 5, 'M15': 15, 'H1': 60, 'H4': 240}


def now(): return datetime.now(timezone.utc)


def clean(value):
    if isinstance(value, dict): return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value): return None
    if hasattr(value, 'item'): return clean(value.item())
    return value


def write_json(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(clean(row), default=str, allow_nan=False, indent=2), encoding='utf-8')
    temp.replace(path)


def append(path, row):
    with Path(path).open('a', encoding='utf-8') as f:
        f.write(json.dumps(clean(row), default=str, allow_nan=False)+'\n')


def forbidden(*a, **kw): raise RuntimeError('Forward research worker forbids orders/accounts/notifications')


class ReadOnlyBars:
    def __init__(self, mt5, home):
        self.mt5, self.home = mt5, home
        self.evidence = []

    def __enter__(self): return self
    def __exit__(self, *a): pass

    def rates(self, symbol, tf, count=350):
        from megalord.research.broker_clock import observed_offset_seconds
        offset = observed_offset_seconds(self.mt5)
        for attempt in range(3):
            # First MT5 history request may return an old terminal cache while
            # asynchronously starting synchronization. Retry read-only; never
            # call symbol_select or accept that cached bar as current.
            raw = self.mt5.copy_rates_from_pos(symbol, getattr(self.mt5, 'TIMEFRAME_'+tf), 0, count+1)
            if raw is None or len(raw) < count:
                raise ValueError(f'{symbol} {tf}: insufficient history')
            frame = pd.DataFrame(raw)
            frame['time'] = pd.to_datetime(frame['time']-offset, unit='s', utc=True)
            frame = frame.set_index('time').sort_index()
            clock=pd.Timestamp.now(tz='UTC')
            frame=frame.loc[frame.index+pd.Timedelta(minutes=MINUTES[tf])<=clock].tail(count)
            close = frame.index[-1]+pd.Timedelta(minutes=MINUTES[tf])
            age = (clock-close).total_seconds()
            if len(frame)==count and 0 <= age <= MINUTES[tf]*60+120:break
            if attempt==2:
                raise ValueError(f'{symbol} {tf}: stale/forming history; last close {close}, age {age:.0f}s')
            time.sleep(.5)
        dest = self.home/'bars'/f'{symbol}_{tf}_{frame.index[-1].value}.parquet'
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists(): frame.to_parquet(dest)
        self.evidence.append(dict(symbol=symbol, timeframe=tf, count=len(frame),
            last_close=str(close), observed_at=now().isoformat(),
            file=str(dest.relative_to(self.home)), sha256=sha256(dest.read_bytes()).hexdigest()))
        return frame


def emit(home, engine, symbol, side, origin, entry, stop, tp1, tp2, sub, metadata=None):
    origin = pd.Timestamp(origin)
    if origin.tzinfo is None: raise ValueError('Native signal origin requires timezone')
    at = now()
    if origin > pd.Timestamp(at): raise ValueError('Future native signal rejected')
    norm = symbol.replace('USDT','USD').replace('DOGEUSD','DOGUSD')
    row = dict(schema_version=1, source_kind='NATIVE_FORWARD_INTENT', engine=engine,
        signal_id=f'{engine}:{norm}:{sub}:{origin.isoformat()}:{side}', symbol=norm,
        direction=side, origin_at=origin.isoformat(), available_at=at.isoformat(),
        entry_price=float(entry), sl_price=float(stop), tp1_price=float(tp1), tp2_price=float(tp2),
        sub_strategy=sub, metadata=metadata or {}, broker_fill_verified=False)
    # The append-only publication is deduplicated across worker restarts.
    from megalord.research.forward_stream import SPECS
    file = home/('intents.jsonl' if norm in SPECS[engine][1] else 'excluded_intents.jsonl')
    existing = {json.loads(line)['signal_id'] for line in file.read_text(encoding='utf-8').splitlines()} if file.exists() else set()
    if row['signal_id'] not in existing: append(file, row)


def setup_engine(engine, home, client):
    if engine != 'ChecklistTrigger':
        immutable=home.parent/'native'/(engine+'Project')
        project = immutable if immutable.exists() else ROOT.parent/(engine+'Project')
        sys.path.insert(0, str(project))
    if engine == 'Trawma':
        import pragma.live_scanner as native
        from pragma.data.binance_data import BinanceDataClient, filter_closed_bars
        # No native execution bridge or notifier is constructed.
        scanner = native.LiveSentinelScanner(notifier=SimpleNamespace(),
            bridge=SimpleNamespace(paper_tracker=None, execute_signal=lambda s: None),
            macro_path=ROOT.parent/'Begonya00/shared/macro_bias_gate.json')
        bn = BinanceDataClient(no_cache=True, futures_only=True, timeout=10)
        original_rates=bn.rates
        def rates(symbol,tf,count):
            frame=filter_closed_bars(original_rates(symbol,tf,count),tf)
            close=frame.index[-1]+pd.Timedelta(minutes=MINUTES[tf])
            if (pd.Timestamp.now(tz='UTC')-close).total_seconds()>MINUTES[tf]*60+120:
                raise ValueError(f'Stale Binance data for {symbol} {tf}')
            file=home/'bars'/f'binance_{symbol}_{tf}_{frame.index[-1].value}.parquet'
            file.parent.mkdir(exist_ok=True)
            if not file.exists():frame.to_parquet(file)
            client.evidence.append(dict(symbol=symbol,timeframe=tf,count=len(frame),last_close=str(close),
                observed_at=now().isoformat(),file=str(file.relative_to(home)),sha256=sha256(file.read_bytes()).hexdigest()))
            return frame
        bn.rates=rates
        def publish(sig):
            if sig is None:return
            emit(home,engine,sig.symbol,sig.side,
                 pd.Timestamp(sig.bar_time)+pd.Timedelta(minutes=MINUTES[sig.timeframe]),
                 sig.entry_price,sig.stop_loss,sig.tp1,sig.tp2,sig.engine,
                 dict(timeframe=sig.timeframe,price_source='binance_futures' if 'USDT' in sig.symbol else 'mt5',
                      macro_bias=sig.macro_bias,native_signal=asdict(sig)))
        def cycle():
            emitted = []
            for symbol in native.mt5_watch_symbols():
                sig = scanner.scan_mt5_symbol(client, symbol, macro_required=True)
                if sig: publish(sig);emitted.append(sig)
            btc = filter_closed_bars(bn.rates('BTCUSDT','H1',150), 'H1')
            for symbol in ['BTCUSDT','SOLUSDT','DOGEUSDT','XRPUSDT']:
                sig = scanner.scan_crypto_symbol(bn, symbol, btc_frame=None if symbol=='BTCUSDT' else btc)
                if sig: publish(sig);emitted.append(sig)
            return dict(signals=len(emitted), last_processed_bars={str(k):str(v) for k,v in scanner._last_processed_bar_time.items()})
        return cycle
    if engine == 'Pragma':
        os.environ['PRAGMA_MACRO_GATE_PATH']=str(ROOT.parent/'Begonya00/shared/macro_bias_gate.json')
        import pragma.run_pipeline as native
        native.MT5DataClient = lambda: client
        def cycle():
            results = []
            for symbol in ['EURUSD','CADCHF']:
                report = native.run_pipeline(symbol=symbol, source='mt5', bars_count=3000,
                    notifier=SimpleNamespace(notify=lambda *a,**k: {'sent':False}), notify_pois=False)
                for tr in report['triggers']:
                    if tr['execute']:
                        emit(home,engine,symbol,tr['direction'],
                            pd.Timestamp(report['bars']['last_closed_m1_time'])+pd.Timedelta(minutes=1),
                            tr['entry_price'],tr['stop_loss'],tr['take_profit_1'],tr['take_profit_2'],tr['poi_type'],
                            dict(poi_type=tr['poi_type'], regime=report['regime'], macro=report['macro']))
                results.append(dict(symbol=symbol, bars=report['bars'], macro=report['macro'],triggers=len(report['triggers'])))
            write_json(home/'latest_report.json', results)
            return dict(symbols=results)
        return cycle
    if engine == 'Stigma':
        spec = importlib.util.spec_from_file_location('native_stigma_live', project/'run_pipeline.py')
        native = importlib.util.module_from_spec(spec); spec.loader.exec_module(native)
        from stigma.execution_notifier import LivePositionTracker, LivePerformanceTracker
        native.fetch_mt5 = lambda symbol,tf,bars: client.rates(symbol,tf,bars)
        native_binance=native.fetch_binance
        def binance(symbol,tf,bars,**kw):
            frame=native_binance(symbol,tf,bars,**kw)
            close=frame.index[-1]+pd.Timedelta(minutes=MINUTES[tf])
            if (pd.Timestamp.now(tz='UTC')-close).total_seconds()>MINUTES[tf]*60+120:
                raise ValueError('Stale Stigma Binance history')
            file=home/'bars'/f'binance_{symbol}_{tf}_{frame.index[-1].value}.parquet'
            file.parent.mkdir(exist_ok=True)
            if not file.exists():frame.to_parquet(file)
            client.evidence.append(dict(symbol=symbol,timeframe=tf,count=len(frame),last_close=str(close),
                observed_at=now().isoformat(),file=str(file.relative_to(home)),sha256=sha256(file.read_bytes()).hexdigest()))
            return frame
        native.fetch_binance=binance
        tracker = LivePositionTracker(home/'positions.json', LivePerformanceTracker(home/'native_model_performance.json'))
        def cycle():
            signals, clocks = native.scan_whitelisted_strategies(
                whitelist_file=project/'reports/whitelist.json', position_tracker=tracker,
                send_alerts=False, verbose=False)
            whitelist=json.loads((project/'reports/whitelist.json').read_text())['whitelisted_strategies']
            expected={':'.join(str(st[k]).upper() for k in ('source','symbol','timeframe')) for st in whitelist}
            if set(clocks)!=expected:raise ValueError('Stigma missing source clocks: '+str(expected-set(clocks)))
            for s in signals:
                emit(home,engine,s['symbol'],s['side'],
                     pd.Timestamp(s['bar_time'])+pd.Timedelta(minutes=MINUTES[s['timeframe']]),
                     s['entry'],s['stop_loss'],s['tp1'],s['tp2'],s['strategy_mode'],s)
            return dict(signals=len(signals), last_closed_bars=clocks,
                occupancy_basis='Isolated native paper producer; not actual portfolio fills')
        return cycle
    def cycle():
        frames = {}
        for symbol in ['EURUSD','GBPUSD','AUDUSD']:
            frames[symbol] = {}
            for tf, label in [('M15','15m'),('H1','1h'),('H4','4h')]:
                frame = client.rates(symbol, tf, 500)
                rows=[]
                for t,r in frame.iterrows():
                    rows.append(dict(timestamp=t.value//1_000_000,open=r.open,high=r.high,low=r.low,close=r.close,volume=r.tick_volume))
                frames[symbol][label] = rows
        input_path=home/'input.json';write_json(input_path,dict(observed_at=now().isoformat(),frames=frames))
        result=subprocess.run(['C:/Program Files/nodejs/node.exe',str(ROOT/'megalord/research/forward_clt.cjs'),str(home)],
            cwd=home,capture_output=True,text=True,timeout=60)
        (home/'node_stdout.log').write_text(result.stdout,encoding='utf-8')
        (home/'node_stderr.log').write_text(result.stderr,encoding='utf-8')
        if result.returncode: raise ValueError('CLT worker failed: '+result.stderr[-1200:])
        return json.loads((home/'native_result.json').read_text(encoding='utf-8'))
    return cycle


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--engine',required=True,choices=ENGINES)
    p.add_argument('--home',required=True,type=Path)
    p.add_argument('--once',action='store_true')
    args=p.parse_args();home=args.home.resolve();home.mkdir(parents=True,exist_ok=True)
    import msvcrt
    lock=(home/'worker.lock').open('a+b')
    if lock.tell()==0:lock.write(b'0');lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    import MetaTrader5 as mt5
    if not mt5.initialize(): raise RuntimeError('Attached MT5 unavailable')
    pinned=mt5.account_info()
    for method in ['order_send','order_check','login','symbol_select']:
        setattr(mt5,method,forbidden)
    # Prevent accidental notifications even if a native path changes later.
    import urllib.request
    native_urlopen=urllib.request.urlopen
    def public_only(url,*a,**k):
        target=url.full_url if hasattr(url,'full_url') else str(url)
        if 'api.telegram.org' in target: return forbidden()
        return native_urlopen(url,*a,**k)
    urllib.request.urlopen=public_only
    client=ReadOnlyBars(mt5,home)
    cycle=setup_engine(args.engine,home,client)
    (home/'intents.jsonl').touch(exist_ok=True)
    (home/'worker.pid').write_text(str(os.getpid()),encoding='ascii')
    try:
        while not (home.parent/'STOP').exists():
            at=now();client.evidence=[]
            protocol_path=home.parent/'protocol.json'
            if protocol_path.exists():
                cutoff=json.loads(protocol_path.read_text())['collection_cutoff']
                if pd.Timestamp(at)>=pd.Timestamp(cutoff):break
            status=dict(engine=args.engine,started_at=at.isoformat(),orders_enabled=False,notifications_enabled=False)
            try:
                manifest_path=home.parent/'source_manifest.json'
                if manifest_path.exists():
                    from megalord.research.forward_capture import verify_sources
                    verify_sources(json.loads(manifest_path.read_text(encoding='utf-8')))
                acc=mt5.account_info()
                if acc is None or (acc.login,acc.server)!=(pinned.login,pinned.server):
                    raise PermissionError('Attached account changed')
                macro=ROOT.parent/'Begonya00/shared/macro_bias_gate.json'
                if macro.exists():
                    raw=macro.read_bytes();digest=sha256(raw).hexdigest()
                    dest=home/('macro_'+digest+'.json');dest.write_bytes(raw)
                    status['macro_before']=dict(observed_at=now().isoformat(),sha256=digest,file=dest.name)
                status['result']=cycle();status['ok']=True
                if macro.exists():
                    raw=macro.read_bytes();digest=sha256(raw).hexdigest()
                    dest=home/('macro_'+digest+'.json');dest.write_bytes(raw)
                    status['macro_after']=dict(observed_at=now().isoformat(),sha256=digest,file=dest.name)
                    status['macro_stable_during_cycle']=status.get('macro_before',{}).get('sha256')==digest
            except Exception as exc:
                status.update(ok=False,error=f'{type(exc).__name__}: {exc}')
            status.update(completed_at=now().isoformat(),bars=client.evidence)
            write_json(home/'heartbeat.json',status);append(home/'heartbeats.jsonl',status)
            print(json.dumps(dict(engine=args.engine,ok=status['ok'],error=status.get('error'))),flush=True)
            if args.once:break
            time.sleep(30)
    finally:
        mt5.shutdown()
        lock.close()


if __name__=='__main__':main()
