"""Input-stage routing: development-only derivative publication and protected-derivative qualification paths.
Transport/metadata fixtures only; no market-success claim is made or manufactured."""
import sqlite3

import pytest

from tests.test_retrospective_publication import STEP, source
from trader.research.closure import ClosureRefused, study as S
from trader.research.closure.publish import publish_exact_cut
from trader.research.closure.store import Store

COVER = dict(fit_bars=4, reserve_bars=3, max_gap_bars=0)


def derivs(path):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE derivs(symbol TEXT NOT NULL,series TEXT NOT NULL,ts INTEGER NOT NULL,value REAL,'
                   'PRIMARY KEY(symbol,series,ts))')
        db.executemany('INSERT INTO derivs VALUES (?,?,?,?)', [('A', 'funding', i * STEP, i / 100) for i in range(8)])


def publish(tmp_path, name, **kw):
    return publish_exact_cut(tmp_path / 'source.db', tmp_path / f'{name}.db', tf='4h', window_bars=8, now_ms=9 * STEP,
                             deriv_src=tmp_path / 'derivs.db', dst_derivs=tmp_path / f'{name}-derivs.db', **kw)


def events(path):
    with sqlite3.connect(path) as db:
        return [r[0] // STEP for r in db.execute('SELECT ts FROM derivs ORDER BY ts')]


def test_development_only_derivatives_stop_before_reserve_and_keep_full_candles(tmp_path):
    source(tmp_path / 'source.db')
    derivs(tmp_path / 'derivs.db')
    before = ((tmp_path / 'source.db').read_bytes(), (tmp_path / 'derivs.db').read_bytes())
    full = publish(tmp_path, 'full', coverage_filter=COVER)
    dev = publish(tmp_path, 'dev', coverage_filter=COVER, development_derivatives_only=True)
    assert full['derivs']['data_scope'] == 'DEVELOPMENT_AND_PROTECTED'
    assert dev['derivs']['data_scope'] == 'DEVELOPMENT_ONLY'
    assert dev['as_of_ms'] == full['as_of_ms'] == 8 * STEP
    assert events(tmp_path / 'full-derivs.db') == list(range(8))
    assert events(tmp_path / 'dev-derivs.db') == list(range(5))
    assert dev['derivs']['event_range_ms'] == [0, 4 * STEP] and full['derivs']['event_range_ms'] == [0, 7 * STEP]
    # Protected final-test candle receipts are the full replay history in both copies.
    for name in ('full', 'dev'):
        with sqlite3.connect(tmp_path / f'{name}.db') as db:
            assert db.execute('SELECT COUNT(*) FROM candles').fetchone()[0] == 8
            late = db.execute('SELECT COUNT(*) FROM market_revisions WHERE event_ms>=?', (5 * STEP,)).fetchone()[0]
            assert late == 3 * 3        # 3 reserved events x 3 retained versions
    assert ((tmp_path / 'source.db').read_bytes(), (tmp_path / 'derivs.db').read_bytes()) == before


def test_development_only_derivatives_refuse_without_declared_coverage(tmp_path):
    source(tmp_path / 'source.db')
    derivs(tmp_path / 'derivs.db')
    with pytest.raises(ClosureRefused, match='development_derivatives_require_declared_window'):
        publish(tmp_path, 'dev', development_derivatives_only=True)
    assert not (tmp_path / 'dev.db').exists() and not (tmp_path / 'dev-derivs.db').exists()


@pytest.mark.parametrize('kind', ['alpha', 'hedge'])
@pytest.mark.parametrize('protected', [True, False])
def test_qualification_uses_protected_derivatives_else_falls_back(tmp_path, monkeypatch, kind, protected):
    store = Store(tmp_path / 'store.db')
    schema = S.hedge_qualification.FREEZE_SCHEMA if kind == 'hedge' else 'closure-freeze.synthetic'
    store.put_once('freezes', 'fz1', dict(schema=schema, freeze_id='fz1'))
    S._ensure_unit(store, 'st', 'freeze', 'freeze')
    S._set_unit(store, 'st', 'freeze', 'DONE', 1, result=dict(freeze_id='fz1'))
    S._ensure_unit(store, 'st', 'qualify', 'qualify', ['freeze'])
    seen = {}

    def stub(*args, **kwargs):
        seen['derivs_path'] = kwargs.get('derivs_path')
        return dict(status='NOT_QUALIFIED', reason='routing_stub', qualification_id='q', evidence_id='e')
    monkeypatch.setattr(S.hedge_qualification, 'qualify_hedge', stub)
    monkeypatch.setattr(S.qualification, 'qualify', stub)
    study = dict(unit_timeout_s=60, workdir=str(tmp_path / 'work'), snapshot_path=str(tmp_path / 'snap.db'),
                 book='book', source_journal_path='sj', budget_journal_path='bj', derivs_path='dev-derivs.db')
    if protected:
        study['protected_derivs_path'] = 'protected-derivs.db'
    unit = next(u for u in store.units('st') if u['unit_id'] == 'qualify')
    out = S._run_unit(store, 'st', study, dict(), unit, 2, None, timeout_s=60, child=False, cfg={})
    assert out['reason'] == 'routing_stub'
    assert seen['derivs_path'] == ('protected-derivs.db' if protected else 'dev-derivs.db')
    store.close()
