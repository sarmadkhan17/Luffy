"""Durable, append-only history of public venue registry truth and universe selection.

Records what was observed, never what is permitted. Account-level eligibility
in a stored snapshot is whatever the snapshot says (UNKNOWN for the public
provider); UNKNOWN never authorizes trading or exposure, and nothing here is
read by Risk or entry capability. No network, no private venue call.

Tables live in the shared journal database and are created lazily:

- registry_contents / registry_snapshots: each RegistrySnapshot exactly,
  reconstructable byte-for-byte (`snapshot_json`) and verifiable against its
  `snapshot_id`. Record sets are stored once per content hash (records carry
  their own observation time, so each refresh is normally a new version).
- registry_refreshes: one row per refresh attempt, failures included.
- universe_revisions: each tradable-scan universe selection with its
  members, explicit exclusion reasons and revision identity, per environment.
- universe_untradeable: symbols the venue refused for this account, per
  environment, so a restart does not forget them.

All tables refuse UPDATE and DELETE except universe_untradeable's insert-only
first-seen rule.
"""
from __future__ import annotations

import hashlib
import json
import time

DDL = """
CREATE TABLE IF NOT EXISTS registry_contents (
    content_sha256 TEXT PRIMARY KEY,
    records_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS registry_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    environment TEXT NOT NULL,
    source TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    as_of_ms INTEGER NOT NULL,
    account_scope TEXT NOT NULL,
    account_trading TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL REFERENCES registry_contents(content_sha256),
    recorded_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS registry_refreshes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outcome TEXT NOT NULL,
    failure_reason TEXT,
    environment TEXT NOT NULL,
    source TEXT NOT NULL,
    request_start_ms INTEGER NOT NULL,
    response_received_ms INTEGER,
    http_status INTEGER,
    body_sha256 TEXT,
    snapshot_id TEXT REFERENCES registry_snapshots(snapshot_id),
    provenance_json TEXT NOT NULL,
    recorded_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS universe_revisions (
    revision_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    selection_version TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at_ms INTEGER NOT NULL,
    members_json TEXT NOT NULL,
    exclusions_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    recorded_ms INTEGER NOT NULL,
    PRIMARY KEY (environment, revision_id)
);
CREATE TABLE IF NOT EXISTS universe_untradeable (
    environment TEXT NOT NULL,
    symbol TEXT NOT NULL,
    reason TEXT NOT NULL,
    first_seen_ms INTEGER NOT NULL,
    PRIMARY KEY (environment, symbol)
);
"""
_IMMUTABLE = ("registry_contents", "registry_snapshots", "registry_refreshes", "universe_revisions")
_GUARDS = "".join(
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t} "
    f"BEGIN SELECT RAISE(ABORT,'{t} is append-only'); END;"
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t} "
    f"BEGIN SELECT RAISE(ABORT,'{t} is append-only'); END;" for t in _IMMUTABLE)

ENVIRONMENTS = ("production", "demo")


class RegistryStoreError(ValueError):
    pass


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _ms() -> int:
    return time.time_ns() // 1_000_000


def _env(environment: str) -> str:
    if environment not in ENVIRONMENTS:
        raise RegistryStoreError("environment must be production or demo")
    return environment


def ensure(conn) -> None:
    conn.executescript(DDL + _GUARDS)


def record_snapshot(journal, snapshot, environment: str, *, recorded_ms: int | None = None) -> str:
    """Persist one RegistrySnapshot exactly; idempotent per snapshot_id."""
    env = _env(environment)
    canonical = snapshot.canonical_json()
    if _sha(canonical) != snapshot.snapshot_id:
        raise RegistryStoreError("snapshot_id does not match its canonical form")
    payload = json.loads(canonical)
    records_json = _canon(payload["records"])
    content = _sha(records_json)
    with journal._tx() as c:
        ensure(c)
        old = c.execute("SELECT environment FROM registry_snapshots WHERE snapshot_id=?",
                        (snapshot.snapshot_id,)).fetchone()
        if old is not None:
            if old[0] != env:
                raise RegistryStoreError("snapshot already recorded under another environment")
            return snapshot.snapshot_id
        c.execute("INSERT OR IGNORE INTO registry_contents VALUES (?,?)", (content, records_json))
        c.execute("INSERT INTO registry_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (snapshot.snapshot_id, env, snapshot.source, snapshot.schema_version,
                   snapshot.as_of_ms, snapshot.account_scope, snapshot.account_trading.value,
                   len(snapshot.records), content, recorded_ms if recorded_ms is not None else _ms()))
    return snapshot.snapshot_id


