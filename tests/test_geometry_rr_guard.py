import pytest
from megalord.live.mt5_executor import MegalordMT5Executor


def test_valid_order_geometry_rejects_collapsed_rr():
    executor = MegalordMT5Executor(target_account=26735345, base_risk_pct=0.50)

    # Simulation of the CHFJPY incident:
    # Entry: 191.674, SL: 191.430 (Risk = 0.244)
    # TP1: 191.680 (Reward = 0.006) -> R:R = 0.024
    is_valid = executor._valid_order_geometry(
        direction="LONG",
        entry=191.674,
        sl=191.430,
        tp=191.680,
        min_rr=0.60
    )
    assert not is_valid, "Executor must reject collapsed R:R geometry (50 cent TP)!"


def test_valid_order_geometry_accepts_healthy_rr():
    executor = MegalordMT5Executor(target_account=26735345, base_risk_pct=0.50)

    # Healthy geometry:
    # Entry: 191.674, SL: 191.430 (Risk = 0.244)
    # TP1: 191.918 (Reward = 0.244) -> 1.0R
    is_valid_tp1 = executor._valid_order_geometry(
        direction="LONG",
        entry=191.674,
        sl=191.430,
        tp=191.918,
        min_rr=0.60
    )
    assert is_valid_tp1, "Executor must accept healthy 1.0R TP1 geometry."

    # TP2 runner: 3.0R
    is_valid_tp2 = executor._valid_order_geometry(
        direction="LONG",
        entry=191.674,
        sl=191.430,
        tp=192.406,
        min_rr=1.20
    )
    assert is_valid_tp2, "Executor must accept healthy 3.0R TP2 geometry."


def test_short_order_geometry_rejects_collapsed_rr():
    executor = MegalordMT5Executor(target_account=26735345, base_risk_pct=0.50)

    # Short: Entry 1.1000, SL 1.1020 (Risk = 20 pips)
    # Collapsed TP: 1.0999 (Reward = 1 pip)
    is_valid = executor._valid_order_geometry(
        direction="SHORT",
        entry=1.1000,
        sl=1.1020,
        tp=1.0999,
        min_rr=0.60
    )
    assert not is_valid, "Short collapsed R:R must be rejected."
