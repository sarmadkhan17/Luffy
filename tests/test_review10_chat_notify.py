"""Offline regressions for finite chat tools and notification acknowledgements."""
import types

import pytest
import requests

from trader.chat.agent import AnalystAgent
from trader.chat.tools import get_pnl
from trader.core.journal import Journal
from trader.notify.telegram import Telegram


@pytest.mark.parametrize('name,args', [
    ('get_pnl', '{}'), ('get_positions', '{}'),
    ('owner_query', '{"kind":"cost","root":"/tmp/arbitrary"}'),
    ('owner_query', '{"kind":"approvals","cfg":{}}'),
    ('owner_query', '[]'), ('owner_query', '{'),
])
def test_runtime_dispatch_rejects_undeclared_tools_and_arguments(tmp_path, monkeypatch, name, args):
    j = Journal(tmp_path / 'journal.db')
    def unexpected(*a, **kw):
        pytest.fail('undeclared input reached evidence authority')
    monkeypatch.setattr('trader.owner.queries.query', unexpected)
    monkeypatch.setattr('trader.chat.tools.get_pnl', unexpected)
    agent = AnalystAgent(j, None)
    assert agent._exec(name, args)['error']
    assert j.kv_get('control_state') is None


def test_runtime_dispatch_passes_exact_query_and_fixed_configuration(tmp_path, monkeypatch):
    j = Journal(tmp_path / 'journal.db')
    cfg = {'brain': {'enabled': False}}
    seen = []
    def query(journal, **kwargs):
        seen.append((journal, kwargs))
        return {'schema': 'owner-query.v1', 'records': []}
    monkeypatch.setattr('trader.owner.queries.query', query)
    agent = AnalystAgent(j, None)
    agent.query_cfg = cfg
    agent._exec('owner_query', '{"kind":"approvals","identity":"exact-id","offset":0}')
    assert seen == [(j, dict(cfg=cfg, kind='approvals', identity='exact-id', offset=0))]


def test_legacy_pnl_does_not_convert_unknown_realized_pnl_to_zero(tmp_path):
    j = Journal(tmp_path / 'journal.db')
    with j._tx() as db:
        for identity, pnl in [('win', 50), ('loss', -20), ('unknown', None), ('flat', 0)]:
            db.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,opened_at,status,realized_pnl) VALUES(?, 'SOL/USDT','long',1,100,'2026-10-20','closed',?)", (identity, pnl))
    out = get_pnl(j, 'all')
    assert out['trades'] == 4 and out['unknown_pnl_trades'] == 1
    assert out['wins'] == 1 and out['losses'] == 1
    assert out['realized_pnl'] is out['win_rate'] is out['profit_factor'] is None


@pytest.mark.parametrize('evidence,pnl,classification', [
    ({'basis': 'reconcile_ghost_unpriced', 'pnl_status': 'UNKNOWN'}, 0, 'UNKNOWN'),
    ({'basis': 'panic_order_unconfirmed', 'pnl_status': 'ESTIMATE'}, 10, 'DERIVED_ESTIMATE'),
    ({'basis': 'exchange_exit_detected_by_size', 'booked_pnl_basis': 'estimated'}, 7, 'DERIVED_ESTIMATE'),
])
def test_legacy_pnl_reuses_retained_booking_classification(tmp_path, evidence, pnl, classification):
    j = Journal(tmp_path / 'journal.db')
    with j._tx() as db:
        db.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,opened_at,status) VALUES('t', 'SOL/USDT','long',1,100,'2026-10-20','open')")
    j.close_trade('t', 110, pnl, 'review10', accounting=evidence)
    out = get_pnl(j, 'all')
    assert out['pnl_value_class'] == classification
    assert out['realized_pnl'] == (None if classification == 'UNKNOWN' else pnl)
    assert out['whole_trade_complete'] is False
    from trader.knowledge.vault import Vault
    vault = Vault.__new__(Vault)
    vault.journal = j
    _, note = vault._pnl_summary(j.query("SELECT * FROM trades WHERE id='t'"))
    assert classification in note


def _telegram(monkeypatch):
    monkeypatch.setattr('trader.notify.telegram.Env.telegram', lambda: ('SENTINEL_TOKEN', '123'))
    return Telegram()


@pytest.mark.parametrize('http_ok,body,expected', [
    (True, {'ok': True, 'result': {'message_id': 1}}, True),
    (True, {'ok': False, 'description': 'rejected'}, False),
    (True, {}, False), (False, {'ok': False}, False),
])
def test_telegram_reports_api_acknowledgement(monkeypatch, http_ok, body, expected):
    tg = _telegram(monkeypatch)
    sent = []
    def post(url, **kw):
        sent.append((url, kw))
        return types.SimpleNamespace(ok=http_ok, json=lambda: body)
    monkeypatch.setattr('trader.notify.telegram.requests.post', post)
    assert tg.send('<b>FROZEN</b>', silent=True) is expected
    assert sent[0][1]['json'] == {'chat_id': '123', 'text': '<b>FROZEN</b>', 'parse_mode': 'HTML', 'disable_notification': True}
    assert sent[0][1]['timeout'] == 10


def test_telegram_failure_log_does_not_disclose_token(monkeypatch, caplog):
    tg = _telegram(monkeypatch)
    def fail(url, **kw):
        raise requests.ConnectionError(f'Unable to reach {url}')
    monkeypatch.setattr('trader.notify.telegram.requests.post', fail)
    assert tg.send('test') is False
    assert 'SENTINEL_TOKEN' not in caplog.text
    assert 'ConnectionError' in caplog.text


def test_unconfigured_telegram_does_not_call_provider(monkeypatch):
    tg = _telegram(monkeypatch)
    tg.token = ''
    monkeypatch.setattr('trader.notify.telegram.requests.post', lambda *a, **kw: pytest.fail('network'))
    assert tg.send('test') is False
