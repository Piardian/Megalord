"""Authorized Telegram sidecar: observes research files, never alters trading.

Reads from current native-source/portfolio registries. Existing history is
skipped on first enablement. Failed sends stay queued, with persistent dedup.
"""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from html import escape
from megalord.research.notification_metrics import scoreboard,format_scoreboard
import json
import time

ROOT=Path(__file__).resolve().parents[2]
PREFIX='🧪 <b>MEGALORD — GÖLGE TEST</b>\n'


def format_message(kind,row,stats=None):
    message=_format_message(kind,row)
    if message and stats is not None:message+=format_scoreboard(stats)
    return message


def _format_message(kind,row):
    symbol=escape(str(row.get('symbol','')));engine=escape(str(row.get('engine',row.get('engine_name',''))))
    side={'LONG':'ALIŞ','SHORT':'SATIŞ'}.get(row.get('direction'),str(row.get('direction','')))
    if kind=='signal':
        return PREFIX+f'Sinyal bulundu: {engine} / {symbol} / {escape(side)}\nHenüz giriş onayı değil.'
    if kind=='event':
        if row.get('kind')=='MODELED_ENTRY':
            return PREFIX+f'Gölge giriş: {engine} / {symbol} / {escape(side)}\nGiriş: {row.get("fill")}\nSL: {row.get("stop")}\nTP1: {row.get("tp1")} | TP2: {row.get("tp2")}\nGerçek emir gönderilmedi.'
        if row.get('kind')=='MODELED_EXIT_LEG':
            r=float(row['allocated_quote_r'])
            return PREFIX+f'Gölge çıkış: {escape(str(row.get("signal_id","")))}\nBacak: {row.get("leg")} / Sebep: {escape(str(row.get("reason","")))}\nRisk ağırlıklı fiyat sonucu: {r:+.3f}R\nTüm giderler sonrası net kâr değildir.'
        return None
    if kind=='decision':
        status=row.get('status','')
        if status in ('MODEL_APPROVED','ALREADY_PROCESSED','BEFORE_PROSPECTIVE_START'):return None
        evidence=row.get('evidence',{});signal=evidence.get('signal',{})
        decision=evidence.get('observed_arbiter',{})
        action=decision.get('action',status)
        reasons={
            'NATURAL_BAND_REQUIRED':'Üst zaman yönü bu dönüş işlemine izin vermedi.',
            'HTF_CONFLICT_VETO':'Sinyal üst zaman yönüne ters.',
            'QUALITY_ENTRY_DRIFT':'Fiyat girişten uzak. Bekleniyor.',
            'QUALITY_SPREAD_TOO_HIGH':'Alış-satış farkı yüksek.',
            'QUALITY_LOW_LIVE_TP1_RR':'Güncel fiyatla hedefin getirisi yetersiz.',
            'SOURCE_HEALTH_RETRY':'Veri kaynakları hazır değil. Bekleniyor.',
            'STALE_QUOTE_RETRY':'Güncel fiyat bekleniyor.',
            'MISSING_FRESH_QUOTE_RETRY':'Fiyat alınamadı. Tekrar denenecek.',
            'CLONE_CAPPED':'Benzer işlemler için risk sınırı dolu.'}
        reason=reasons.get(action,'Mevcut giriş kuralları izin vermedi.')
        return PREFIX+f'Giriş yapılmadı: {escape(str(signal.get("engine_name","")))} / {escape(str(signal.get("symbol","")))}\n{reason}\nKod: {escape(str(action))}'
    return None


def read_new_lines(path,offset):
    path=Path(path)
    if not path.exists():return [],offset
    with path.open('rb') as stream:
        if path.stat().st_size<offset:raise ValueError('Notification source was truncated')
        stream.seek(offset);raw=stream.read()
    boundary=raw.rfind(b'\n')
    if boundary<0:return [],offset
    complete=raw[:boundary+1]
    return [json.loads(line) for line in complete.decode('utf-8').splitlines() if line.strip()],offset+len(complete)


def notification_key(kind,row):
    # Repeated pending-entry checks generate a single message per reason.
    if kind=='decision':
        action=row.get('evidence',{}).get('observed_arbiter',{}).get('action',row.get('status'))
        body=[kind,row.get('signal_id'),row.get('status'),action]
    elif kind=='signal':body=[kind,row.get('signal_id')]
    else:body=[kind,row.get('kind'),row.get('signal_id'),row.get('leg'),row.get('time')]
    return sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()


def save(path,state):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(state,indent=2),encoding='utf-8');temp.replace(path)


