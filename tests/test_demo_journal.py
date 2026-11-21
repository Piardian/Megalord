import pytest
from megalord.research.demo_journal import reconcile_linked_deals,check_profile


def deals():
    common=dict(position_id=10,volume=1.,swap=0.,fee=0.,time=1792770000)
    return [dict(common,ticket=1,entry=0,price=100.,profit=0.,commission=-2.),
            dict(common,ticket=2,entry=1,price=105.,profit=50.,commission=-2.,swap=-1.)]


def test_entry_and_exit_commissions_are_both_included_without_claiming_complete_net():
    out=reconcile_linked_deals(deals(),[dict(signal_id='x',position_ids=[10])])
    assert out['closed_linked_signals'][0]['broker_deal_cash']==45.
    assert out['closed_linked_signals'][0]['commission']==-4.
    assert out['complete_operating_net_cash'] is None
    assert not out['net_edge_proven']


def test_unlinked_fills_and_account_charges_are_not_assigned_to_megalord():
    out=reconcile_linked_deals(deals(),[])
    assert out['closed_linked_signals']==[]
    assert out['unlinked_deal_count']==2


def test_missing_cash_field_or_open_partial_exit_cannot_be_counted_as_closed_profit():
    rows=deals();del rows[1]['commission']
    out=reconcile_linked_deals(rows,[dict(signal_id='x',position_ids=[10])])
    assert not out['closed_linked_signals'] and out['unresolved']
    out=reconcile_linked_deals(deals()[:1],[dict(signal_id='x',position_ids=[10])])
    assert not out['closed_linked_signals'] and out['unresolved']


def test_duplicate_position_assignment_and_duplicate_deals_are_rejected():
    with pytest.raises(ValueError):
        reconcile_linked_deals(deals(),[dict(signal_id='x',position_ids=[10]),dict(signal_id='y',position_ids=[10])])
    with pytest.raises(ValueError):reconcile_linked_deals(deals()+deals(),[])


def test_existing_prop_terminal_and_unpinned_demo_are_rejected():
    with pytest.raises(ValueError):check_profile(dict(terminal_path='C:/Program Files/Five Percent Online MetaTrader 5/terminal64.exe',expected_login=1,expected_server='MetaQuotes-Demo'))
    with pytest.raises(ValueError):check_profile(dict(terminal_path='C:/Program Files/MetaTrader 5/terminal64.exe',expected_login=0,expected_server='MetaQuotes-Demo'))


def test_user_selected_prop_account_can_be_observed_but_identity_cannot_switch():
    from types import SimpleNamespace
    from megalord.research.account_observer import verify_observed_account
    profile=dict(expected_login=123,expected_server='FivePercentOnline-Real')
    assert verify_observed_account(SimpleNamespace(login=123,server='FivePercentOnline-Real'),profile)
    assert not verify_observed_account(SimpleNamespace(login=456,server='FivePercentOnline-Real'),profile)
    assert not verify_observed_account(None,profile)
