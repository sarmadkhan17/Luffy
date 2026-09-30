import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from trader.dashboard import owner_api as api


def test_cache_reuses_snapshot_and_invalidates_all_source_changes(tmp_path, monkeypatch):
    p = tmp_path / 'a.md'
    p.write_text('# A\n')
    cache = api.KnowledgeReadCache(tmp_path)
    original = api.knowledge
    reads = []
    def read(*a, **kw):
        reads.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(api, 'knowledge', read)
    first = cache.read()
    assert cache.read() is first and len(reads) == 1
    assert json.loads(first)['generated_at'] == json.loads(cache.read())['generated_at']
    st = p.stat()
    p.write_text('# B\n')
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert cache.read() != first and len(reads) == 2  # same mtime/size, ctime changed
    p.rename(tmp_path / 'b.md')
    cache.read()
    p.write_text('# C\n')
    assert json.loads(cache.read())['total_nodes'] == 2
    p.unlink()
    assert json.loads(cache.read())['total_nodes'] == 1
    assert len(reads) == 5


def test_cache_failure_never_falls_back_and_memory_is_bounded(tmp_path, monkeypatch):
    p = tmp_path / 'a.md'
    p.write_text('# A')
    cache = api.KnowledgeReadCache(tmp_path)
    cache.read()
    p.write_text('# changed')
    def fail(*a, **kw):
        raise OSError('unreadable')
    with monkeypatch.context() as m:
        m.setattr(api, 'knowledge', fail)
        with pytest.raises(OSError):
            cache.read()
        assert cache.encoded is None and cache.key is None
    cache.MAX_BYTES = 1
    assert cache.read() and cache.encoded is None
    cache.MAX_BYTES = 200000
    cache.MAX_FILES = 0
    assert cache.read() and cache.encoded is None


def test_cache_rejects_continuously_moving_source(tmp_path, monkeypatch):
    p = tmp_path / 'a.md'
    p.write_text('# A')
    cache = api.KnowledgeReadCache(tmp_path)
    original = api.knowledge
    def moving(*a, **kw):
        result = original(*a, **kw)
        p.write_text(p.read_text() + '\nchanged')
        return result
    monkeypatch.setattr(api, 'knowledge', moving)
    with pytest.raises(OSError, match='changed_during_read'):
        cache.read()
    assert cache.encoded is None


def test_cache_serializes_concurrent_builds(tmp_path, monkeypatch):
    (tmp_path / 'a.md').write_text('# A')
    original = api.knowledge
    reads = []
    def read(*a, **kw):
        reads.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(api, 'knowledge', read)
    cache = api.KnowledgeReadCache(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: cache.read(), range(16)))
    assert len(reads) == 1 and all(r == results[0] for r in results)


@pytest.mark.parametrize('missing', ['venue_positions', 'reconciliation', 'venue_protection',
                                     'complete_listing', 'observation_consistent',
                                     'precision_known'])
def test_verified_requires_each_available_check(missing):
    now = datetime.now(timezone.utc)
    trade = {'symbol': 'BTC/USDT', 'opened_at': (now-timedelta(minutes=1)).isoformat()}
    rec = {'symbol': 'BTC/USDT', 'stop_present': True, 'verified': True, 'reasons': []}
    snap = dict(status='VERIFIED', reasons=[], observed_at=now.isoformat(), freshness='fresh',
                venue_positions=True, reconciliation=True, venue_protection=True,
                complete_listing=True, observation_consistent=True, precision_known=True,
                symbols=[rec])
    assert api.protection_for(trade, snap)['status'] == 'VERIFIED'
    for value in (False, None):
        snap[missing] = value
        assert api.protection_for(trade, snap)['status'] == 'PARTIAL'
    snap['freshness'] = 'stale'
    assert api.protection_for(trade, snap)['status'] == 'STALE'
