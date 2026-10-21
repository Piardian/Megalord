"""Unit tests for Megalord 24/7 Daemon and Live Command Center."""
import json
import pytest
from pathlib import Path
import pandas as pd
import tempfile
import shutil

from megalord.megalord_daemon import MegalordDaemon, MegalordTelegramNotifier
from megalord.types import TradeSignal, MathFamily, StrategyRegime


@pytest.fixture
def local_tmp_dir():
    d = Path(tempfile.mkdtemp(prefix="megalord_test_"))
    yield d
    try:
        shutil.rmtree(d)
    except Exception:
        pass


def test_daemon_initialization_and_dashboard(local_tmp_dir):
    cfg_file = local_tmp_dir / "megalord_config.json"
    cfg_data = {
        "system_name": "Megalord Test",
        "target_account": 26735345,
        "firewall": {
            "max_daily_loss_pct": 2.5,
            "currency_cluster_caps": {"USD": 1.5}
        },
        "allocator": {
            "base_engine_weights": {"Begonya00": 1.0, "Convexity_v2": 0.0},
            "engine_execution_enabled": {"Begonya00": True, "Convexity_v2": False},
            "compass_only_engines": ["Convexity_v2"],
        }
    }
    cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")
    state_file = local_tmp_dir / "megalord_live_state.json"

    daemon = MegalordDaemon(config_path=cfg_file, state_path=state_file, mode="shadow")
    assert daemon.state["account_id"] == 26735345
    assert daemon.mode == "shadow"
    # Verify dashboard prints without exception
    daemon.print_dashboard()


def test_daemon_single_cycle_execution(local_tmp_dir):
    cfg_file = local_tmp_dir / "megalord_config.json"
    cfg_data = {
        "system_name": "Megalord Test",
        "target_account": 26735345,
        "firewall": {
            "max_daily_loss_pct": 2.5,
            "currency_cluster_caps": {"USD": 1.5, "EUR": 1.5}
        },
        "allocator": {
            "base_engine_weights": {"Begonya00": 1.0, "Pragma": 0.90, "Convexity_v2": 0.0},
            "engine_execution_enabled": {"Begonya00": True, "Pragma": True, "Convexity_v2": False},
            "compass_only_engines": ["Convexity_v2"],
            "orthogonal_confluence_multiplier": 1.30,
        }
    }
    cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")
    state_file = local_tmp_dir / "megalord_live_state.json"

    daemon = MegalordDaemon(config_path=cfg_file, state_path=state_file, mode="shadow")

    # Mock signals
    sig1 = TradeSignal(
        signal_id="MOCK_01",
        engine_name="Begonya00",
        math_family=MathFamily.FAMILY_SMC,
        strategy_regime=StrategyRegime.TREND_MOMENTUM,
        symbol="EURUSD",
        direction="LONG",
        entry_time=pd.Timestamp.now(tz="UTC"),
        exit_time=None,
        entry_price=1.1000,
        sl_price=1.0950,
        tp2_price=1.1100,
    )
    sig_compass = TradeSignal(
        signal_id="MOCK_02",
        engine_name="Convexity_v2",
        math_family=MathFamily.FAMILY_MACRO_TREND,
        strategy_regime=StrategyRegime.TREND_MOMENTUM,
        symbol="EURUSD",
        direction="LONG",
        entry_time=pd.Timestamp("2026-03-01 10:05:00", tz="UTC"),
        exit_time=None,
    )

    daemon.poll_new_incoming_signals = lambda: [sig1, sig_compass]
    daemon.fetch_htf_macro_gates = lambda: {"EURUSD": "LONG"}

    res = daemon.execute_cycle()
    assert res["processed"] == 2
    # Begonya00 should be approved, Convexity_v2 blocked (compass only)
    assert res["approved"] == 1
    assert res["blocked"] == 1
    assert len(daemon.state["open_positions"]) == 1
    assert daemon.state["open_positions"][0]["engine_name"] == "Begonya00"
    assert state_file.exists()


def test_telegram_notifier_mock():
    notifier = MegalordTelegramNotifier(bot_token="dummy", chat_id="dummy")
    assert notifier.is_configured() is True
    # Without valid token sending should gracefully return False without raising
    sent = notifier.send_alert("<b>Test</b>")
    assert sent is False
