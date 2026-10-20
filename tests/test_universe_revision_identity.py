"""Offline regression for immutable persisted universe provenance headers."""
import socket

import pytest

from trader.core.journal import Journal
from trader.data import registry_store as store


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('external connection attempted')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)


@pytest.mark.parametrize('field,replacement', [
    ('selection_version', 'universe.future.v2'),
    ('source', 'different-venue'),
    ('observed_at_ms', 2001),
])
def test_existing_revision_refuses_changed_provenance_header(tmp_path, field, replacement):
    journal = Journal(tmp_path / 'journal.db')
    receipt = dict(revision_id='retained-historical-id',
                   selection_version='universe.volume-age.v1', source='original-venue',
                   observed_at_ms=2000, members=['BTC/USDT'], exclusions={},
                   selection_config={'top_n': 1}, majors_unobserved=[])
    assert store.record_universe_revision(journal, receipt, 'demo', recorded_ms=2100)
    assert not store.record_universe_revision(journal, receipt, 'demo', recorded_ms=2200)
    with pytest.raises(store.RegistryStoreError, match='revision_id_content_conflict'):
        store.record_universe_revision(journal, dict(receipt, **{field: replacement}), 'demo')
    with journal._tx() as conn:
        row = conn.execute('SELECT selection_version,source,observed_at_ms,recorded_ms '
                           'FROM universe_revisions').fetchone()
    assert tuple(row) == ('universe.volume-age.v1', 'original-venue', 2000, 2100)
    # A shared revision label in another environment is a separate observation.
    assert store.record_universe_revision(journal, receipt, 'production', recorded_ms=2300)
    with journal._tx() as conn:
        assert conn.execute('SELECT COUNT(*) FROM universe_revisions').fetchone()[0] == 2
