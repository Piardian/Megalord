import pandas as pd

from megalord.arbiter.signal_arbiter import MegalordSignalArbiter
from megalord.types import MathFamily, StrategyRegime, TradeSignal


def test_arbiter_vetoes_new_signal_without_stop():
    config = {
        "allocator": {
            "base_engine_weights": {"Begonya00": 0.5},
            "engine_execution_enabled": {"Begonya00": True},
        },
        "signal_quality_filter": {"enabled": True},
    }
    signal = TradeSignal(
        signal_id="missing-sl", engine_name="Begonya00", math_family=MathFamily.FAMILY_SMC,
        strategy_regime=StrategyRegime.TREND_MOMENTUM, symbol="EURUSD", direction="LONG",
        entry_time=pd.Timestamp.now(tz="UTC"), exit_time=None, entry_price=1.1,
        sl_price=0.0, tp2_price=1.103,
    )
    decision = MegalordSignalArbiter(config).evaluate_signal(signal, [])
    assert decision.allowed is False
    assert decision.action == "QUALITY_MISSING_SL"
