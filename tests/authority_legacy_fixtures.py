"""Explicit TEST-ONLY historical authority; never imported by runtime."""
from trader.engine.paper_exit_evidence import canonical
from trader.strategy import legacy_authority as L
import hashlib


def grant(j, sid):
    row = j.query('SELECT * FROM strategies WHERE id=?', (sid,))[0]
    body = dict(schema='strategy-legacy-authority.v1', strategy_id=sid,
        definition_sha256=L.definition_hash(row), actor='operator',
        authority='GRANDFATHERED_REAL_EXECUTION', issued_at_ms=L.FACTORY_BOUNDARY_MS-1,
        provenance='TEST-ONLY historical owner receipt')
    text = canonical(body)
    with j._tx() as db:
        # Simulate a receipt predating installation of the closed import fence.
        db.execute("DROP TRIGGER legacy_authority_no_insert")
        db.execute(f'INSERT INTO {L.TABLE} VALUES(?,?,?)',
            (sid, text, hashlib.sha256(text.encode()).hexdigest()))
        db.execute(f"CREATE TRIGGER legacy_authority_no_insert BEFORE INSERT ON {L.TABLE} BEGIN SELECT RAISE(ABORT, 'LEGACY_AUTHORITY_IMPORT_REQUIRED'); END")


def seed(j, sid='strategy'):
    with j._tx() as db:
        db.execute("INSERT INTO strategies(id,name,kind,params,state,regime_filter,markets) VALUES(?,?,'ema_trend','{}','active','[]','[]')", (sid,sid))
    grant(j, sid)
