from megalord.research.notification_metrics import scoreboard,format_scoreboard


def entry(sid='a'):return dict(kind='MODELED_ENTRY',signal_id=sid)
def out(leg,reason,r,sid='a'):return dict(kind='MODELED_EXIT_LEG',signal_id=sid,leg=leg,reason=reason,allocated_quote_r=r,time=str(leg))


def test_two_stopped_legs_count_as_one_sl_trade_and_duplicates_do_not_double_pnl():
    events=[entry(),out(1,'STOP',-.2),out(2,'STOP',-.2),out(2,'STOP',-.2)]
    s=scoreboard({},events)
    assert s['realized_quote_r']==-.4 and s['sl_closed_trades']==1 and s['closed_trades']==1


def test_tp1_then_profitable_protection_is_not_a_losing_sl_trade():
    s=scoreboard({},[entry(),out(1,'TP',.2),out(2,'STOP',.01)])
    assert s['tp1_hits']==1 and s['tp2_hits']==0
    assert s['sl_closed_trades']==0 and s['protected_closed_trades']==1


def test_partial_exit_does_not_count_as_closed_trade():
    s=scoreboard({},[entry(),out(1,'TP',.2)])
    assert s['closed_trades']==0 and s['tp1_hits']==1


def test_unknown_fees_or_other_account_profit_never_become_megalord_net():
    s=scoreboard({},[entry(),out(1,'TP',.2),out(2,'TP',.4)],
        dict(closed_linked_signals=[dict(signal_id='other',broker_deal_cash=999)],signal_attribution_independently_verified=True))
    assert s['broker_after_deductions_cash'] is None
    assert 'ölçülmedi' in format_scoreboard(s)


def test_verified_matched_broker_cash_is_displayed_without_subtracting_commission_again():
    broker=dict(signal_attribution_independently_verified=True,account_currency='USD',
        closed_linked_signals=[dict(signal_id='a',broker_deal_cash=45,commission=-4)])
    s=scoreboard({},[entry(),out(1,'TP',.2),out(2,'TP',.4)],broker)
    assert s['broker_after_deductions_cash']==45
    assert '+45.00 USD' in format_scoreboard(s)
    assert s['complete_operating_net_cash'] is None
