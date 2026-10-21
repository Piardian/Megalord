from types import SimpleNamespace

from megalord.live import mt5_executor


class FakeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self, loss_per_lot=1000.0, account=True, symbol=True):
        self._account = SimpleNamespace(equity=10000.0, balance=10000.0) if account else None
        self._symbol = SimpleNamespace(
            trade_tick_size=0.0001, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        ) if symbol else None
        self.loss_per_lot = loss_per_lot

    def account_info(self):
        return self._account

    def symbol_info(self, symbol):
        return self._symbol

    def order_calc_profit(self, order_type, symbol, volume, entry, stop):
        return -self.loss_per_lot * volume


def test_lot_sizing_rounds_down_to_step(monkeypatch):
    monkeypatch.setattr(mt5_executor, "mt5", FakeMT5(loss_per_lot=1000.0))
    executor = mt5_executor.MegalordMT5Executor(base_risk_pct=0.5)
    assert executor.calculate_lot_size("EURUSD", 1.1000, 1.0990, 1.0) == 0.05


def test_lot_sizing_rejects_when_minimum_lot_exceeds_risk_budget(monkeypatch):
    monkeypatch.setattr(mt5_executor, "mt5", FakeMT5(loss_per_lot=10000.0))
    executor = mt5_executor.MegalordMT5Executor(base_risk_pct=0.5)
    assert executor.calculate_lot_size("EURUSD", 1.1000, 1.0990, 1.0) is None


def test_lot_sizing_fails_closed_without_account(monkeypatch):
    monkeypatch.setattr(mt5_executor, "mt5", FakeMT5(account=False))
    assert mt5_executor.MegalordMT5Executor().calculate_lot_size("EURUSD", 1.1000, 1.0990) is None


def test_lot_sizing_fails_closed_without_symbol_metadata(monkeypatch):
    monkeypatch.setattr(mt5_executor, "mt5", FakeMT5(symbol=False))
    assert mt5_executor.MegalordMT5Executor().calculate_lot_size("EURUSD", 1.1000, 1.0990) is None


def test_lot_sizing_fails_closed_when_broker_profit_calculation_is_missing(monkeypatch):
    fake = FakeMT5()
    fake.order_calc_profit = lambda *args: None
    monkeypatch.setattr(mt5_executor, "mt5", fake)
    assert mt5_executor.MegalordMT5Executor().calculate_lot_size("EURUSD", 1.1000, 1.0990) is None
