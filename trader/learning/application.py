"""Deterministic application of registered proposals; no learning policy here.

One exact historical evaluation is consumed at a time. Samples are recomputed
by the registered evaluator, never obtained by pooling outcome/lineage counts.
Explicit calls only: no Kernel wiring, activation, orders or LLM.
"""
from dataclasses import dataclass, asdict
from enum import Enum
import json
import hashlib
from pathlib import Path
from types import MappingProxyType

from . import foundation as L
from trader.strategy import factory_handoff as F

SCHEMA = 'learning-application-receipt.v1'
TABLE = 'learning_application_receipts'
TARGET_REGISTRY = MappingProxyType({t: tuple(r.rule_id for r in L.REGISTERED_RULES.values()
                                           if r.target == t) for t in L.Target})


class Result(str, Enum):
    APPLIED = 'APPLIED'
    NO_CHANGE = 'NO_CHANGE'
    INSUFFICIENT = 'INSUFFICIENT_EVIDENCE'
    STALE = 'STALE'
    CONFLICT = 'CONFLICT'
    UNREGISTERED = 'UNREGISTERED_RULE'
    INCOMPLETE = 'INCOMPLETE_REPLAY'


@dataclass(frozen=True)
class Request:
    evidence_id: str
    proposal_id: str
    target_json: str
    source_versions_json: str

    @property
    def application_id(self):
        return L.digest(asdict(self))


