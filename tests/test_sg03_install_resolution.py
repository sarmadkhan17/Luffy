"""Offline current-install resolution; no acquisition or new authority.

Factory writers create the versions and installs. A synthetic referee-source
revision exercises distinct frozen specs with the same candidate/strategy ID;
it is fixture data, never permission to revise real research evidence.
"""
import copy
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import Snapshot, Action, Decision
from trader.engine.paper import PaperExecutor
from trader.engine.versioned_exits import LiveExitAdapter
from trader.kernel import Kernel
from trader.strategy import factory_handoff as F
from trader.strategy.compile import compile_version
from trader.strategy.spec import StrategySpec
from tests.authority_factory_fixtures import _journal, _install, _trades, T0, DAY


def dump(journal):
    return '\n'.join(journal._conn().iterdump())


def frozen(journal):
    return {table: journal.query(f'SELECT * FROM {table}') for table in F._TABLES}


def first(tmp_path):
    cfg = load_config()
    j, h = _journal(tmp_path)
    out = F.create_version(j, cfg, {'kind': 'research_candidate', 'hash': h}, at_ms=T0 - 1)
    v = _install(j, out['version_id'], at_ms=T0)
    return j, cfg, h, v


def successor(j, cfg, h, *, installed_at=T0 + DAY):
    # Revise synthetic source values only, never Factory history. Both rendered
    # reconstructions must agree before the real create_version writer accepts.
    from trader.research import vocab
    from trader.research.ledger import Ledger
    from trader.research.runner import ResearchRunner
    from trader.research.combo import evaluated_record
    ledger = Ledger(j)
    ledger.record_gauges('4h', {e: {'p10': -2., 'p25': -1., 'p75': 1., 'p90': 2.,
                                  'n': 9000, 'finite_frac': .99, 'usable': True}
                              for e in vocab.expressions('4h')})
    row = j.query('SELECT * FROM research_combos WHERE hash=?', (h,))[0]
    combo = ResearchRunner(j, cfg, run=lambda *a, **k: None)._combo(row, '4h', 'fixed')
    look = j.query('SELECT * FROM research_tests WHERE hash=?', (h,))[0]
    detail = json.loads(look['detail'])
    detail['evaluated'] = evaluated_record(combo)
    with j._tx() as conn:
        conn.execute('UPDATE research_tests SET detail=? WHERE seq=?', (json.dumps(detail), look['seq']))
    out = F.create_version(j, cfg, {'kind': 'research_candidate', 'hash': h}, at_ms=installed_at - 1)
    return _install(j, out['version_id'], at_ms=installed_at)


@pytest.mark.parametrize('new_clock', [T0 - DAY, T0 + DAY])
def test_successive_installs_use_exact_current_spec_and_preserve_receipts(tmp_path, new_clock):
    j, cfg, h, old = first(tmp_path)
    history = frozen(j)
    new = successor(j, cfg, h, installed_at=new_clock)
    assert old['strategy_id'] == new['strategy_id'] and old['spec_hash'] != new['spec_hash']
    assert old['evidence_ids'] != new['evidence_ids']
    for table, rows in history.items():
        assert j.query(f'SELECT * FROM {table}')[:len(rows)] == rows
    # Journal initialization already backfills this projection timestamp.
    # Prepare its normal initialized shape before auditing read-only calls.
    with j._tx() as c:
        c.execute("UPDATE strategies SET state_changed_at=created_at WHERE state_changed_at=''")
    before = dump(j)
    receipt = F.resolve_current_install(j, new['strategy_id'])
    assert receipt['version_id'] == new['version_id']
    assert F.verify_install(j, new) == receipt
    assert F.verify_install(j, old, current=False)['version_id'] == old['version_id']
    assert compile_version(j, new['version_id']).version_id == new['version_id']
    k = Kernel.__new__(Kernel)
    k.journal = j
    assert k._compile_population_spec(StrategySpec.from_dict(new['spec'])).version_id == new['version_id']
    sp = F.bind_installed_spec(j, StrategySpec.from_dict(new['spec']))
    assert sp.exit._exit_binding['install_id'] == receipt['install_id']
    assert F.live_entry_block(j, new['strategy_id']) == 'version_not_live_authorized:SHADOW'
    assert dump(j) == before
    reopened = Journal(tmp_path / 'j.db')
    assert F.resolve_current_install(reopened, new['strategy_id']) == receipt
    assert F.load_version(reopened, old['version_id']) == old
    assert dump(reopened) == before


