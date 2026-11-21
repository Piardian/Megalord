"""Launch one isolated four-engine shadow source set and its evidence recorder."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json
import subprocess
import sys

from megalord.research.forward_capture import freeze_sources

ROOT=Path(__file__).resolve().parents[2]


def main():
    registry=ROOT/'audit/forward_sources/current.json'
    if registry.exists() and sys.platform=='win32':
        import msvcrt
        previous=Path(json.loads(registry.read_text(encoding='utf-8'))['home'])
        for engine in ('Trawma','Pragma','Stigma','ChecklistTrigger'):
            lockpath=previous/engine/'worker.lock'
            if lockpath.exists():
                with lockpath.open('r+b') as stream:
                    try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                    except OSError:raise RuntimeError('An isolated source set is already running: '+str(previous))
                    stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
    now=datetime.now(timezone.utc)
    home=ROOT/'audit/forward_sources'/now.strftime('%Y%m%dT%H%M%S_%fZ')
    home.mkdir(parents=True,exist_ok=False)
    freeze_sources(home)
    (home/'protocol.json').write_text(json.dumps(dict(started_at=now.isoformat(),
        collection_cutoff=(now+timedelta(days=30)).isoformat(),
        purpose='Isolated native prospective sources; no broker execution',
        enabled_engines=['Trawma','Pragma','Stigma','ChecklistTrigger'],
        disabled_engines=['Begonya','Begonya00'],parameters_optimized=False,
        notifications_enabled=False,orders_enabled=False,
        portfolio_forward_test_started=False,live_approved=False),indent=2),encoding='utf-8')
    processes={}
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    for engine in ('Trawma','Pragma','Stigma','ChecklistTrigger'):
        target=home/engine;target.mkdir()
        with (target/'stdout.log').open('w',encoding='utf-8') as stdout,(target/'stderr.log').open('w',encoding='utf-8') as stderr:
            process=subprocess.Popen([sys.executable,'-u','-m','megalord.research.forward_sources',
                '--engine',engine,'--home',str(target)],cwd=ROOT,stdout=stdout,stderr=stderr,creationflags=flags)
            processes[engine]=process.pid
    with (home/'capture_stdout.log').open('w',encoding='utf-8') as stdout,(home/'capture_stderr.log').open('w',encoding='utf-8') as stderr:
        process=subprocess.Popen([sys.executable,'-u','-m','megalord.research.forward_capture',
            '--source-home',str(home)],cwd=ROOT,stdout=stdout,stderr=stderr,creationflags=flags)
        processes['capture']=process.pid
    record=dict(home=str(home),started_at=now.isoformat(),processes=processes,
        note='PID registry is a snapshot; health timestamps determine current liveness.')
    (home/'processes.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    (ROOT/'audit/forward_sources/current.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record,indent=2))


if __name__=='__main__':main()
