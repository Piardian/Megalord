import json
from pathlib import Path
import pandas as pd
import pytest
from megalord.research.monthly_diagnostic import recorded_month
from megalord.types import TradeSignal, MathFamily, StrategyRegime


def item(identity, entry, exit, value):
    return TradeSignal(identity, "Trawma", MathFamily.FAMILY_ORDERFLOW, StrategyRegime.TREND_MOMENTUM,
                       "BTCUSD", "LONG", pd.Timestamp(entry), pd.Timestamp(exit) if exit else None,
                       entry_price=100., sl_price=99., raw_r_result=value)


def test_future_closes_are_excluded_and_initial_loss_counts_in_drawdown(tmp_path):
    cfg = json.loads((Path(__file__).resolve().parents[1] / "config/megalord_config.json").read_text())
    cfg["allocator"]["base_engine_weights"]["Trawma"] = 1.
    path = tmp_path / "cfg.json"; path.write_text(json.dumps(cfg))
    sigs = [item("loss", "2026-10-01T10:00Z", "2026-10-01T11:00Z", -1),
            item("future", "2026-10-02T10:00Z", "2026-11-02T11:00Z", 100),
            item("unknown", "2026-10-03T10:00Z", None, 999)]
    result = recorded_month(sigs, config_path=path, start="2026-10-01T00:00Z", end="2026-11-01T00:00Z")
    assert result["allocated_recorded_r"] == -1
    assert result["closed_record_drawdown_r"] == 1
    assert result["accepted_not_closed_by_cutoff"] == 1
    assert result["unknown_exit_ids_excluded"] == ["unknown"]
    assert not result["net_profitability_proven"] and not result["current_exit_policy_replayed"]


def test_duplicate_ids_cannot_inflate_the_recorded_month(tmp_path):
    s = item("same", "2026-10-01T10:00Z", "2026-10-01T11:00Z", 2)
    with pytest.raises(ValueError, match="Duplicate"):
        recorded_month([s, s], config_path="unused", start="2026-10-01T00:00Z", end="2026-11-01T00:00Z")
