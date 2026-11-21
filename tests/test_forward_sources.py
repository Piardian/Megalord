from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from megalord.research.forward_sources import ReadOnlyBars
from megalord.research.forward_stream import parse_intent
from megalord.types import StrategyRegime


AT=datetime(2026,10,23,14,30,tzinfo=timezone.utc)


def intent(engine='ChecklistTrigger',sub='SWING_BOS'):
    return dict(schema_version=1,source_kind='NATIVE_FORWARD_INTENT',engine=engine,
        signal_id='test',symbol='EURUSD' if engine!='Stigma' else 'USDJPY',direction='LONG',
        origin_at='2026-10-23T14:29:00Z',available_at='2026-10-23T14:29:01Z',
        entry_price=100,sl_price=99,tp1_price=101,tp2_price=102,sub_strategy=sub)


@pytest.mark.parametrize('engine,sub,mr', [('ChecklistTrigger','SWING_BOS',False),
    ('Pragma','LONG_MEAN_REVERSION',True),('Pragma','TREND_PULLBACK',False),
    ('Stigma','OU_ONLY',True),('Stigma','MOM_ONLY',False)])
def test_native_bridge_keeps_current_root_regime_classification(engine,sub,mr):
    sig=parse_intent(intent(engine,sub),observed_at=AT)
    assert (sig.strategy_regime==StrategyRegime.MEAN_REVERSION)==mr
    assert sig.exit_time is None and sig.raw_r_result==0


@pytest.mark.parametrize('change', [dict(raw_r_result=8),dict(available_at='2026-10-23T14:31:00Z'),
    dict(origin_at='2026-10-23T14:29:30Z'),dict(sl_price=101),dict(tp2_price=98),dict(symbol='CHFJPY')])
def test_corrupt_or_future_native_intents_fail_closed(change):
    row=intent();row.update(change)
    with pytest.raises((ValueError,KeyError)):parse_intent(row,observed_at=AT)


def test_read_only_bar_adapter_excludes_forming_bar_and_has_no_mutator_calls(tmp_path,monkeypatch):
    clock=pd.Timestamp.now(tz='UTC').floor('min')
    rows=[dict(time=int((clock-pd.Timedelta(minutes=n)).timestamp())+10800,
        open=100.,high=101.,low=99.,close=100.,tick_volume=10) for n in range(3,-1,-1)]
    fake=SimpleNamespace(TIMEFRAME_M1=1,copy_rates_from_pos=lambda *a:rows)
    monkeypatch.setattr('megalord.research.broker_clock.observed_offset_seconds',lambda m:10800)
    reader=ReadOnlyBars(fake,tmp_path)
    bars=reader.rates('EURUSD','M1',3)
    assert len(bars)==3
    assert bars.index.max()==clock-pd.Timedelta(minutes=1)
    assert reader.evidence[0]['last_close']==str(clock)


def test_stale_terminal_cache_is_retried_but_never_accepted(tmp_path,monkeypatch):
    old=pd.Timestamp.now(tz='UTC')-pd.Timedelta(days=1)
    rows=[dict(time=int((old+pd.Timedelta(minutes=i)).timestamp()),
        open=100.,high=101.,low=99.,close=100.,tick_volume=10) for i in range(4)]
    calls=[]
    def fetch(*a):calls.append(a);return rows
    fake=SimpleNamespace(TIMEFRAME_M1=1,copy_rates_from_pos=fetch)
    monkeypatch.setattr('megalord.research.broker_clock.observed_offset_seconds',lambda m:0)
    monkeypatch.setattr('megalord.research.forward_sources.time.sleep',lambda n:None)
    with pytest.raises(ValueError,match='stale/forming'):
        ReadOnlyBars(fake,tmp_path).rates('EURUSD','M1',3)
    assert len(calls)==3


def test_frozen_native_copy_is_used_without_hot_swapping_other_checkout_edits(tmp_path):
    import json
    from hashlib import sha256
    from megalord.research.forward_capture import ROOT,verify_sources
    original=tmp_path/'other_checkout.py';original.write_text('changed unrelated working copy')
    frozen=tmp_path/'frozen.py';frozen.write_text('frozen experiment source')
    config=json.loads((ROOT/'config/megalord_config.json').read_text())
    config.pop('telegram',None);config['target_account']=0
    manifest={str(original):dict(sha256=sha256(frozen.read_bytes()).hexdigest(),verify_path=str(frozen)),
        'POLICY':dict(sha256=sha256(json.dumps(config,sort_keys=True).encode()).hexdigest())}
    verify_sources(manifest)
    frozen.write_text('tampered actual runtime source')
    with pytest.raises(ValueError,match='Frozen sources'):verify_sources(manifest)
