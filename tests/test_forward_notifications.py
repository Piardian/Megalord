from megalord.research.forward_notifications import format_message,notification_key,read_new_lines


def test_shadow_exit_cannot_claim_complete_net_profit():
    text=format_message('event',dict(kind='MODELED_EXIT_LEG',signal_id='x',leg=1,reason='TP',allocated_quote_r=.2))
    assert 'GÖLGE TEST' in text and 'net kâr değildir' in text


def test_repeated_waiting_decisions_do_not_spam_new_messages():
    a=dict(signal_id='x',status='WAITING_LIMIT_PULLBACK',observed_at='one')
    b=dict(a,observed_at='two')
    assert notification_key('decision',a)==notification_key('decision',b)


def test_partial_json_write_is_read_only_after_newline(tmp_path):
    file=tmp_path/'events.jsonl';file.write_text('{"a":1}',encoding='utf-8')
    assert read_new_lines(file,0)==([],0)
    file.write_text('{"a":1}\n',encoding='utf-8')
    rows,offset=read_new_lines(file,0)
    assert rows==[{'a':1}]
    assert read_new_lines(file,offset)==([],offset)


def test_telegram_html_escapes_native_names():
    msg=format_message('signal',dict(engine='<bad>',symbol='X&Y',direction='LONG'))
    assert '<bad>' not in msg and '&lt;bad&gt;' in msg and 'X&amp;Y' in msg
