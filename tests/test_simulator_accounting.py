import json

import pandas as pd
import pytest

from megalord.simulator.master_engine import MegalordSimulator
from megalord.types import MathFamily, StrategyRegime, TradeSignal


def simulator(tmp_path, budget=100, cooldown=100):
    config = {
        "allocator": {
            "base_engine_weights": {"Begonya00": 1},
            "begonya00_aplus_multiplier": 1,
            "clone_confluence_cap": 100,
            "consecutive_loss_cooldown_threshold": cooldown,
        },
        "signal_quality_filter": {"enabled": False},
        "firewall": {"max_daily_loss_pct": budget,
                     "currency_cluster_caps": {"EUR": 100, "USD": 100}},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return MegalordSimulator(str(path))


def signal(name, entry, exit, result):
    return TradeSignal(
        signal_id=name, engine_name="Begonya00", math_family=MathFamily.FAMILY_SMC,
        strategy_regime=StrategyRegime.TREND_MOMENTUM, symbol="EURUSD", direction="LONG",
        entry_time=pd.Timestamp(entry, tz="UTC"),
        exit_time=pd.Timestamp(exit, tz="UTC") if exit else None,
        entry_price=1.1, sl_price=1.09, tp2_price=1.12, raw_r_result=result,
    )


def test_default_configuration_is_actual_project_configuration():
    sim = MegalordSimulator()
    assert sim.config_path.parent.name == "config"
    assert sim.config["allocator"]["base_engine_weights"] == sim.arbiter.base_weights


def test_missing_config_fails_instead_of_silently_changing_strategy(tmp_path):
    with pytest.raises(FileNotFoundError):
        MegalordSimulator(str(tmp_path / "absent.json"))


def test_overnight_loss_is_not_erased_before_first_entry(tmp_path):
    sim = simulator(tmp_path, budget=1.5)
    result = sim.run_orchestration([
        signal("overnight", "2026-10-19 20:00", "2026-10-20 08:00", -1),
        signal("next", "2026-10-20 09:00", "2026-10-20 10:00", 1),
    ])
    assert result["executed_trades_count"] == 1
    assert "Realized: 1.00R" in result["rejected_trades"][0]["reason"]


def test_repeated_windows_have_no_inherited_loss_streak(tmp_path):
    sim = simulator(tmp_path, cooldown=1)
    signals = [signal("loss", "2026-10-19 08:00", "2026-10-19 09:00", -1),
               signal("win", "2026-10-19 10:00", "2026-10-19 11:00", 1)]
    assert sim.run_orchestration(signals) == sim.run_orchestration(signals)


def test_equity_drawdown_follows_exit_order(tmp_path):
    result = simulator(tmp_path).run_orchestration([
        signal("loss-a", "2026-10-19 01:00", "2026-10-19 05:00", -1),
        signal("win", "2026-10-19 02:00", "2026-10-19 07:00", 2),
        signal("loss-b", "2026-10-19 03:00", "2026-10-19 06:00", -1),
    ])
    assert result["realized_signal_ids"] == ["loss-a", "loss-b", "win"]
    assert result["max_drawdown_r"] == 2


def test_open_position_cannot_supply_realized_profit(tmp_path):
    result = simulator(tmp_path).run_orchestration([
        signal("open", "2026-10-19 01:00", None, 999),
    ])
    assert result["net_r"] == 0
    assert result["realized_trades_count"] == 0
    assert result["open_trades_count"] == 1
    assert result["live_readiness_proven"] is False


def test_due_close_order_controls_cooldown(tmp_path):
    sim = simulator(tmp_path, cooldown=1)
    result = sim.run_orchestration([
        signal("late-win", "2026-10-19 01:00", "2026-10-19 06:00", 1),
        signal("early-loss", "2026-10-19 02:00", "2026-10-19 05:00", -1),
        signal("candidate", "2026-10-19 07:00", "2026-10-19 08:00", 1),
    ])
    assert result["executed_trades"][-1]["risk_r"] == 1


def test_sharpe_proxy_includes_inactive_days_and_uses_exit_dates(tmp_path):
    result = simulator(tmp_path).run_orchestration([
        signal("first", "2026-10-19 01:00", "2026-10-19 05:00", 1),
        signal("second", "2026-10-21 01:00", "2026-10-22 05:00", -0.5),
    ])
    daily_r = pd.Series([1.0, 0.0, 0.0, -0.5])
    expected = round(daily_r.mean() / daily_r.std() * 365.25 ** 0.5, 2)
    assert result["annualized_sharpe"] == expected
    assert "not account-return Sharpe" in result["sharpe_basis"]
