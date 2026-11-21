"""Notification-only counters; never alters decisions or invents missing fees."""
import math
from html import escape


def scoreboard(portfolio,events,broker=None):
    entries={};exits={};seen=set();pnl=0.;tp1=tp2=0
    for event in events:
        sid=event.get('signal_id')
        if event.get('kind')=='MODELED_ENTRY':entries[sid]=event;continue
        if event.get('kind')!='MODELED_EXIT_LEG':continue
        key=(sid,event.get('leg'),event.get('time'))
        if key in seen:continue
        seen.add(key);exits.setdefault(sid,[]).append(event)
        pnl+=float(event['allocated_quote_r'])
        if event.get('reason')=='TP':
            if event.get('leg')==1:tp1+=1
            elif event.get('leg')==2:tp2+=1
    closed=[];sl=protected=0
    for sid,rows in exits.items():
        if {r.get('leg') for r in rows}!={1,2}:continue
        closed.append(sid)
        value=sum(float(r['allocated_quote_r']) for r in rows)
        if any(r.get('reason')=='STOP' for r in rows):
            if value< -1e-12:sl+=1
            else:protected+=1
    unrealized=portfolio.get('summary',{}).get('unrealized_weighted_quote_r')
    broker=broker or {};linked=broker.get('closed_linked_signals',[])
    known={};duplicates=set()
    for r in linked:
        sid=r.get('signal_id')
        if sid in known:duplicates.add(sid)
        known[sid]=r
    # Cash from unrelated The5ers positions is never Megalord test PnL.
    net=None
    if closed and broker.get('signal_attribution_independently_verified') is True and all(sid in known and sid not in duplicates for sid in closed):
        values=[known[sid].get('broker_deal_cash') for sid in closed]
        if all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):net=sum(values)
    return dict(realized_quote_r=pnl,unrealized_quote_r=unrealized,
        total_entries=len(entries),closed_trades=len(closed),tp1_hits=tp1,tp2_hits=tp2,
        sl_closed_trades=sl,protected_closed_trades=protected,
        broker_after_deductions_cash=net,account_currency=broker.get('account_currency'),
        complete_operating_net_cash=None)


def format_scoreboard(stats):
    unrealized=stats.get('unrealized_quote_r')
    open_text=f'{unrealized:+.3f}R' if isinstance(unrealized,(int,float)) and math.isfinite(unrealized) else 'ölçülmedi'
    net=stats.get('broker_after_deductions_cash');currency=stats.get('account_currency')
    net_text=f'{net:+.2f} {escape(str(currency or "hesap para birimi"))}' if net is not None else 'ölçülmedi (doğrulanmış broker kaydı yok)'
    return ('\n\n<b>BU TESTİN GÜNCEL TOPLAMI</b>'
        f'\nKapanan işlem: {stats["closed_trades"]} / Gölge giriş: {stats["total_entries"]}'
        f'\nGerçekleşen fiyat PnL: {stats["realized_quote_r"]:+.3f}R'
        f'\nAçık fiyat PnL: {open_text}'
        f'\nKomisyon/swap/ücret sonrası net: {net_text}'
        f'\nTP1: {stats["tp1_hits"]} | TP2: {stats["tp2_hits"]}'
        f'\nSL ile kapanan işlem: {stats["sl_closed_trades"]}'
        f'\nBE/koruma ile kapanan: {stats["protected_closed_trades"]}'
        '\nTP sayıları hedef vuruşudur; SL sayısı kapanmış işlemdir.'
        '\nFiyat PnL spreadi içerir. Bilinmeyen masraf sıfır sayılmaz.'
        '\nİşletme giderleri ayrıca ölçülür.')
