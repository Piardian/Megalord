from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from megalord.research.forward_capture import candidate_evidence, tick_evidence
from megalord.types import TradeSignal, MathFamily, StrategyRegime


NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def test_broker_clock_does_not_turn_stale_quote_into_fresh_quote():
    tick = SimpleNamespace(time=int(NOW.timestamp())+10800-180,
                           time_msc=0, bid=100., ask=101.)
    row = tick_evidence(tick, offset=10800, observed_at=NOW)
    assert row['age_seconds'] == 180
    assert not row['fresh']
    assert row['executable_quote_valid']


@pytest.mark.parametrize('bid,ask', [(102., 101.), (0., 1.), (float('nan'), 1.)])
def test_invalid_bid_ask_never_count_as_executable(bid, ask):
    tick = SimpleNamespace(time=NOW.timestamp(), time_msc=0, bid=bid, ask=ask)
    row = tick_evidence(tick, offset=0, observed_at=NOW)
    assert not row['fresh']
    assert not row['executable_quote_valid']


@pytest.mark.parametrize('offset_minutes,prospective', [(-10, False), (1, False), (0, True)])
def test_legacy_and_future_signals_cannot_become_prospective_results(offset_minutes, prospective):
    signal = TradeSignal('id', 'Stigma', MathFamily.FAMILY_STAT_QUANT,
        StrategyRegime.MEAN_REVERSION, 'BTCUSD', 'LONG',
        pd.Timestamp(NOW + timedelta(minutes=offset_minutes)), None,
        entry_price=100., sl_price=99., tp1_price=101., tp2_price=102., raw_r_result=500.)
    row = candidate_evidence(signal, observed_at=NOW, started_at=NOW)
    assert row['prospective_origin'] is prospective
    assert 'raw_r_result' not in row
    assert not row['broker_fill_verified']