def test_same_spec_different_versions_refuse_without_timestamp_or_label_selection(tmp_path):
    j, cfg, h, old = first(tmp_path)
    cfg2 = copy.deepcopy(cfg)
    cfg2['risk']['max_open_positions'] += 1
    out = F.create_version(j, cfg2, {'kind': 'research_candidate', 'hash': h}, at_ms=T0 + 1)
    new = _install(j, out['version_id'], at_ms=T0 + 2)
    assert new['version_id'] != old['version_id'] and new['spec_hash'] == old['spec_hash']
    j._local.governor_write = True
    try:
        j.upsert_spec(StrategySpec.from_dict(new['spec']), state='active')
    finally:
        j._local.governor_write = False
    before = dump(j)
    for call in (lambda: F.resolve_current_install(j, old['strategy_id']),
                 lambda: F.verify_install(j, old), lambda: F.verify_install(j, new),
                 lambda: compile_version(j, old['version_id']),
                 lambda: compile_version(j, new['version_id'])):
        with pytest.raises(F.HandoffRefused, match='install_missing_or_ambiguous'):
            call()
    assert F.live_entry_block(j, old['strategy_id']) == 'version_install_unbound'
    assert F.verify_install(j, old, current=False)['version_id'] == old['version_id']
    assert F.verify_install(j, new, current=False)['version_id'] == new['version_id']
    assert dump(j) == before


@pytest.mark.parametrize('gap', ['no_projection', 'unmatched_projection', 'no_receipt',
                               'no_validation', 'stale_validation', 'corrupt_receipt',
                               'wrong_index', 'wrong_event', 'changed_during_read'])
def test_missing_stale_conflicting_or_changed_authority_refuses_read_only(tmp_path, monkeypatch, gap):
    j, cfg, h, v = first(tmp_path)
    if gap == 'no_projection':
        F.retire_version(j, v['version_id'], F.RETIRED, reason_code='fixture', actor='factory', at_ms=T0 + 1)
    elif gap == 'unmatched_projection':
        sp = StrategySpec.from_dict(v['spec'])
        sp.name += ' unmatched'
        j.upsert_spec(sp, state='paper')
    elif gap == 'no_receipt':
        with j._tx() as c:
            c.execute('DROP TRIGGER strategy_version_installs_no_delete')
            c.execute('DELETE FROM strategy_version_installs')
    elif gap == 'no_validation':
        with j._tx() as c:
            c.execute('DROP TRIGGER strategy_validation_receipts_no_delete')
            c.execute('DELETE FROM strategy_validation_receipts')
    elif gap == 'stale_validation':
        with j._tx() as c:
            c.execute("UPDATE research_candidates SET state='gate1_fail' WHERE hash=?", (h,))
    elif gap in ('corrupt_receipt', 'wrong_index'):
        with j._tx() as c:
            c.execute('DROP TRIGGER strategy_version_installs_no_update')
            column, value = ('canonical_json', '{}') if gap == 'corrupt_receipt' else ('install_id', 'wrong')
            c.execute(f'UPDATE strategy_version_installs SET {column}=?', (value,))
    elif gap == 'wrong_event':
        with j._tx() as c:
            c.execute('DROP TRIGGER strategy_version_events_no_update')
            c.execute("UPDATE strategy_version_events SET ref_id='wrong' WHERE to_state='SHADOW'")
    else:
        real = F._installed
        count = 0
        def changing(*a):
            nonlocal count
            count += 1
            return real(*a) if count == 1 else None
        monkeypatch.setattr(F, '_installed', changing)
    before = dump(j)
    with pytest.raises(F.HandoffRefused):
        F.resolve_current_install(j, v['strategy_id'])
    assert dump(j) == before


def test_reprojected_old_spec_does_not_restore_stale_validation(tmp_path):
    j, cfg, h, old = first(tmp_path)
    new = successor(j, cfg, h)
    # Re-projecting the old spec does not make its withdrawn validation current.
    j.upsert_spec(StrategySpec.from_dict(old['spec']), state='paper')
    before = dump(j)
    with pytest.raises(F.HandoffRefused, match='validation_evidence_changed'):
        F.resolve_current_install(j, old['strategy_id'])
    assert dump(j) == before


def test_uninstalled_proposal_keeps_refusal_diagnostic_without_install_authority(tmp_path):
    j, cfg, h, old = first(tmp_path)
    sp = StrategySpec.from_dict(old['spec'])
    sp.exit.stop = {'kind': 'atr', 'mult': 4.}
    out = F.derive_version(j, old['version_id'], sp, at_ms=T0 + 1)
    proposed = F.load_version(j, out['version_id'])
    j.upsert_spec(StrategySpec.from_dict(proposed['spec']), state='paper')
    before = dump(j)
    with pytest.raises(F.HandoffRefused, match='install_missing_or_ambiguous'):
        F.resolve_current_install(j, proposed['strategy_id'])
    assert F.live_entry_block(j, proposed['strategy_id']) == 'version_not_live_authorized:PROPOSED'
    assert F.state_of(j, proposed['version_id']) == F.PROPOSED
    assert F.verify_install(j, old, current=False)['version_id'] == old['version_id']
    assert dump(j) == before


def test_install_added_during_resolution_refuses_instead_of_choosing_old_receipt(tmp_path, monkeypatch):
    j, cfg, h, old = first(tmp_path)
    cfg2 = copy.deepcopy(cfg)
    cfg2['risk']['max_open_positions'] += 1
    real = F.verify_validation
    captured = []
    def changed(journal, version):
        validation = real(journal, version)
        with monkeypatch.context() as context:
            context.setattr(F, 'verify_validation', real)
            out = F.create_version(journal, cfg2, {'kind': 'research_candidate', 'hash': h}, at_ms=T0 + 1)
            _install(journal, out['version_id'], at_ms=T0 + 2)
        captured.append(dump(journal))
        return validation
    monkeypatch.setattr(F, 'verify_validation', changed)
    with pytest.raises(F.HandoffRefused, match='installed_version_changed'):
        F.resolve_current_install(j, old['strategy_id'])
    assert dump(j) == captured[0]


