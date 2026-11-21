"""Strict prospective-only bridge for isolated native forward workers."""
from pathlib import Path
import json
import math

import pandas as pd

from megalord.types import TradeSignal, MathFamily, StrategyRegime
from megalord.adapters.stream_loader import (
    TRAWMA_WHITELIST, PRAGMA_WHITELIST, STIGMA_WHITELIST, CHECKLIST_WHITELIST,
)

SPECS = {
    'Trawma': (MathFamily.FAMILY_ORDERFLOW, TRAWMA_WHITELIST),
    'Pragma': (MathFamily.FAMILY_AMT_VWAP, PRAGMA_WHITELIST),
    'Stigma': (MathFamily.FAMILY_STAT_QUANT, STIGMA_WHITELIST),
    'ChecklistTrigger': (MathFamily.FAMILY_SMC, CHECKLIST_WHITELIST),
}


def parse_intent(row, *, observed_at):
    engine=row['engine'];family,whitelist=SPECS[engine]
    if row.get('source_kind')!='NATIVE_FORWARD_INTENT' or row.get('schema_version')!=1:
        raise ValueError('Native forward intent schema required')
    if any(k in row for k in ('exit_time','raw_r_result','realized_r','net_r')):
        raise ValueError('Future outcome fields forbidden')
    at=pd.Timestamp(observed_at);origin=pd.Timestamp(row['origin_at']);available=pd.Timestamp(row['available_at'])
    if any(t.tzinfo is None for t in (at,origin,available)) or not origin<=available<=at:
        raise ValueError('Causal timezone-aware origin/publication required')
    if row['symbol'] not in whitelist or row['direction'] not in ('LONG','SHORT'):
        raise ValueError('Native symbol/direction outside frozen Megalord universe')
    entry,stop,tp1,tp2=[float(row[k]) for k in ('entry_price','sl_price','tp1_price','tp2_price')]
    sign=1 if row['direction']=='LONG' else -1
    if not all(math.isfinite(x) for x in (entry,stop,tp1,tp2)) or min(entry,stop)<=0 or sign*(entry-stop)<=0:
        raise ValueError('Invalid original entry/stop geometry')
    if tp2<=0 or sign*(tp2-entry)<=0 or (tp1 and sign*(tp1-entry)<=0):
        raise ValueError('Invalid target geometry')
    sub=row.get('sub_strategy','');meta=dict(row.get('metadata') or {})
    mr=engine=='Pragma' and 'MEAN_REVERSION' in sub or engine=='Stigma' and 'OU' in sub
    meta.update(forward_publication_at=available.isoformat(),upstream_causality_verified=False)
    return TradeSignal(row['signal_id'],engine,family,
        StrategyRegime.MEAN_REVERSION if mr else StrategyRegime.TREND_MOMENTUM,
        row['symbol'],row['direction'],origin,None,entry,stop,tp1,tp2,
        sub_strategy=sub,metadata=meta)


def load_forward_intents(home, *, observed_at):
    signals=[];seen=set()
    for engine in SPECS:
        file=Path(home)/engine/'intents.jsonl'
        if not file.exists():continue
        for line in file.read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            row=json.loads(line)
            if row['engine']!=engine:raise ValueError('Forward producer identity mismatch')
            sig=parse_intent(row,observed_at=observed_at)
            if sig.signal_id not in seen:signals.append(sig);seen.add(sig.signal_id)
    return signals