def source_versions(cfg):
    # Exact current evaluator and policies, not mutable 'latest' references.
    return dict(rule_version=L.digest(L.code_manifest()), risk_config=L.digest(cfg),
                application_authority=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def request(evidence, proposal, target, versions):
    return Request(evidence.evidence_id, proposal.proposal_id,
                   L.canonical(target), L.canonical(versions))


def ensure(journal):
    F.ensure(journal)
    with journal._tx() as c:
        c.executescript(f"""
        CREATE TABLE IF NOT EXISTS {TABLE}(
            application_id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL,
            result TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS learning_applied_proposal
            ON {TABLE}(proposal_id) WHERE result='APPLIED';
        CREATE TRIGGER IF NOT EXISTS learning_application_no_update
            BEFORE UPDATE ON {TABLE} BEGIN
            SELECT RAISE(ABORT, 'learning application receipt is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS learning_application_no_delete
            BEFORE DELETE ON {TABLE} BEGIN
            SELECT RAISE(ABORT, 'learning application receipt is immutable'); END;
        """)


class _BoundJournal:
    """All Governor reads use the same locked transaction as the receipt."""
    def __init__(self, connection):
        self.connection = connection

    def query(self, sql, params=()):
        cur = self.connection.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def _receipt(row):
    body = json.loads(row['payload'])
    if L.digest(body) != row['sha256'] or body['application_id'] != row['application_id']:
        raise ValueError('application_receipt_corrupt')
    return body


def _decision(journal, cfg, ev, proposal, req, at_ms):
    target = json.loads(req.target_json)
    versions = json.loads(req.source_versions_json)
    previous = target
    requested = json.loads(proposal.proposed_json) if proposal.proposed_json else None
    body = dict(schema=SCHEMA, application_id=req.application_id,
        learning_evidence_id=ev.evidence_id, proposal_id=proposal.proposal_id,
        rule_id=proposal.rule, rule_version=proposal.rule_version,
        target_type=proposal.target, target_id=target.get('version_id'),
        previous=previous, requested=requested, resulting=previous,
        governor_transition_receipt=None, applied_at=at_ms,
        source_hashes=dict(evidence=ev.provenance, current=versions,
                           target=L.digest(target)), request=asdict(req))

    def finish(result, reason):
        return json.loads(L.canonical(dict(body, result=result.value, reason=reason)))

    rule = L.REGISTERED_RULES.get(proposal.rule)
    if rule is None or proposal.target not in TARGET_REGISTRY or rule.target != proposal.target:
        return finish(Result.UNREGISTERED, 'exact rule/target is not registered')
    if req.evidence_id != ev.evidence_id or req.proposal_id != proposal.proposal_id:
        return finish(Result.CONFLICT, 'request evidence/proposal binding differs')
    if ev.quality != 'VERIFIED_REPLAY' or not L.verified_history(ev):
        return finish(Result.INCOMPLETE, 'outcome/attribution/evidence replay is not verified')
    history = json.loads(ev.historical_json)
    # Outcome time is also retained in the verified history, not supplied by a caller.
    if 'capture_manifest' in history:
        observed_ms = history['capture_manifest']['capture']['observed_ms']
    else:
        observed_ms = history['decision_manifest']['observed_ms']
    if type(at_ms) is not int or at_ms < observed_ms:
        return finish(Result.STALE, 'application clock precedes evidence')
    if proposal.rule_version != L.digest(L.code_manifest()):
        return finish(Result.STALE, 'exact registered evaluator version differs')
    current = json.loads(proposal.current_json)
    if set(target) != {'version_id', 'spec_hash', 'state', 'history_sha256'} or current != {k:v for k,v in target.items() if k != 'history_sha256'}:
        return finish(Result.CONFLICT, 'exact lifecycle precondition differs')
    expected = L.propose(ev, proposal.target, current, rule=proposal.rule)
    if expected != proposal:
        return finish(Result.CONFLICT, 'proposal is not the registered recomputed proposal')
    if proposal.status != L.Status.APPLICABLE:
        result = {L.Status.INCOMPLETE:Result.INCOMPLETE, L.Status.STALE:Result.STALE,
                  L.Status.CONFLICTING:Result.CONFLICT}.get(proposal.status, Result.INSUFFICIENT)
        return finish(result, proposal.reason)
    if versions != source_versions(cfg) or versions['risk_config'] != L.digest(history.get('risk_config')):
        return finish(Result.STALE, 'current source versions/policy differ')
    # Only the registered retirement action can reach the Governor. No generic updates.
    if requested != dict(current, state=F.RETIRED) or F.RETIRED not in rule.proposed_states:
        return finish(Result.UNREGISTERED, 'requested action is not registered')
    old = journal.query(f"SELECT * FROM {TABLE} WHERE proposal_id=? AND result='APPLIED'", (proposal.proposal_id,)) if journal.query("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)) else []
    if old:
        prior = _receipt(old[0])
        body['resulting'] = prior['resulting']
        body['governor_transition_receipt'] = prior['governor_transition_receipt']
        return finish(Result.NO_CHANGE, 'proposal already applied; reversal needs its own registered rule')
    try:
        actual = F.lifecycle_target(journal, current['version_id'])
    except (F.HandoffRefused, KeyError):
        return finish(Result.STALE, 'exact target unavailable')
    body['resulting'] = actual
    if actual != target:
        return finish(Result.STALE, 'target state/history changed since proposal creation')
    if F.RETIRED not in F.GOVERNOR_ALLOWED.get(actual['state'], set()):
        return finish(Result.CONFLICT, 'Governor does not authorize this lifecycle transition')
    # at_ms is checked against the Governor's clock before any mutation.
    events = F.governor_events(journal, current['version_id'])
    if events and at_ms < events[-1]['at_ms']:
        return finish(Result.STALE, 'Governor clock regressed')
    return finish(Result.APPLIED, proposal.reason)


def apply(journal, cfg, evidence, proposal, req, *, at_ms, shadow=False):
    """Compare-and-apply; Governor event and immutable receipt commit together.

    Shadow reads only. It never creates tables or calls a mutation authority.
    Failed gates may persist explanatory receipts but cannot mutate a target.
    """
    if shadow:
        return _decision(journal, cfg, evidence, proposal, req, at_ms)
    ensure(journal)
    with journal._tx() as c:
        F._begin(c)
        bound = _BoundJournal(c)
        rows = bound.query(f'SELECT * FROM {TABLE} WHERE application_id=?', (req.application_id,))
        if rows:
            prior = _receipt(rows[0])
            if (prior['learning_evidence_id'], prior['proposal_id']) != (evidence.evidence_id, proposal.proposal_id):
                raise ValueError('application_identity_conflict')
            return prior
        receipt = _decision(bound, cfg, evidence, proposal, req, at_ms)
        if receipt['result'] == Result.APPLIED.value:
            target = json.loads(req.target_json)
            transition = F.govern_version(bound, cfg, target['version_id'], F.RETIRED,
                actor='strategy_governor', reason_code=req.application_id, at_ms=at_ms,
                expected_target=target, _connection=c)
            receipt['governor_transition_receipt'] = transition
            receipt['resulting'] = F.lifecycle_target(bound, target['version_id'])
        c.execute(f'INSERT INTO {TABLE} VALUES(?,?,?,?,?)',
                  (req.application_id, proposal.proposal_id, receipt['result'],
                   L.canonical(receipt), L.digest(receipt)))
        return receipt


def prepare_captured(journal, cfg, outcome_id):
    """Explicit consumer for real retained evidence; missing sources stay missing."""
    from . import capture as C
    # capture API accepts a DB-like execute reader.
    class Reader:
        def execute(self, sql, params=()):
            rows = journal.query(sql, params)
            class Cursor:
                def fetchone(self):
                    return tuple(rows[0].values()) if rows else None
                def fetchall(self):
                    return [tuple(r.values()) for r in rows]
            return Cursor()
    o, sources = C.learning_outcome(Reader(), outcome_id)
    ev = L.evidence(o, L.attribute(o), L.replay(o, sources))
    target = dict(version_id=o.lineage.version_id, spec_hash=o.lineage.spec_hash, state='UNKNOWN', history_sha256=None)
    if o.lineage.version_id and journal.query("SELECT 1 FROM sqlite_master WHERE name='strategy_versions'"):
        try:
            target = F.lifecycle_target(journal, o.lineage.version_id)
        except (F.HandoffRefused, KeyError):
            pass
    current = {k:v for k,v in target.items() if k != 'history_sha256'}
    proposal = L.propose(ev, L.Target.LIFECYCLE, current, rule=L.DECAY_RULE)
    return ev, proposal, request(ev, proposal, target, source_versions(cfg))
