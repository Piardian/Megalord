import json
import pandas as pd

from megalord.adapters.stream_loader import ProjectStreamLoader
from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.filters.signal_quality_filter import SignalQualityFilter


def loader(tmp_path):
    l = ProjectStreamLoader()
    l.stigma_live_path = tmp_path / 'live_positions.json'
    l.stigma_cache_path = tmp_path / 'absent.json'
    l.trawma_paper_path = tmp_path / 'paper.json'
    l.trawma_cache_path = tmp_path / 'absent.json'
    l.checklist_live_path = tmp_path / 'clt.jsonl'
    l.checklist_path = tmp_path / 'absent.json'
    l.pragma_live_path = tmp_path / 'pragma.json'
    l.pragma_cache_path = tmp_path / 'absent.json'
    return l


def test_stigma_native_entry_alias_and_initial_stop_survive_breakeven(tmp_path):
    l = loader(tmp_path)
    row = dict(symbol='BTCUSDT', strategy_mode='OU_ONLY', entry=100.,
               stop_loss=100., initial_stop_loss=99., tp1=101., tp2=102.,
               entry_time='2026-10-01T11:00Z', status='OPEN')
    l.stigma_live_path.write_text(json.dumps({'BTCUSDT:OU_ONLY': row}))
    s, = l.load_stigma_whitelist()
    assert s.entry_price == 100 and s.sl_price == 99
    assert s.metadata['initial_stop_preserved']


def test_h1_pending_intent_arrives_at_signal_close_without_future_fill(tmp_path):
    l = loader(tmp_path)
    pending = dict(symbol='BTCUSDT', strategy_mode='OU_ONLY', timeframe='H1', entry=100.,
                   stop_loss=99., tp1=101., tp2=102., signal_time='2026-10-01T10:00Z',
                   status='PENDING_NEXT_OPEN')
    l.stigma_live_path.with_suffix('.pending.json').write_text(json.dumps({'BTCUSDT:OU_ONLY': pending}))
    s, = l.load_stigma_whitelist()
    assert s.exit_time is None and s.raw_r_result == 0
    assert s.entry_time == pd.Timestamp('2026-10-01T11:00Z')
    from megalord.research.decision_evidence import capture_entry_context
    snapshot=capture_entry_context({},s,decided_at='2026-10-01T11:00:15Z',active_positions=[],
        daily_realized_loss_r=0.,loss_streaks={},macro_bias='NATURAL_BAND')
    assert 'hurst' not in snapshot['signal']['metadata']
    # This passes the actual entry freshness/geometry gate, without a historical bypass.
    d = MegalordSignalArbiter().evaluate_signal(s, [], macro_htf_bias='NATURAL_BAND',
                                              now_utc='2026-10-01T11:00:15Z')
    assert d.allowed
    filled = dict(pending, status='OPEN', entry_time='2026-10-01T11:00Z', initial_stop_loss=99.)
    l.stigma_live_path.write_text(json.dumps({'BTCUSDT:OU_ONLY': filled}))
    assert len(l.load_stigma_whitelist()) == 1
    assert l.load_stigma_whitelist()[0].signal_id == s.signal_id


def test_trawma_original_stop_is_not_overwritten_by_paper_breakeven(tmp_path):
    l = loader(tmp_path)
    l.trawma_paper_path.write_text(json.dumps([dict(id='p',symbol='BTCUSDT',side='LONG',
        entry_price=100.,stop_loss=100.,initial_stop=99.,entry_time='2026-10-01T11:00Z',status='OPEN')]))
    s, = l.load_trawma_whitelist()
    assert s.entry_price == 100 and s.sl_price == 99


def test_checklist_native_intent_is_deduplicated_without_future_labels(tmp_path):
    l=loader(tmp_path)
    r=dict(schema_version=1,source_kind='NATIVE_ENTRY_INTENT',signal_id='CLT_NATIVE_x',
        symbol='EURUSD',direction='LONG',entry_time='2026-10-01T11:00Z',available_at='2026-10-01T11:00Z',
        entry_price=1.1,sl_price=1.09,tp2_price=1.12,status='PENDING_LIMIT',grade='A+')
    l.checklist_live_path.write_text(json.dumps(r)+'\n'+json.dumps(r)+'\n{incomplete append')
    s,=l.load_checklist_trigger()
    assert s.entry_price==1.1 and s.sl_price==1.09 and s.exit_time is None
    assert s.raw_r_result==0 and s.metadata['status']=='PENDING_LIMIT'
    newer=dict(r,entry_time='2026-10-01T12:00Z',available_at='2026-10-01T12:00Z')
    l.checklist_live_path.write_text(json.dumps(r)+'\n'+json.dumps(newer)+'\n')
    assert l.load_checklist_trigger()[0].entry_time==pd.Timestamp('2026-10-01T11:00Z')
    r['exit_time']='2026-10-01T12:00Z'
    l.checklist_live_path.write_text(json.dumps(r)+'\n')
    assert l.load_checklist_trigger()==[]


def test_exact_one_r_float_roundoff_does_not_lower_the_quality_threshold():
    assert not SignalQualityFilter._below_rr(.99999999999994,1.)
    assert SignalQualityFilter._below_rr(.999999999,1.)
    assert SignalQualityFilter._below_rr(.999,1.)


def test_pragma_balance_poi_keeps_mean_reversion_identity_and_true_close_time(tmp_path):
    from megalord.types import StrategyRegime
    l=loader(tmp_path)
    l.pragma_live_path.write_text(json.dumps([dict(signal_id='PRG_fixture',symbol='EURUSD',
        direction='LONG',poi_type='LONG_MEAN_REVERSION',regime='BALANCE',
        bar_time='2026-10-01T10:04Z',created_at='2026-10-01T10:05:15Z',
        entry_price=1.1,stop_loss=1.09,tp1=1.11,tp2=1.12)]))
    s,=l.load_pragma()
    assert s.strategy_regime==StrategyRegime.MEAN_REVERSION
    assert s.entry_time==pd.Timestamp('2026-10-01T10:05Z')
    decision=MegalordSignalArbiter().evaluate_signal(s,[],macro_htf_bias='LONG_ONLY',now_utc='2026-10-01T10:05:15Z')
    assert not decision.allowed and decision.action=='NATURAL_BAND_REQUIRED'