def test_paper_reader_reaches_current_version_without_changing_any_state(tmp_path, monkeypatch):
    j, cfg, h, old = first(tmp_path)
    new = successor(j, cfg, h)
    paper = PaperExecutor(j, cfg)
    real = F.state_of
    seen = []
    def state(journal, vid):
        seen.append(vid)
        return real(journal, vid)
    monkeypatch.setattr(F, 'state_of', state)
    snap = Snapshot('BTC/USDT', '2026-10-14T00:00:00+00:00', 100., {}, market_type='futures')
    decision = Decision('d', 'c', 'BTC/USDT', Action.BUY, 1., .5, .8, [], [])
    before = dump(j)
    with pytest.raises(ValueError, match='paper_decision_identity_unverified'):
        paper.enter(decision, snap, new['strategy_id'], reference_equity=10000., state='ACTIVE')
    assert seen == [new['version_id']]
    assert dump(j) == before


def test_historical_exit_and_signal_replay_keep_original_binding_after_replacement(tmp_path):
    from trader.strategy import exit_policy as E
    from trader.learning.capture_runtime import _signal_version
    j, cfg, h, old = first(tmp_path)
    _trades(j, old['strategy_id'], [10.], T0, spec_hash=old['spec_hash'])
    trade = dict(j.query('SELECT * FROM trades')[0])
    identity = json.loads(trade['entry_identity_json'])
    original = _signal_version(j._conn(), old['strategy_id'], old['spec_hash'], T0 + DAY)
    history = frozen(j)
    successor(j, cfg, h)
    assert _signal_version(j._conn(), old['strategy_id'], old['spec_hash'], T0 + DAY) == original
    for table, rows in history.items():
        assert j.query(f'SELECT * FROM {table}')[:len(rows)] == rows
    policy, state = E.decode(identity['exit_state'])
    ts = state.last_bar_ms + policy.step_ms
    price = policy.target + 1
    frame = pd.DataFrame([dict(ts=pd.Timestamp(ts, unit='ms', tz='UTC'),
                               open=100., high=price, low=100., close=price, volume=10.)])
    snap = Snapshot(trade['symbol'], pd.Timestamp(ts + policy.step_ms, unit='ms', tz='UTC').isoformat(),
                    price, {old['spec']['timeframe']: frame}, market_type='futures')
    sent = []
    adapter = LiveExitAdapter(j, SimpleNamespace(close=lambda t, **kw: sent.append(dict(t)) or True))
    receipts = frozen(j)
    assert adapter.manage(trade, snap) is not None
    assert sent[0]['_strategy_exit_evidence']['version_id'] == old['version_id']
    assert sent[0]['_strategy_exit_evidence']['install_id'] == identity['install_id']
    assert frozen(j) == receipts
    assert adapter.manage(trade, snap) is not None
    assert sent[1]['_strategy_exit_evidence'] == sent[0]['_strategy_exit_evidence']
    reopened = Journal(tmp_path / 'j.db')
    assert _signal_version(reopened._conn(), old['strategy_id'], old['spec_hash'], T0 + DAY) == original
    assert F.verify_install(reopened, old, current=False)['install_id'] == identity['install_id']


def test_current_readers_share_the_resolver_and_historical_readers_do_not():
    import inspect
    from trader.learning import capture_runtime
    for reader in (Kernel._compile_population_spec, Kernel._mechanism_once, Kernel._try_enter,
                   PaperExecutor.enter, F.bind_installed_spec, F.live_entry_block, F.verify_install):
        source = inspect.getsource(reader)
        assert 'resolve_current_install' in source
        assert 'SELECT version_id FROM strategy_version_installs WHERE strategy_id' not in source
    for reader in (LiveExitAdapter.manage, PaperExecutor.manage, capture_runtime._signal_version):
        assert 'resolve_current_install' not in inspect.getsource(reader)


@pytest.mark.parametrize('extra', [
    '\nf.create_version(journal, cfg, {})',
    '\nf.govern_version(journal, cfg, "v", "ACTIVE")',
    '\ngetattr(f, "govern_version")',
    '\nfrom trader.strategy.factory_handoff import govern_version',
])
def test_dashboard_allowlist_still_detects_writers_and_module_escape(monkeypatch, extra):
    from pathlib import Path
    from tests.test_strategy_factory_handoff import test_factory_writes_no_trading_state_and_has_no_trading_caller
    path = Path('trader/dashboard/glacier_truth.py')
    original = Path.read_text
    source = original(path)
    # Mutate only the reader's in-memory input; never the dashboard file.
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **kw:
                        source + extra if self == path else original(self, *a, **kw))
    with pytest.raises(AssertionError):
        test_factory_writes_no_trading_state_and_has_no_trading_caller()