def main():
    from megalord.megalord_daemon import MegalordTelegramNotifier
    directory=ROOT/'audit/notifications';directory.mkdir(parents=True,exist_ok=True)
    import msvcrt
    lock=(directory/'worker.lock').open('a+b')
    if lock.tell()==0:lock.write(b'0');lock.flush()
    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    notifier=MegalordTelegramNotifier(config=json.loads((ROOT/'config/megalord_config.json').read_text()))
    if not notifier.is_configured():raise RuntimeError('Telegram destination missing')
    file=directory/'state.json'
    state=json.loads(file.read_text()) if file.exists() else dict(enabled_at=datetime.now(timezone.utc).isoformat(),
        tails={},sent=[],pending={},health=None,started_message_sent=False)
    first=not file.exists()
    def sources():
        native=Path(json.loads((ROOT/'audit/forward_sources/current.json').read_text())['home'])
        portfolio=Path(json.loads((ROOT/'audit/forward_portfolio/current.json').read_text())['home'])
        paths=[('signal',native/e/'intents.jsonl') for e in ('Trawma','Pragma','Stigma','ChecklistTrigger')]
        paths.extend([('decision',portfolio/'decisions.jsonl'),('event',portfolio/'events.jsonl')])
        return native,portfolio,paths
    if first:
        for _,path in sources()[2]:state['tails'][str(path)]=path.stat().st_size if path.exists() else 0
        save(file,state)
    try:
        while not (directory/'STOP').exists():
            if not state['started_message_sent']:
                if notifier.send_alert(PREFIX+'Bildirimler açıldı.\nSinyal, giriş, çıkış ve sistem sorunları bildirilecek.\nGerçek emirler kapalı.'):
                    state['started_message_sent']=True
            native,portfolio,paths=sources()
            pf=portfolio/'state.json'
            portfolio_state=json.loads(pf.read_text()) if pf.exists() else {}
            events,_=read_new_lines(portfolio/'events.jsonl',0)
            broker={};broker_registry=ROOT/'audit/account_observer/current.json'
            if broker_registry.exists():
                broker_home=Path(json.loads(broker_registry.read_text())['home'])
                broker_file=broker_home/'status.json'
                if broker_file.exists():broker=json.loads(broker_file.read_text())
            stats=scoreboard(portfolio_state,events,broker)
            state['scoreboard']=stats
            if state.get('format_version',1)<2:
                state['pending']['format-v2']=PREFIX+'Bildirimlere PnL, TP1, TP2 ve SL sayaçları eklendi.\nÖlçülmeyen net sonuç boş bırakılır.'+format_scoreboard(stats)
                state['format_version']=2
            for kind,path in paths:
                rows,offset=read_new_lines(path,state['tails'].get(str(path),0))
                for row in rows:
                    message=format_message(kind,row,stats)
                    if message:
                        key=notification_key(kind,row)
                        if key not in state['sent']:state['pending'][key]=message
                state['tails'][str(path)]=offset
            problems=[];now=datetime.now(timezone.utc)
            for engine in ('Trawma','Pragma','Stigma','ChecklistTrigger'):
                hb=native/engine/'heartbeat.json'
                value=json.loads(hb.read_text()) if hb.exists() else {}
                age=(now-datetime.fromisoformat(value['completed_at'])).total_seconds() if value else 99999
                if not value.get('ok') or age>120:problems.append(engine)
            pf=portfolio/'state.json'
            value=json.loads(pf.read_text()) if pf.exists() else {}
            age=(now-datetime.fromisoformat(value['observed_at'])).total_seconds() if value else 99999
            if age>90 or (portfolio/'stopped.json').exists():problems.append('Portföy takibi')
            health=','.join(problems)
            if health!=state['health']:
                if problems:state['pending']['health:'+now.isoformat()]=PREFIX+'Sistem kontrolü gerekiyor:\n'+escape(health)+'\nYeni sonuçları doğrulanmış saymayın.'
                elif state['health']:state['pending']['recovered:'+now.isoformat()]=PREFIX+'Veri kaynakları ve portföy takibi yeniden çalışıyor.'
                state['health']=health
            save(file,state) # Queue survives a send failure or process restart.
            for key,message in list(state['pending'].items()):
                if notifier.send_alert(message):
                    state['sent'].append(key);del state['pending'][key];save(file,state)
                else:break
            state['last_checked_at']=now.isoformat();save(file,state)
            print(json.dumps(dict(at=now.isoformat(),startup_delivered=state['started_message_sent'],
                queued=len(state['pending']),delivered=len(state['sent']),health_issues=problems)),flush=True)
            time.sleep(10)
    finally:lock.close()


if __name__=='__main__':main()
