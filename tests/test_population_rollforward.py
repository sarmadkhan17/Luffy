"""Bounded prospective rollforward: a later frozen window in the same local ledger.

Synthetic tmp ledgers only; no network, live DB or trading.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from trader.cognition import dataset as D
from trader.observability import population as P, learning as L
from tests.test_attention_learning import publish
from tests.test_pit_population import config, NOW

DAY = 86400000
SYMBOLS = ['S%d/USDT' % i for i in range(6)]


def window(tmp_path, name, start, cut, frozen, export=None, prior=None):
    from tests.test_pit_dataset import declaration as make_declaration
    declaration = make_declaration(SYMBOLS, cut, start=start, mode='forward')
    freeze = D.freeze(declaration, frozen, code_manifest={'fixture': 'synthetic'})
    a, b = tmp_path/(name+'-declaration.json'), tmp_path/(name+'-freeze.json')
    a.write_text(json.dumps(declaration)); b.write_text(json.dumps(freeze))
    cfg = {'declaration': str(a), 'receipt': str(b),
           'export_directory': str(tmp_path/(export or 'exports-'+name))}
    if prior:
        cfg['prior_declarations'] = [c['declaration'] for c in prior]
    return cfg


def second(tmp_path, first, **over):
    args = dict(start=NOW+DAY, cut=NOW+2*DAY, frozen=NOW+DAY-1000, prior=[first])
    args.update(over)
    return window(tmp_path, 'b', **args)


def files(cfg):
    return {p: p.read_bytes() for p in Path(cfg['export_directory']).glob('*/*.json')}


def rows(db, prefix=''):
    return (db.execute('SELECT * FROM population_events WHERE id LIKE ? ORDER BY rowid', (prefix+'%',)).fetchall(),
            db.execute("SELECT * FROM population_meta WHERE key IN ('binding','activation','last_invocation') "
                       'ORDER BY key').fetchall())


def opened(db, cfg, now):
    with db:
        pop = P.Producer(db, cfg, 'forecast', now)
    P.flush(db, cfg, 'forecast')
    return pop


def version(cfg):
    return D.declaration_version(json.loads(Path(cfg['declaration']).read_text()))


def test_two_window_rotation_preserves_old_capture_bytes(tmp_path):
    a = config(tmp_path)
    b = second(tmp_path, a)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW); first = L.step(src, dst, NOW, a)
    end = first['recent'][0]['deadline_ms']
    publish(src, end+1, 'resolve', 120); L.step(src, dst, end+1, a)
    a_local = dict(a, local_ledgers={'forecast': str(dst)})
    old_capture = P.capture(a_local, NOW+DAY+10)
    old_files = files(a)
    with sqlite3.connect(dst) as db:
        old_rows = rows(db)
    # Rotation: a new frozen window starting at the old cut, in the same ledger.
    publish(src, NOW+DAY+20, 's2'); L.step(src, dst, NOW+DAY+20, b)
    new_files = files(b)
    new_events = [json.loads(raw) for raw in new_files.values()]
    assert {e['declaration_version'] for e in new_events} == {version(b)}
    assert [e['source']['previous_declaration_version'] for e in new_events
            if e['kind'] == 'activation'] == [version(a)]
    with sqlite3.connect(dst) as db:
        # Old immutable index rows, receipt clocks and the v1 binding are untouched.
        assert rows(db, version(a)+':') == old_rows
        assert db.execute("SELECT payload FROM population_meta WHERE key='active'").fetchone()[0] == version(b)
    assert files(a) == old_files
    # Old window stays independently capturable, byte-identical, via its archived config.
    assert P.capture(a_local, NOW+DAY+10) == old_capture
    # New capture selects only its own receipts and binding.
    b_local = dict(b, local_ledgers={'forecast': str(dst)})
    new_capture = P.capture(b_local, NOW+DAY+30)
    assert new_capture['local_indices']['forecast']
    assert all(r['id'].startswith(version(b)+':forecast:') for r in new_capture['local_indices']['forecast'])
    assert new_capture['cohort']['receipt_reconciliation']['forecast']['status'] == 'matched'
    assert len(new_capture['cohort']['rows']) == 6
    src.unlink(); dst.unlink()
    assert P.replay_capture(old_capture) == old_capture['coverage']
    assert P.replay_capture(new_capture) == new_capture['coverage']


def test_old_pending_episodes_are_not_backfilled_or_given_future_terminals(tmp_path):
    a = config(tmp_path)
    b = second(tmp_path, a)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW); L.step(src, dst, NOW, a)
    old_files = files(a)
    late = NOW+DAY+L.PROTOCOL['grace_ms']+1
    L.step(src, dst, late, b)
    # Old episodes resolve in the underlying ledger but neither window records them.
    with sqlite3.connect(dst) as db:
        assert db.execute("SELECT COUNT(*) FROM episodes WHERE status!='pending'").fetchone()[0] == 6
    assert files(a) == old_files
    kinds = [json.loads(raw)['kind'] for raw in files(b).values()]
    assert kinds == ['activation']


def test_old_pending_receipts_refuse_rotation_until_exported_by_old_config(tmp_path):
    a = config(tmp_path)
    b = second(tmp_path, a)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    with sqlite3.connect(dst) as db:
        P.Producer(db, a, 'forecast', NOW).emit('gap:x', 'gap', {'reason': 'test'})
        before = rows(db)
    publish(src, NOW+DAY+20, 's2')
    with pytest.raises(ValueError, match='prior_window_pending'):
        L.step(src, dst, NOW+DAY+20, b)
    with sqlite3.connect(dst) as db:
        assert rows(db) == before
        assert not db.execute("SELECT 1 FROM population_meta WHERE key LIKE '%:%' OR key='active'").fetchone()
        with pytest.raises(ValueError, match='prior_window_pending'):
            P.Producer(db, b, 'forecast', NOW+DAY+20)
        with pytest.raises(ValueError, match='prior_window_pending'):
            P.flush(db, b, 'forecast')
    assert not files(b)
    with sqlite3.connect(dst) as db:
        P.flush(db, a, 'forecast')
    assert len(files(a)) == 2
    L.step(src, dst, NOW+DAY+20, b)
    assert all(json.loads(raw)['declaration_version'] == version(b) for raw in files(b).values())


def test_no_cross_window_export(tmp_path):
    a = config(tmp_path)
    b = second(tmp_path, a)
    db = sqlite3.connect(tmp_path/'outbox.db')
    with db:
        P.Producer(db, a, 'forecast', NOW).emit('one', 'gap', {'a': 1})
    P.flush(db, a, 'forecast')
    old_files = files(a)
    with db:
        P.Producer(db, b, 'forecast', NOW+DAY).emit('two', 'gap', {'b': 2})
    # The closed window drains only its own prefix; new pending stays put.
    P.flush(db, a, 'forecast')
    assert files(a) == old_files
    assert db.execute('SELECT COUNT(*) FROM population_events WHERE payload IS NOT NULL').fetchone()[0] == 2
    P.flush(db, b, 'forecast')
    assert {json.loads(raw)['declaration_version'] for raw in files(b).values()} == {version(b)}
    assert files(a) == old_files
    # A receipt copied across windows is refused by capture.
    stray = next(iter(files(b)))
    target = Path(a['export_directory'])/'forecast'/stray.name
    target.write_bytes(stray.read_bytes())
    with pytest.raises(ValueError, match='receipt_mismatch'):
        P.capture(a, NOW+2*DAY)
    target.unlink()
    assert P.capture(a, NOW+2*DAY)['coverage']['registrations'] == 0


def test_directory_conflicts_and_rebind(tmp_path):
    a = config(tmp_path)
    db = sqlite3.connect(tmp_path/'p.db')
    opened(db, a, NOW)
    before = rows(db)
    same_dir = second(tmp_path, a, export='exports')
    with pytest.raises(ValueError, match='directory_conflict'):
        P.Producer(db, same_dir, 'forecast', NOW+DAY)
    b = second(tmp_path, a)
    (Path(b['export_directory'])/'forecast').mkdir(parents=True)
    (Path(b['export_directory'])/'forecast'/'x.json').write_text('{}')
    with pytest.raises(ValueError, match='directory_conflict'):
        P.Producer(db, b, 'forecast', NOW+DAY)
    (Path(b['export_directory'])/'forecast'/'x.json').unlink()
    assert rows(db) == before
    opened(db, b, NOW+DAY)
    # Same-version directory rebind and old-window directory moves are refused.
    moved = dict(b, export_directory=str(tmp_path/'elsewhere'))
    with pytest.raises(ValueError, match='binding_conflict'):
        P.Producer(db, moved, 'forecast', NOW+DAY+1)
    with pytest.raises(ValueError, match='binding_conflict'):
        P.flush(db, moved, 'forecast')
    with pytest.raises(ValueError, match='binding_conflict'):
        P.flush(db, dict(a, export_directory=str(tmp_path/'elsewhere')), 'forecast')
    # Old window cannot resume once closed; clocks stay monotonic across windows.
    with pytest.raises(ValueError, match='window_closed'):
        P.Producer(db, a, 'forecast', NOW+DAY+1)
    with pytest.raises(ValueError, match='clock_regression'):
        P.Producer(db, b, 'forecast', NOW+DAY-1)


@pytest.mark.parametrize('over,now,reason', [
    (dict(start=NOW+DAY-1, frozen=NOW+DAY-1000), NOW+DAY, 'window_overlap'),
    (dict(frozen=NOW+DAY+1), NOW+DAY+1, 'retroactive_freeze'),
    (dict(start=NOW+DAY, frozen=NOW), NOW+DAY-1, 'prior_window_open'),
    (dict(prior=None), NOW+DAY, 'prior_window_unknown'),
])
def test_invalid_rotation_leaves_ledger_unchanged(tmp_path, over, now, reason):
    a = config(tmp_path)
    db = sqlite3.connect(tmp_path/'p.db')
    with db:
        P.Producer(db, a, 'forecast', NOW)
    P.flush(db, a, 'forecast')
    before = db.execute('SELECT * FROM population_meta ORDER BY key').fetchall(), rows(db)
    with pytest.raises(ValueError, match=reason):
        with db:
            P.Producer(db, second(tmp_path, a, **over), 'forecast', now)
    assert (db.execute('SELECT * FROM population_meta ORDER BY key').fetchall(), rows(db)) == before


def test_refreeze_of_active_window_is_refused(tmp_path):
    a = config(tmp_path)
    b = second(tmp_path, a)
    db = sqlite3.connect(tmp_path/'p.db')
    opened(db, a, NOW)
    opened(db, b, NOW+DAY)
    declaration = json.loads(Path(b['declaration']).read_text())
    Path(b['receipt']).write_text(json.dumps(D.freeze(declaration, NOW+DAY-999,
                                                      code_manifest={'fixture': 'synthetic'})))
    with pytest.raises(ValueError, match='binding_conflict'):
        P.Producer(db, b, 'forecast', NOW+DAY+1)


def test_third_window_uses_recorded_bounds(tmp_path):
    a = config(tmp_path)
    b = second(tmp_path, a)
    db = sqlite3.connect(tmp_path/'p.db')
    opened(db, a, NOW)
    opened(db, b, NOW+DAY)
    overlap = window(tmp_path, 'c', NOW+2*DAY-1, NOW+3*DAY, NOW+DAY)
    with pytest.raises(ValueError, match='window_overlap'):
        P.Producer(db, overlap, 'forecast', NOW+2*DAY)
    c = window(tmp_path, 'c', NOW+2*DAY, NOW+3*DAY, NOW+2*DAY-1)
    pop = opened(db, c, NOW+2*DAY)
    assert pop.eligible('S0/USDT', NOW+2*DAY) and not pop.eligible('S0/USDT', NOW+2*DAY-1)
    with pytest.raises(ValueError, match='window_closed'):
        P.Producer(db, b, 'forecast', NOW+2*DAY+1)
