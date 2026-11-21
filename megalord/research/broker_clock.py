"""Explicit correction for the empirically audited non-UTC broker feed.

The normal API contract is UTC. FivePercentOnline-Real reported epochs were
10800 seconds ahead on Oct22/23, confirmed independently over the month by BTC
returns. A fresh heartbeat must confirm this mapping on EVERY retrieval.
Unexpected clock/DST changes fail closed instead of silently changing offsets.
"""
import pandas as pd

AUDITED_OFFSETS = {'FivePercentOnline-Real': 10800}


def observed_offset_seconds(mt5, *, now=None):
    account = mt5.account_info() if hasattr(mt5, 'account_info') else None
    server = getattr(account, 'server', '')
    offset = AUDITED_OFFSETS.get(server, 0)
    if not offset:
        return 0
    # A continuously traded heartbeat avoids inferring an offset from stale FX
    # quotes at a weekend. No symbol selection or account mutation is performed.
    tick = mt5.symbol_info_tick('BTCUSD')
    clock = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz='UTC')
    if clock.tzinfo is None:
        raise ValueError('UTC-aware clock required')
    if tick is None or abs(float(tick.time)-clock.timestamp()-offset) > 60:
        raise ValueError('Audited broker clock not confirmed by a fresh heartbeat; data blocked')
    return offset


def normalize_reported_frame(frame, offset_seconds, *, server=''):
    if frame.attrs.get('broker_clock_normalized'):
        raise ValueError('Broker timestamps already normalized')
    out=frame.copy()
    ix=pd.DatetimeIndex(out.index)
    if ix.tz is None:
        raise ValueError('Raw reported epochs must be explicitly UTC-labelled')
    out.index=ix-pd.Timedelta(seconds=offset_seconds)
    out.attrs.update(broker_clock_normalized=True,reported_epoch_offset_seconds=offset_seconds,broker_server=server)
    return out
