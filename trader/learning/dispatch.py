"""Generic deterministic dispatch: replay-complete evidence -> registered rules.

This module owns no learning policy. It inspects the exact registered
production rule registry (``targets.PRODUCTION_RULES``), keeps only rules whose
declared contract is compatible with one verified LearningEvidence, lets each
such rule create its own UpdateProposal, and queues it for the existing
LearningApplication checkpoint. With no compatible registered rule the answer
is NO_PROPOSAL / UNREGISTERED and nothing mutates. A target category existing is
never permission, and there is no generic win -> up / loss -> down behaviour.

Isolated tests may pass an explicit registry, but only through
``targets.registry`` which refuses anything except a Journal marked test-only,
outside ``data/``, with no live trades. The Kernel never passes one.
"""
from dataclasses import dataclass
from enum import Enum
import json
import logging
import time

from . import foundation as L, targets as T, application as A, runtime as R

log = logging.getLogger(__name__)

SCHEMA = 'learning-dispatch-receipt.v1'
TABLE = 'learning_dispatch_receipts'
CHAIN_META_TABLE = 'learning_chain_dispatch_meta_v1'


def ensure_chain_dispatch_meta(conn):
    """Create eligibility storage without reading or expanding retained chains.

    The caller owns the transaction. Readers reconcile missing legacy and
    newly committed chains before trusting this derived projection.
    """
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {CHAIN_META_TABLE} (
        outcome_id TEXT PRIMARY KEY,
        chain_rowid INTEGER NOT NULL UNIQUE,
        replay_gate TEXT NOT NULL,
        evidence_outcome_id TEXT,
        payload_sha256 TEXT NOT NULL
    )""")
    conn.execute(f"""CREATE INDEX IF NOT EXISTS learning_chain_ready_order_v1
        ON {CHAIN_META_TABLE}(outcome_id, chain_rowid)
        WHERE replay_gate = 'REPLAY_COMPLETE'""")


class Result(str, Enum):
    PROPOSED = 'PROPOSED'
    NO_PROPOSAL = 'NO_PROPOSAL'


@dataclass(frozen=True)
class View:
    """The exact replayed facts a rule may inspect; nothing is looked up later."""
    kind: L.Kind
    boundary: L.Boundary
    lineage: dict
    decision_ms: int
    observed_ms: int
    observation: dict
    roles: dict      # role -> dependency row (source_id, version, sha256, status ...)
    sources: dict    # role -> exact retained raw source


def view(ev):
    history = json.loads(ev.historical_json)
    if 'capture_manifest' in history:
        m = history['capture_manifest']
        b = m['capture']
        return View(L.Kind(b['kind']), L.Boundary(b['boundary']), b['lineage'],
                    b['registration']['decision_ms'], b['observed_ms'], b['observation'],
                    {d['role']: d for d in m['dependencies']}, m['sources'])
    wrapper = history['decision_manifest']
    reg = wrapper['registration']
    return View(L.Kind(wrapper['kind']), L.Boundary(wrapper['boundary']), reg['lineage'],
                reg['decision_ms'], wrapper['observed_ms'], json.loads(wrapper['observation_json']),
                {s['role']: dict(s, status='AVAILABLE') for s in wrapper['outcome_sources']},
                {k: v for k, v in history.items() if k != 'decision_manifest'})


def _kinds(rule):
    return tuple(sorted(k.value if isinstance(k, L.Kind) else str(k) for k in rule.kinds))


def dispatchable(rule):
    return (isinstance(rule, T.Rule) and rule.target in T.ADAPTIVE and bool(rule.kinds)
            and callable(rule.context) and callable(rule.sufficient))


def describe(rule):
    return dict(rule_id=rule.rule_id, target=rule.target.value, rule_version=rule.version,
                kinds=list(_kinds(rule)), required_sources=[list(x) for x in rule.required_sources],
                requirements=list(rule.requirements))


def production_registry():
    """The only registry the Kernel can reach. Currently empty by design."""
    return T.PRODUCTION_RULES


def registry_digest(rules):
    return L.digest(sorted((describe(r) for r in rules.values() if dispatchable(r)),
                           key=lambda d: (d['rule_id'], d['rule_version'])))


def ensure(journal):
    R.ensure(journal)
    with journal._tx() as c:
        c.executescript(f"""
        CREATE TABLE IF NOT EXISTS {TABLE}(
          dispatch_id TEXT PRIMARY KEY, outcome_id TEXT NOT NULL, registry_digest TEXT NOT NULL,
          payload TEXT NOT NULL, sha256 TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS learning_dispatch_outcome ON {TABLE}(outcome_id);
        CREATE INDEX IF NOT EXISTS learning_dispatch_outcome_registry_v1
          ON {TABLE}(outcome_id,registry_digest);
        CREATE TRIGGER IF NOT EXISTS learning_dispatch_no_update BEFORE UPDATE ON {TABLE}
          BEGIN SELECT RAISE(ABORT,'dispatch receipt immutable'); END;
        CREATE TRIGGER IF NOT EXISTS learning_dispatch_no_delete BEFORE DELETE ON {TABLE}
          BEGIN SELECT RAISE(ABORT,'dispatch receipt immutable'); END;
        """)
        ensure_chain_dispatch_meta(c)


def _no(rule_id, rule, reason):
    return dict(rule_id=rule_id, rule_version=getattr(rule, 'version', None),
                target=getattr(getattr(rule, 'target', None), 'value', None),
                result=Result.NO_PROPOSAL.value, reason=reason, proposal_id=None)


def screen(rule_id, rule, v):
    """Pure compatibility of ONE registered rule with ONE replayed evidence view.

    Returns (reason, context): reason is None when compatible. Binds the rule's
    declared evidence type, exact source versions, target context and
    sufficiency; nothing here can create or change state.
    """
    if not dispatchable(rule) or rule.rule_id != rule_id:
        return 'RULE_NOT_DISPATCHABLE', None
    if v.kind.value not in _kinds(rule):
        return 'EVIDENCE_TYPE_INCOMPATIBLE', None
    for role, version in rule.required_sources:
        row = v.roles.get(role)
        if not row or row.get('status') != 'AVAILABLE' or row.get('version') != version or role not in v.sources:
            return 'REQUIRED_SOURCE_VERSION_UNAVAILABLE:' + str(role), None
    try:
        context = rule.context(v)
        if not isinstance(context, T.Context):
            return 'TARGET_CONTEXT_UNAVAILABLE', None
        ok, why = rule.sufficient(v)
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return 'RULE_EVALUATION_REFUSED', None
    if ok is not True:
        return 'INSUFFICIENT:' + str(why), None
    return None, context


def _evaluate_rule(journal, cfg, ev, v, rule_id, rule, rules, prior):
    if dispatchable(rule) and (rule_id, rule.version) in prior:
        return _no(rule_id, rule, 'ALREADY_PROPOSED_FOR_EVIDENCE'), None
    reason, context = screen(rule_id, rule, v)
    if reason:
        return _no(rule_id, rule, reason), None
    try:
        state = T.read(journal, rule.target, context)
        proposal = T.propose(ev, rule.target, state, rule_id, rules)
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return _no(rule_id, rule, 'RULE_EVALUATION_REFUSED'), None
    request = A.request(ev, proposal, state, A.source_versions(cfg))
    return dict(rule_id=rule_id, rule_version=rule.version, target=rule.target.value,
                result=Result.PROPOSED.value, reason=proposal.status.value,
                proposal_id=proposal.proposal_id), (proposal, request)


def evaluate(journal, cfg, ev, rules, prior=frozenset()):
    """All decisions for one evidence object. Reads only; returns (rows, proposals)."""
    if not rules or not any(dispatchable(r) for r in rules.values()):
        return [dict(rule_id=None, rule_version=None, target=None, result=Result.NO_PROPOSAL.value,
                     reason='UNREGISTERED', proposal_id=None)], []
    if ev.quality != 'VERIFIED_REPLAY' or not L.verified_history(ev):
        return [dict(rule_id=None, rule_version=None, target=None, result=Result.NO_PROPOSAL.value,
                     reason='REPLAY_INCOMPLETE', proposal_id=None)], []
    v = view(ev)
    rows, proposals = [], []
    for rule_id, rule in sorted(rules.items()):
        row, made = _evaluate_rule(journal, cfg, ev, v, rule_id, rule, rules, prior)
        rows.append(row)
        if made:
            proposals.append(made)
    return rows, proposals


def _prior(journal, outcome_id):
    if not journal.query("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)):
        return frozenset()
    done = set()
    for row in journal.query(f'SELECT payload FROM {TABLE} WHERE outcome_id=?', (outcome_id,)):
        for r in json.loads(row['payload'])['results']:
            if r['result'] == Result.PROPOSED.value:
                done.add((r['rule_id'], r['rule_version']))
    return frozenset(done)


def dispatch_evidence(journal, cfg, ev, *, at_ms, registry=None, shadow=False):
    """Idempotent per (outcome, registry version). Returns the receipt body."""
    rules = production_registry() if registry is None else registry
    digest = registry_digest(rules)
    dispatch_id = L.digest([ev.outcome_id, digest])
    exists = bool(journal.query("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)))
    if exists:
        old = journal.query(f'SELECT * FROM {TABLE} WHERE dispatch_id=?', (dispatch_id,))
        if old:
            body = json.loads(old[0]['payload'])
            if L.digest(body) != old[0]['sha256']:
                raise ValueError('dispatch_receipt_corrupt')
            return dict(body, duplicate=True)
    rows, proposals = evaluate(journal, cfg, ev, rules, _prior(journal, ev.outcome_id))
    body = dict(schema=SCHEMA, dispatch_id=dispatch_id, outcome_id=ev.outcome_id,
                evidence_id=ev.evidence_id, registry_digest=digest, dispatched_at=at_ms,
                results=rows, real_order_submissions=0, mutated_target=False)
    if shadow:
        return dict(body, duplicate=False, written=False)
    ensure(journal)
    with journal._tx() as c:
        c.execute(f'INSERT INTO {TABLE} VALUES(?,?,?,?,?)',
                  (dispatch_id, ev.outcome_id, digest, L.canonical(body), L.digest(body)))
        for proposal, request in proposals:
            R.insert_proposal(c, ev, proposal, request)
    return dict(body, duplicate=False, written=True)


def _projected_pending(journal, digest, limit, after_rowid):
    from trader.core import journal_evidence as E

    # Reconcile and select in one writer snapshot. Producers using direct SQL
    # need no new trigger or callback: their committed chains are discovered
    # here before any watermark can advance. Source chains remain immutable.
    with journal._tx() as conn:
        conn.execute('BEGIN IMMEDIATE')
        ensure_chain_dispatch_meta(conn)
        # Rowids can change after an offline VACUUM. Retain hashes/eligibility
        # by immutable outcome identity and repair the physical locator below.
        conn.execute(f'DELETE FROM {CHAIN_META_TABLE} WHERE outcome_id NOT IN '
                     '(SELECT outcome_id FROM learning_produced_chains)')
        mismatched = conn.execute(f'SELECT 1 FROM {CHAIN_META_TABLE} m '
            'JOIN learning_produced_chains p ON p.outcome_id=m.outcome_id '
            'WHERE m.chain_rowid!=p.rowid LIMIT 1').fetchone()
        if mismatched:
            # A two-phase repair avoids unique-key collisions during swaps.
            conn.execute(f'UPDATE {CHAIN_META_TABLE} SET chain_rowid=-chain_rowid')
            conn.execute(f'UPDATE {CHAIN_META_TABLE} SET chain_rowid='
                         '(SELECT rowid FROM learning_produced_chains p '
                         f'WHERE p.outcome_id={CHAIN_META_TABLE}.outcome_id)')
        cursor = 0
        while True:
            rows = conn.execute(f'SELECT p.rowid,p.outcome_id '
                'FROM learning_produced_chains p '
                f'LEFT JOIN {CHAIN_META_TABLE} m ON m.outcome_id=p.outcome_id '
                'WHERE p.rowid>? AND m.outcome_id IS NULL ORDER BY p.rowid LIMIT 64',
                (cursor,)).fetchall()
            if not rows:
                break
            for row in rows:
                payload = conn.execute('SELECT payload FROM learning_produced_chains '
                                       'WHERE outcome_id=?', (row[1],)).fetchone()[0]
                metadata, _ = E.chain_dispatch_metadata(conn, *row, payload)
                conn.execute(f'INSERT INTO {CHAIN_META_TABLE} VALUES (?,?,?,?,?)', metadata)
            cursor = rows[-1][0]
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()
        clause = (f' AND NOT EXISTS(SELECT 1 FROM {TABLE} d '
                  'WHERE d.outcome_id=m.evidence_outcome_id AND d.registry_digest=?)' if exists else '')
        args = (() if after_rowid is None else (after_rowid,))
        if exists:
            args += (digest,)
        candidates = conn.execute(f'SELECT m.* FROM {CHAIN_META_TABLE} m '
            "WHERE m.replay_gate='REPLAY_COMPLETE'" +
            ('' if after_rowid is None else ' AND m.chain_rowid>?') + clause +
            ' ORDER BY m.outcome_id LIMIT ?', args + (limit,)).fetchall()
        result = []
        for meta in candidates:
            raw = conn.execute('SELECT rowid,outcome_id,payload FROM learning_produced_chains '
                               'WHERE outcome_id=?', (meta[0],)).fetchone()
            actual, logical = E.chain_dispatch_metadata(conn, *raw)
            if actual != tuple(meta):
                raise E.EvidenceError('learning_dispatch_metadata_mismatch')
            row = dict(outcome_id=meta[0], payload=logical)
            if after_rowid is not None:
                row['rowid'] = meta[1]
            result.append(row)
        return result


def pending(journal, digest, limit, after_rowid=None, *, use_projection=True):
    """Replay-complete chains with no receipt for ``digest``, lowest outcome first.

    Missing projection entries are verified once before selection; only the
    selected payloads are expanded on subsequent calls. ``use_projection=False``
    retains the read-only logical query for shadow dispatch. A numeric
    ``after_rowid`` restricts selection and includes rowids in returned rows.
    """
    if not journal.query("SELECT 1 FROM sqlite_master WHERE name='learning_produced_chains'"):
        return []
    if use_projection:
        return _projected_pending(journal, digest, limit, after_rowid)
    exists = bool(journal.query("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)))
    clause = (f" AND NOT EXISTS(SELECT 1 FROM {TABLE} d WHERE d.outcome_id=json_extract(p.payload,'$.evidence.outcome_id')"
              " AND d.registry_digest=?)"
              if exists else '')
    if after_rowid is None:
        return journal.query("SELECT p.outcome_id, p.payload FROM learning_produced_chains p "
                             "WHERE json_extract(p.payload,'$.replay_gate')='REPLAY_COMPLETE'" + clause +
                             ' ORDER BY p.outcome_id LIMIT ?', ((digest,) if exists else ()) + (limit,))
    return journal.query("SELECT p.rowid AS rowid, p.outcome_id, p.payload FROM learning_produced_chains p "
                         "WHERE p.rowid>? AND json_extract(p.payload,'$.replay_gate')='REPLAY_COMPLETE'" + clause +
                         ' ORDER BY p.outcome_id LIMIT ?',
                         (after_rowid,) + ((digest,) if exists else ()) + (limit,))


SWEEP_S = 6 * 3600.0


class Watermark:
    """In-memory cursor that stops dispatch rescanning settled history.

    Invariant: for the current registry digest every chain with ``rowid <= floor``
    is dispatched (a receipt exists) or can never be dispatched. Chains are
    immutable (``learning_produced_chains_update``/``_delete`` triggers guard the
    logical payload; ``compact_completed`` only changes representation) and
    rowids only grow, so a new chain always lands above ``floor``.

    The floor only advances to ``MAX(rowid)`` read *before* a query that returned
    fewer rows than the limit, after every returned row dispatched. A raised
    exception, a full batch, a digest change or a restart leave or reset it to
    the original full scan, so missed events recover on their own. A periodic
    sweep rescans from 0 and counts any pending row at or below the old floor as
    a miss (rowid renumbering, e.g. an offline VACUUM) and dispatches it anyway.
    """

    def __init__(self, sweep_s=SWEEP_S, clock=time.monotonic):
        self.sweep_s, self.clock = sweep_s, clock
        self.floor, self.digest = 0, None
        self.swept_at, self.sweeping, self.audit_floor = None, False, 0
        self.misses, self.sweeps = 0, 0

    @staticmethod
    def _top(journal):
        # Raw table: O(1) and no logical-view blob resolution.
        if not journal.query("SELECT 1 FROM sqlite_master WHERE name='learning_produced_chains'"):
            return 0
        return journal._conn().execute('SELECT MAX(rowid) FROM learning_produced_chains').fetchone()[0] or 0

    def step(self, journal, digest, limit, dispatch):
        now = self.clock()
        if self.digest != digest:
            self.digest, self.floor, self.swept_at, self.sweeping, self.audit_floor = digest, 0, None, False, 0
        if not self.sweeping and (self.swept_at is None or now - self.swept_at >= self.sweep_s):
            self.sweeping, self.audit_floor, self.floor = True, self.floor, 0
        top = self._top(journal)
        if not self.sweeping and top <= self.floor:
            return []                       # nothing newer than everything already settled
        rows = pending(journal, digest, limit, after_rowid=self.floor)
        out = []
        for row in rows:
            if self.sweeping and row['rowid'] <= self.audit_floor:
                self.misses += 1
                log.error('learning dispatch watermark miss: outcome %s rowid %s <= floor %s',
                          row['outcome_id'], row['rowid'], self.audit_floor)
            out.append(dispatch(row))       # an exception leaves the floor where it was
        if len(rows) < limit:
            self.floor = max(self.floor, top)
            if self.sweeping:
                self.sweeping, self.audit_floor, self.swept_at = False, 0, now
                self.sweeps += 1
        return out


def dispatch_pending(journal, cfg, *, at_ms, max_work=8, shadow=False, test_registry=None, watermark=None):
    """Bounded Kernel step: dispatch replay-complete chains not yet dispatched.

    ``watermark`` (a ``Watermark`` owned by one dispatching thread) makes this
    incremental; without it, or in shadow mode, every call is the full scan.
    """
    if type(max_work) is not int or not 1 <= max_work <= 64:
        raise ValueError('bounded_dispatch_work_required')
    rules = production_registry()
    if test_registry is not None:
        rules = T.registry(journal, test_registry)
    digest = registry_digest(rules)

    def one(row):
        ev = R.evidence_from_body(json.loads(row['payload'])['evidence'])
        return dispatch_evidence(journal, cfg, ev, at_ms=at_ms, registry=rules, shadow=shadow)
    if watermark is not None and not shadow:
        return watermark.step(journal, digest, max_work, one)
    return [one(row) for row in pending(journal, digest, max_work, use_projection=not shadow)]


def checkpoint_isolated(journal, cfg, registry, *, at_ms, max_work=8, shadow=False):
    """Isolated-test entry to the SAME checkpoint with an extra registry.

    ``targets.registry`` refuses anything but a Journal marked test-only,
    outside data/, with no live trades, so this cannot be used on production.
    """
    return R._checkpoint(journal, cfg, at_ms, max_work, shadow, T.registry(journal, registry))
