from types import SimpleNamespace

from megalord.live import mt5_executor


class FakeExecutionMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 0
    TRADE_RETCODE_DONE = 10009

    def __init__(self, position_sl=0.0, reject_check=False, reject_repair=False):
        self.position = SimpleNamespace(
            ticket=123, symbol="EURUSD", magic=202699, type=self.POSITION_TYPE_BUY,
            volume=0.05, sl=position_sl, tp=1.1200,
        )
        self.reject_check = reject_check
        self.reject_repair = reject_repair
        self.sent = []
        self.checked = []

    def symbol_info(self, symbol):
        return SimpleNamespace(point=0.00001, volume_step=0.01)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=1.1000, ask=1.1001)

    def positions_get(self, ticket=None, symbol=None):
        if ticket is not None:
            return [self.position] if self.position.ticket == ticket else None
        if symbol is not None and self.position.symbol == symbol:
            return [self.position]
        return None

    def order_check(self, request):
        self.checked.append(dict(request))
        return SimpleNamespace(retcode=10016 if self.reject_check else 0, comment="check rejected")

    def order_send(self, request):
        self.sent.append(dict(request))
        if request.get("action") == self.TRADE_ACTION_SLTP:
            if self.reject_repair:
                return SimpleNamespace(retcode=10016, comment="SL repair rejected")
            self.position.sl = request["sl"]
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=123, deal=456, price=request.get("price", 1.1001), comment="done")

    def last_error(self):
        return "fake error"


def test_order_is_not_sent_when_broker_precheck_rejects(monkeypatch):
    fake = FakeExecutionMT5(reject_check=True)
    monkeypatch.setattr(mt5_executor, "mt5", fake)
    executor = mt5_executor.MegalordMT5Executor()

    result = executor._send_single_deal("EURUSD", fake.ORDER_TYPE_BUY, 1.1001, 0.05, 1.0990, 1.1200, "test")

    assert result is None
    assert len(fake.checked) == 2
    assert fake.sent == []


def test_missing_broker_side_sl_is_restored_and_reverified(monkeypatch):
    fake = FakeExecutionMT5(position_sl=0.0)
    monkeypatch.setattr(mt5_executor, "mt5", fake)
    monkeypatch.setattr(mt5_executor.time, "sleep", lambda *_: None)
    executor = mt5_executor.MegalordMT5Executor()

    assert executor._verify_or_restore_stop_loss(123, "EURUSD", 0.05, fake.ORDER_TYPE_BUY, 1.0990)
    assert fake.position.sl == 1.0990
    assert any(call.get("action") == fake.TRADE_ACTION_SLTP for call in fake.sent)


def test_already_correct_broker_side_sl_is_accepted(monkeypatch):
    fake = FakeExecutionMT5(position_sl=1.0990)
    monkeypatch.setattr(mt5_executor, "mt5", fake)
    executor = mt5_executor.MegalordMT5Executor()

    assert executor._verify_or_restore_stop_loss(123, "EURUSD", 0.05, fake.ORDER_TYPE_BUY, 1.0990)
    assert fake.sent == []

# These tests exercise broker-side stop-loss verification and fail-closed prechecks.

# Regression guard: a filled order is accepted only after the broker-side SL is confirmed.
