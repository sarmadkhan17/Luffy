"""Temporary SQLite and synthetic-array regressions for the unified hot paths."""
import json
import runpy
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from trader.data.derivatives import DerivFeed, SQLITE_BUSY_MS, SQLITE_CACHE_KIB
from trader.strategy.vector_backtest import walk_table, rotation_pf
from trader.research.referee import _pf
from trader.observability import declared


def test_initialized_rollback_store_reestablishes_wal_and_connection_bounds(tmp_path):
    path = tmp_path/'derivs.db'
    feed = DerivFeed(path)
    feed.db.close()
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
    reopened = DerivFeed(path)
    try:
        db = reopened.db
        for pragma, expected in [('journal_mode','wal'),('foreign_keys',1),('synchronous',1),
                                 ('temp_store',2),('cache_size',-SQLITE_CACHE_KIB),('busy_timeout',SQLITE_BUSY_MS)]:
            assert db.execute('PRAGMA '+pragma).fetchone()[0] == expected
        assert db.total_changes == 0
    finally:
        reopened.db.close()


def test_readonly_open_does_not_mutate_rollback_store(tmp_path):
    path = tmp_path/'derivs.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE example(x)')
    before = path.read_bytes()
    feed = DerivFeed(path,read_only=True)
    try:
        assert feed.db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        assert feed.db.execute('PRAGMA query_only').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError): feed.db.execute('INSERT INTO example VALUES (1)')
    finally:
        feed.db.close()
    assert path.read_bytes() == before


@pytest.mark.parametrize('first', [0,210,287,800])
@pytest.mark.parametrize('offset', [0,1,-3,317,1599])
@pytest.mark.parametrize('nonfinite', [False, True])
def test_modular_fused_pf_is_exact_roll_then_mask(first, offset, nonfinite):
    rng = np.random.default_rng(23)
    n = 800
    long,short = rng.random(n)<.25,rng.random(n)<.25
    table = {}
    for side in ('long','short'):
        ex = np.minimum(np.arange(n)+rng.integers(0,16,n),n-1)
        ex[rng.random(n)<.1] = -1
        ex[-20:] = -2  # censoring stops the entire walk, after the dust guard
        rates = rng.normal(-.2,1.5,n)
        if nonfinite:
            rates[211] = np.nan
        pos = rng.choice([.01,1.,10.],n)
        table[side] = ex,rates,pos
    risk = dict(risk_per_trade_pct=.5)
    for equity in (100.,2000.,1e7):
        lo,sh = np.roll(long,offset),np.roll(short,offset)
        lo[:first] = False; sh[:first] = False
        walk = walk_table(table,lo,sh,risk,equity)
        # Independent historical Python arithmetic, including NaN and dust.
        eq,win,loss = equity,0.,0.
        for _,_,r in walk:
            pnl = r*eq*(risk['risk_per_trade_pct']/100.)
            if pnl>0: win+=pnl
            else: loss+=-pnl
            eq+=pnl
        pf = (float('inf') if win>0 else 0.) if loss==0 else win/loss
        actual,count = rotation_pf(table,long,short,risk,equity,offset=offset,first_bar=first)
        assert count == len(walk)
        assert actual == pf or np.isnan(actual) and np.isnan(pf)
        assert _pf(walk,risk,equity) == pf or np.isnan(_pf(walk,risk,equity)) and np.isnan(pf)


def test_declared_fetch_uses_standalone_helper_with_total_deadline(monkeypatch):
    def run(argv,**kwargs):
        assert argv[:2] == [sys.executable,'-B']
        assert Path(argv[2]).name == 'declared_fetch.py'
        assert '-m' not in argv and kwargs['timeout'] == 6
        return SimpleNamespace(returncode=0,stdout='[]')
    monkeypatch.setattr(declared.subprocess,'run',run)
    assert declared.fetch('BTC/USDT') == []


def test_lightweight_helper_is_stdlib_only_and_bounds_response(monkeypatch):
    import builtins
    original = builtins.__import__
    def restricted(name,*a,**k):
        assert name.split('.')[0] not in {'trader','numpy','pandas','numba'}
        return original(name,*a,**k)
    monkeypatch.setattr(builtins,'__import__',restricted)
    helper = runpy.run_path(str(Path(declared.__file__).resolve().parents[2]/'scripts/declared_fetch.py'))
    class Response:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self,limit):
            assert limit == 65537
            return b'x'*limit
    def open_url(url,timeout):
        assert 'symbol=BTCUSDT' in url and timeout == 4
        return Response()
    helper['fetch'].__globals__['urlopen'] = open_url
    with pytest.raises(ValueError,match='too_large'): helper['fetch']('BTC/USDT')
    with pytest.raises(ValueError,match='invalid_symbol'): helper['fetch']('../../bad')
