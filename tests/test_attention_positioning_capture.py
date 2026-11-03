"""PIT positioning is acquired in the shared disposable persistence path."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.observability import positioning as P
from trader.observability.store import Store, read_latest
from trader.observability.attention import settings, evaluate_snapshot, code_manifest, digest
from trader.cognition.attention import POSITIONING_REFERENCE
from trader.cognition.contracts import POSITIONING_SERIES
from trader.data import market_provenance as mp
from tests.test_attention_telemetry import event, frames

CUT = 1_800_000_000_000 // 14_400_000 * 14_400_000


@pytest.fixture
def derivatives(tmp_path):
    path = tmp_path/'derivs.db'
    with sqlite3.connect(path) as db:
        mp.init(db)
        for symbol in ('S0/USDT','S1/USDT'):
            for series in POSITIONING_SERIES:
                step = 28_800_000 if series == 'funding' else 900_000
                events = CUT - 300_000 - np.arange(POSITIONING_REFERENCE,-1,-1)*step
                values = np.r_[np.arange(POSITIONING_REFERENCE,dtype=float),100.]
                frame = pd.DataFrame({'ts':pd.to_datetime(events,unit='ms',utc=True),'value':values})
                retained = mp.annotate(frame,instrument_id='test:'+symbol,source='recorded:'+series,
                                       kind='derivative',received_ms=CUT-1000)
                mp.append(db,'derivative:'+symbol+':'+series,retained)
                # A later, contradictory retrieval must not leak into this scan.
                late = mp.annotate(frame.iloc[-1:].assign(value=-10000.),instrument_id='test:'+symbol,
                                   source='recorded:'+series,kind='derivative',received_ms=CUT+1000)
                mp.append(db,'derivative:'+symbol+':'+series,late)
    return path


def test_reader_uses_injected_readonly_connection_and_excludes_future_revisions(derivatives):
    before = hashlib.sha256(derivatives.read_bytes()).hexdigest()
    calls, statements = [], []
    def connect(database, **kwargs):
        calls.append((database,kwargs))
        assert database.endswith('?mode=ro')
        db = sqlite3.connect(database,**kwargs)
        db.set_trace_callback(statements.append)
        return db
    records, provenance = P.read(derivatives,['S1/USDT','S0/USDT'],CUT,connect=connect)
    assert provenance['status'] == 'ok' and len(calls) == 1, provenance
    assert len(records) == 2 * len(POSITIONING_SERIES) * (POSITIONING_REFERENCE+1)
    assert max(p['value'] for p in records) == 100.
    assert all(p['observed_ms'] <= CUT and p['available_ms'] <= CUT for p in records)
    assert sum(len(ids) for ids in provenance['revisions'].values()) == len(records)
    assert any(s == 'BEGIN' for s in statements)
    assert not any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER')) for s in statements)
    assert hashlib.sha256(derivatives.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('declared', [False, True])
def test_shared_store_freezes_positioning_and_exposes_crowding(derivatives, tmp_path, monkeypatch, declared):
    directory = tmp_path/'declared-population' if declared else tmp_path
    store = Store(directory/'attention.db',settings())
    original = P.read
    calls = []
    def captured(path,*args,**kwargs):
        assert path == derivatives
        assert not store.db.in_transaction, 'acquisition must precede writer ownership'
        calls.append(path)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(P,'read',captured)
    try:
        scan = event('positioning',CUT,frames(2,CUT))
        store.write(scan)
        store.write(dict(kind='causes',scan_id='positioning',as_of_ms=CUT,items=[]))
        payload = read_latest(store.path,now_ms=CUT)['scan']
        assert len(calls) == 1
        assert payload['positioning_capture']['status'] == 'ok', payload['positioning_capture']
        assert len(payload['positioning_input']) == 84
        assert 'observability/positioning.py' in payload['code_manifest']
        assert payload['input_hash'] == digest(scan['input'])
        row = next(row for row in payload['rows'] if row['symbol'] == 'S0/USDT')
        assert set(row['positioning']) == set(POSITIONING_SERIES)
        assert all(p['status'] == 'ok' and p['z'] > 0 for p in row['positioning'].values())
        # Duplicate persistence must not query or replace the frozen inputs.
        store.write(event('positioning',CUT,frames(2,CUT)))
        assert len(calls) == 1
        # Replay evaluates exactly the retained input without opening derivs.db.
        monkeypatch.setattr(P,'read',lambda *a,**k:pytest.fail('replay reacquired positioning'))
        assert evaluate_snapshot(scan)['rows'] == payload['rows']
    finally:
        store.close()


def test_missing_derivative_store_is_nonfatal_and_does_not_create_it(tmp_path):
    scan = event('no-positioning',CUT,frames(2,CUT))
    expected = evaluate_snapshot(copy.deepcopy(scan))['rows']
    store = Store(tmp_path/'attention.db',settings())
    try:
        store.write(scan)
        assert 'positioning' not in scan['input']
        assert scan['positioning_capture']['status'] == 'positioning_unavailable'
        assert scan['issues'][-1]['reason'] == 'positioning_unavailable'
        assert evaluate_snapshot(scan)['rows'] == expected
        assert not (tmp_path/'derivs.db').exists()
    finally:
        store.close()


def test_supplied_frozen_positioning_is_not_overwritten(tmp_path, monkeypatch):
    scan = event('retained',CUT,frames(2,CUT))
    scan['input']['positioning'] = []
    monkeypatch.setattr(P,'read',lambda *a,**k:pytest.fail('frozen input overwritten'))
    store = Store(tmp_path/'attention.db',settings())
    try:
        store.write(scan)
        assert scan['input']['positioning'] == []
    finally:
        store.close()


def test_producer_only_queues_capture_without_reading_positioning(tmp_path, monkeypatch):
    from trader.observability.collector import Collector
    collector = Collector(tmp_path, start=False)
    monkeypatch.setattr(P, 'read', lambda *a, **k: pytest.fail('producer read positioning'))
    assert collector.begin(frames(2, CUT), ['S0/USDT', 'S1/USDT'], CUT)
    queued = collector.queue.get_nowait()
    assert 'positioning' not in queued['input']
    assert 'positioning_capture' not in queued
    assert not (tmp_path/'attention.db').exists()


def test_failed_reader_is_explicit_and_capture_module_is_hashed(derivatives):
    def broken(*a,**k): raise sqlite3.OperationalError('fixture unavailable')
    result, provenance = P.read(derivatives,['S0/USDT'],CUT,connect=broken)
    assert result is None and provenance['reason'] == 'OperationalError'
    assert provenance['status'] == 'positioning_unavailable'
    assert code_manifest()['observability/positioning.py'] == hashlib.sha256(Path(P.__file__).read_bytes()).hexdigest()
