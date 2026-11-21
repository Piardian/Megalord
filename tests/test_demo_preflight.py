from types import SimpleNamespace
import pytest
from megalord.research.demo_preflight import demo_account_check


@pytest.mark.parametrize("server,mode,login,expected", [
    ("FivePercentOnline-Real", 0, 123, False),
    ("MetaQuotes-Demo", 2, 123, False),
    ("MetaQuotes-Demo", 0, 999, False),
    ("MetaQuotes-Demo", 0, 123, True),
])
def test_demo_mode_alone_does_not_authorize_existing_prop_account(server, mode, login, expected):
    result = demo_account_check(SimpleNamespace(server=server, trade_mode=mode, login=login),
                                expected_login=123, expected_server="MetaQuotes-Demo")
    assert result["eligible_account_profile"] is expected
    assert not result["live_approved"] and result["orders_sent"] == 0


def test_unpinned_or_missing_demo_cannot_pass():
    assert not demo_account_check(None, expected_login=0, expected_server="MetaQuotes-Demo")["eligible_account_profile"]
    assert not demo_account_check(None, expected_login=123, expected_server="MetaQuotes-Demo")["eligible_account_profile"]
