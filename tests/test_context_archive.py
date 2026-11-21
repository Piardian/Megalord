import json
import pytest
from megalord.research.context_archive import ContextArchive,context_asof


def test_context_is_recorded_without_trades_but_never_backdated_or_filled_across_gaps(tmp_path):
    p=tmp_path/'context.jsonl';a=ContextArchive(p)
    assert a.observe(observed_at='2026-10-01T11:00Z',macro_gates={'EURUSD':'LONG'},news_config={},news_refreshed_at=None)
    assert not a.observe(observed_at='2026-10-01T11:00:15Z',macro_gates={'EURUSD':'LONG'},news_config={},news_refreshed_at=None)
    rows=[json.loads(r) for r in p.read_text().splitlines()]
    assert context_asof(rows,'2026-10-01T10:59Z') is None
    assert context_asof(rows,'2026-10-01T11:01Z')['payload']['macro_gates']=={'EURUSD':'LONG'}
    assert context_asof(rows,'2026-10-01T11:03Z') is None
    assert not rows[0]['calendar_completeness_verified']
    rows[0]['payload']['macro_gates']['EURUSD']='SHORT'
    with pytest.raises(ValueError,match='fingerprint'):context_asof(rows,'2026-10-01T11:01Z')


def test_changed_context_is_archived_immediately(tmp_path):
    a=ContextArchive(tmp_path/'context.jsonl')
    a.observe(observed_at='2026-10-01T11:00Z',macro_gates={},news_config={},news_refreshed_at=None)
    assert a.observe(observed_at='2026-10-01T11:00:01Z',macro_gates={'EURUSD':'SHORT'},news_config={},news_refreshed_at=None)
    with pytest.raises(ValueError,match='backwards'):
        a.observe(observed_at='2026-10-01T10:59Z',macro_gates={},news_config={},news_refreshed_at=None)
