"""Owner queries resolve stored cuts without rewriting evidence or guessing."""
import json
import sqlite3

import pytest

from trader.core.journal import Journal
from trader.core import journal_evidence as E
from trader.owner import queries as Q
from trader.portfolio.runtime import SCHEMA


@pytest.mark.parametrize('storage', ['inline', 'segmented'])
@pytest.mark.parametrize('mutation', ['none', 'identity', 'schema', 'blob'])
def test_owner_portfolio_exact_read_refuses_forgery(tmp_path, storage, mutation):
    path = tmp_path / 'data/runtime-portfolio.db'
    path.parent.mkdir()
    body = dict(schema=SCHEMA, portfolio_cut_id='TEST-ONLY-cut',
                event_receipt={'inputs': {'current': {'as_of_ms': 1000}}})
    if mutation == 'identity': body['portfolio_cut_id'] = 'foreign'
    if mutation == 'schema': body['schema'] = 'unknown'
    with sqlite3.connect(path) as db:
        db.executescript(E.BLOB_SCHEMA)
        db.execute('CREATE TABLE evaluations(cut_id TEXT PRIMARY KEY,payload TEXT)')
        raw = json.dumps(body)
        if storage == 'segmented': raw = E.store(db, raw)
        if mutation == 'blob':
            if storage == 'segmented':
                db.execute("UPDATE journal_evidence_blobs_v1 SET payload=X'00'")
            else:
                raw = '!luffy-journal-detail.v1!broken'
        db.execute('INSERT INTO evaluations VALUES(?,?)', ('TEST-ONLY-cut', raw))
    before = path.read_bytes()
    j = Journal(tmp_path / 'j.db')
    if mutation == 'none':
        out = Q.query(j, 'portfolio', 'TEST-ONLY-cut', root=tmp_path)
        assert out['records'][0]['value'] == body
        assert out['records'][0]['available_at'] is None
    else:
        with pytest.raises(ValueError):
            Q.query(j, 'portfolio', 'TEST-ONLY-cut', root=tmp_path)
    assert path.read_bytes() == before