def snapshot_json(journal, snapshot_id: str) -> str:
    """The stored snapshot's exact canonical JSON, verified against its id."""
    with journal._tx() as c:
        ensure(c)
        row = c.execute(
            "SELECT s.schema_version,s.as_of_ms,s.account_scope,s.account_trading,s.source,k.records_json "
            "FROM registry_snapshots s JOIN registry_contents k USING(content_sha256) "
            "WHERE s.snapshot_id=?", (snapshot_id,)).fetchone()
    if row is None:
        raise RegistryStoreError("unknown_snapshot")
    text = _canon({"schema_version": row[0], "as_of_ms": row[1], "account_scope": row[2],
                   "account_trading": row[3], "source": row[4], "records": json.loads(row[5])})
    if _sha(text) != snapshot_id:
        raise RegistryStoreError("stored_snapshot_corrupt")
    return text


def record_refresh(journal, result, *, recorded_ms: int | None = None) -> None:
    """Persist one refresh attempt (RegistryRefreshResult); failures included.
    A successful snapshot is stored first, under the attempt's declared environment."""
    from dataclasses import asdict
    prov = result.provenance
    env = _env(prov.environment)
    if result.snapshot is not None:
        record_snapshot(journal, result.snapshot, env, recorded_ms=recorded_ms)
    with journal._tx() as c:
        ensure(c)
        c.execute("INSERT INTO registry_refreshes(outcome,failure_reason,environment,source,request_start_ms,"
                  "response_received_ms,http_status,body_sha256,snapshot_id,provenance_json,recorded_ms) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (prov.outcome, prov.failure_reason, env, prov.source, prov.request_start_ms,
                   prov.response_received_ms, prov.http_status, prov.body_sha256, prov.snapshot_id,
                   _canon(asdict(prov)), recorded_ms if recorded_ms is not None else _ms()))


def record_universe_revision(journal, receipt: dict, environment: str, *, recorded_ms: int | None = None) -> bool:
    """Persist one universe selection receipt's identity, members and exclusions.
    True if newly recorded. The same revision under different content is refused."""
    env = _env(environment)
    members = _canon(list(receipt["members"]))
    exclusions = _canon(receipt.get("exclusions"))
    if receipt.get("exclusions") is None:
        raise RegistryStoreError("exclusions_missing")
    config = _canon({"selection_config": receipt["selection_config"],
                     "majors_unobserved": receipt.get("majors_unobserved", [])})
    with journal._tx() as c:
        ensure(c)
        old = c.execute("SELECT members_json,exclusions_json,config_json FROM universe_revisions "
                        "WHERE environment=? AND revision_id=?", (env, receipt["revision_id"])).fetchone()
        if old is not None:
            if tuple(old) != (members, exclusions, config):
                raise RegistryStoreError("revision_id_content_conflict")
            return False
        c.execute("INSERT INTO universe_revisions VALUES (?,?,?,?,?,?,?,?,?)",
                  (receipt["revision_id"], env, receipt["selection_version"], receipt["source"],
                   receipt["observed_at_ms"], members, exclusions, config,
                   recorded_ms if recorded_ms is not None else _ms()))
    return True


def record_untradeable(journal, environment: str, symbol: str, reason: str,
                       *, now_ms: int | None = None) -> bool:
    """Remember a venue refusal for this environment; first sighting wins."""
    env = _env(environment)
    if not symbol or not isinstance(symbol, str):
        raise RegistryStoreError("symbol_required")
    with journal._tx() as c:
        ensure(c)
        cur = c.execute("INSERT OR IGNORE INTO universe_untradeable VALUES (?,?,?,?)",
                        (env, symbol, str(reason)[:200], now_ms if now_ms is not None else _ms()))
        return cur.rowcount == 1


def load_untradeable(journal, environment: str) -> dict[str, str]:
    env = _env(environment)
    with journal._tx() as c:
        ensure(c)
        return {r[0]: r[1] for r in c.execute(
            "SELECT symbol,reason FROM universe_untradeable WHERE environment=?", (env,))}
