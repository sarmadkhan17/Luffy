"""Read-only grandfather authority. No migration or runtime grant issuer.

A historical exception must already have an immutable owner receipt naming
this exact installed definition. Merely predating Factory grants nothing.
New installs and changed definitions fail closed. Tests use TEST-ONLY grants
in temporary journals; this module never writes production authority.
"""
import hashlib
import json

# First versioned Factory authority boundary, not a per-process boot time.
FACTORY_BOUNDARY_MS = 1790726400000  # 2026-09-30T00:00:00Z
TABLE = 'strategy_legacy_authorities'
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
 strategy_id TEXT PRIMARY KEY, canonical_json TEXT NOT NULL,
 canonical_sha256 TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS legacy_authority_no_insert BEFORE INSERT ON {TABLE}
BEGIN SELECT RAISE(ABORT, 'LEGACY_AUTHORITY_IMPORT_REQUIRED'); END;
CREATE TRIGGER IF NOT EXISTS legacy_authority_no_update BEFORE UPDATE ON {TABLE}
BEGIN SELECT RAISE(ABORT, 'legacy authority is immutable'); END;
CREATE TRIGGER IF NOT EXISTS legacy_authority_no_delete BEFORE DELETE ON {TABLE}
BEGIN SELECT RAISE(ABORT, 'legacy authority is immutable'); END;
"""


def definition_hash(row):
    """Identity excludes mutable stats/lifecycle, includes all trading inputs."""
    from ..engine.paper_exit_evidence import canonical
    if row['kind'] == 'spec':
        from .factory_handoff import _frozen_spec
        from .spec import StrategySpec
        return _frozen_spec(StrategySpec.from_json(row['spec_json']))[1]
    body = {k: row[k] for k in ('id', 'kind', 'params', 'regime_filter', 'markets')}
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def grandfathered(journal, strategy_id):
    from ..engine.paper_exit_evidence import canonical
    from ..engine.state import OWNER_ACTORS
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)):
        return False
    grants = journal.query(f'SELECT * FROM {TABLE} WHERE strategy_id=?', (strategy_id,))
    rows = journal.query('SELECT * FROM strategies WHERE id=?', (strategy_id,))
    if len(grants) != 1 or len(rows) != 1:
        return False
    grant, row = grants[0], rows[0]
    body = json.loads(grant['canonical_json'])
    return (canonical(body) == grant['canonical_json']
        and hashlib.sha256(grant['canonical_json'].encode()).hexdigest() == grant['canonical_sha256']
        and body.get('schema') == 'strategy-legacy-authority.v1'
        and body.get('strategy_id') == strategy_id
        and body.get('definition_sha256') == definition_hash(row)
        and body.get('actor') in OWNER_ACTORS
        and body.get('authority') == 'GRANDFATHERED_REAL_EXECUTION'
        and type(body.get('issued_at_ms')) is int
        and 0 < body['issued_at_ms'] < FACTORY_BOUNDARY_MS
        and row['state'] in ('paper', 'active', 'demoted'))
