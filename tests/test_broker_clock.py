from types import SimpleNamespace
import pandas as pd
import pytest
from megalord.research.broker_clock import observed_offset_seconds,normalize_reported_frame


def test_audited_future_epoch_is_shifted_only_with_a_fresh_heartbeat():
    now=pd.Timestamp('2026-10-23T10:00Z')
    api=SimpleNamespace(account_info=lambda:SimpleNamespace(server='FivePercentOnline-Real'),
                        symbol_info_tick=lambda sym:SimpleNamespace(time=now.timestamp()+10800-2))
    assert observed_offset_seconds(api,now=now)==10800
    frame=pd.DataFrame({'close':[1.]},index=pd.DatetimeIndex(['2026-10-23T13:00Z']))
    aligned=normalize_reported_frame(frame,10800)
    assert aligned.index[0]==now and frame.index[0]!=now
    with pytest.raises(ValueError,match='already'):normalize_reported_frame(aligned,10800)
    api.symbol_info_tick=lambda sym:SimpleNamespace(time=now.timestamp()+7200)
    with pytest.raises(ValueError,match='not confirmed'):observed_offset_seconds(api,now=now)


def test_nominal_utc_broker_is_not_shifted():
    api=SimpleNamespace(account_info=lambda:SimpleNamespace(server='Other-UTC'))
    assert observed_offset_seconds(api,now='2026-10-23T10:00Z')==0
