import json
import sqlite3
from dataclasses import replace, asdict
from contextlib import contextmanager

import pytest

from trader.learning import foundation as L
from trader.strategy import factory_handoff as F, rolling
from trader.strategy.compile import compile_spec
from tests.test_rolling import _spec, _frame, RISK


class MemoryJournal:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row

    def query(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params)]

    @contextmanager
    def _tx(self):
        with self.conn:
            yield self.conn


@pytest.fixture
def chain():
    spec = _spec(universe={'include': ['A']})
    compiled = compile_spec(spec)
    version = F._version_record(spec.id, compiled.spec.to_dict(), F._jsha(compiled.spec.to_dict()),
        None, {'kind': 'TEST_ONLY', 'id': 'fixture'}, {})
    risk = dict(RISK, real_funding=False)
    frame = _frame(3000, drift=-0.004, seed=7)
    cutoff = int(frame.ts.iloc[-1].timestamp() * 1000) + 3600000
    dead, evaluation = rolling.has_decayed(compiled, {'A': frame}, risk, '1h',
        recent_days=60, min_trades=3, floor_pf=.85)
    assert dead
    rows = json.loads(frame.to_json(orient='records', date_format='iso'))
    observation = dict(evaluation=evaluation, measurement='simulated_recent_window_not_realized_money')
    snapshots = dict(data=dict(frames={'A': rows}, cutoff_ms=cutoff),
        world=dict(world_id='world-old'), context=dict(context_id='context-old'), strategy=version,
        risk_config=dict(risk=risk, strategies=dict(decay_recent_days=60, decay_min_trades=3, decay_floor_pf=.85)),
        portfolio=dict(positions=[]), decision=dict(decision_id='d', cycle_id='c'),
        reasons=dict(reason='registered recent evaluation'), action=dict(action='evaluation'),
        outcome=dict(observation=observation, protocol=L.DECAY_RULE, evaluation=evaluation,
                     code_manifest=L.code_manifest()))
    sources = tuple(L.Source(role, role, 'old-v1', L.digest(value), cutoff if role in L.PRE_DECISION else cutoff+1)
                    for role, value in snapshots.items())
    retained = {(s.source_id, s.version): snapshots[s.role] for s in sources}
    lineage = L.Lineage('c', 'd', 'context-old', strategy_id=spec.id,
        version_id=version['version_id'], spec_hash=version['spec_hash'], world_id='world-old')
    o = L.Outcome(L.Kind.RESEARCH, L.Boundary.COUNTERFACTUAL, lineage, cutoff, cutoff+1,
                  sources, L.canonical(observation), 'SIMULATED / UNREALIZED')
    from trader.learning import decision_sources as D
    from trader.learning.capture import unavailable
    deps=[dict(unavailable(s.role,'EXACT_TEST_REGISTRATION'),status='AVAILABLE',source_id=s.source_id,version=s.version,sha256=s.sha256,available_ms=s.available_ms) for s in sources if s.role in L.PRE_DECISION]
    reg=dict(event_key='research:test',profile='RESEARCH',decision_ms=cutoff,lineage=json.loads(L.canonical(asdict(lineage))),dependencies=deps)
    wrapper=dict(manifest=D.make(reg),registration=reg,outcome_sources=[asdict(s) for s in sources],kind=o.kind.value,boundary=o.boundary.value,observed_ms=o.observed_ms,observation_json=o.observation_json,label=o.label)
    ref=L.Source('decision_manifest',wrapper['manifest']['manifest_id'],D.SCHEMA,L.digest(wrapper),cutoff)
    retained[(ref.source_id,ref.version)]=wrapper
    o=replace(o,sources=o.sources+(ref,))
    current = dict(version_id=version['version_id'], spec_hash=version['spec_hash'], state=F.APPROVED_FIRST_LIVE)
    return o, retained, current, version


def get_evidence(o, sources):
    return L.evidence(o, L.attribute(o), L.replay(o, sources))


def test_registered_proposal_deterministic_and_existing_authority_changes_future_state(chain):
    o, sources, current, version = chain
    e = get_evidence(o, sources)
    p = L.propose(e, L.Target.LIFECYCLE, current, rule=L.DECAY_RULE)
    assert p.status == L.Status.APPLICABLE, p.reason
    assert p == L.propose(get_evidence(o, sources), L.Target.LIFECYCLE, current, rule=L.DECAY_RULE)
    j = MemoryJournal()
    F.ensure(j)
    with j._tx() as c:
        F._insert_version(c, version, o.decision_ms-1)
        F._transition(c, version['version_id'], F.VALIDATED, 'TEST_ONLY', None, 'factory', o.decision_ms-1)
        F._transition(c, version['version_id'], F.SHADOW, 'TEST_ONLY', None, 'factory', o.decision_ms)
        F._transition(c, version['version_id'], F.APPROVAL_REQUIRED, 'TEST_ONLY', None, 'factory', o.decision_ms)
        F._transition(c, version['version_id'], F.APPROVED_FIRST_LIVE, 'TEST_ONLY', None, 'factory', o.decision_ms)
    before = F.load_version(j, version['version_id'])
    assert L.apply_to_isolated_journal(j, o, sources, p, at_ms=o.observed_ms) == 'APPLIED'
    assert F.state_of(j, version['version_id']) == F.RETIRED
    assert L.apply_to_isolated_journal(j, o, sources, p, at_ms=o.observed_ms) == 'DUPLICATE'
    assert F.load_version(j, version['version_id']) == before  # StrategySpec immutable
    with pytest.raises(F.HandoffRefused, match='transition_not_allowed'):
        F.govern_version(j, {}, version['version_id'], 'REACTIVATED', actor='strategy_governor', reason_code='retry', at_ms=o.observed_ms)
    assert F.RETIRED not in F.GOVERNOR_ALLOWED or not F.GOVERNOR_ALLOWED[F.RETIRED]


@pytest.mark.parametrize('kind', [L.Kind.REJECTED, L.Kind.MISSED, L.Kind.RISK_BLOCKED, L.Kind.CASH])
def test_rejected_future_outcome_stays_counterfactual(chain, kind):
    o, _, _, _ = chain
    rejected = replace(o, kind=kind)
    assert rejected.boundary == L.Boundary.COUNTERFACTUAL
    with pytest.raises(ValueError, match='not_realized_money'):
        replace(rejected, boundary=L.Boundary.REALIZED)
    with pytest.raises(ValueError):
        replace(rejected, observation_json=L.canonical({'net_pnl': 20}))
    with pytest.raises(ValueError):
        replace(rejected, observed_ms=rejected.decision_ms)


def test_exact_historical_versions_not_latest_and_missing_blocks(chain):
    o, sources, cur, _ = chain
    sources[('data', 'latest')] = {'tampered': True}
    assert L.replay(o, sources).status == 'COMPLETE'
    sources.pop(('data', 'old-v1'))
    r = L.replay(o, sources)
    assert r.status == 'INCOMPLETE'
    assert L.propose(get_evidence(o, sources), L.Target.LIFECYCLE, cur, rule=L.DECAY_RULE).status == L.Status.INCOMPLETE


def test_tampered_source_and_future_source_fail_closed(chain):
    o, sources, _, _ = chain
    sources[('portfolio', 'old-v1')] = {'positions': ['changed']}
    assert 'tampered:portfolio' in L.replay(o, sources).faults
    future = replace(o, sources=tuple(replace(s, available_ms=o.observed_ms) if s.role == 'data' else s for s in o.sources))
    assert 'future_source:data' in L.replay(future, sources).faults


def test_no_causal_blame_or_confidence_update_from_losing_trade(chain):
    o, sources, current, _ = chain
    trade = replace(o, kind=L.Kind.EXECUTED)
    a = L.attribute(trade)
    assert not a.causal_claims
    assert all(d[1] != L.Support.ESTABLISHED for d in a.dimensions)
    e = get_evidence(trade, sources)
    assert L.propose(e, L.Target.CONFIDENCE, .8).status == L.Status.INCOMPLETE
    assert L.propose(e, L.Target.LIFECYCLE, current, rule=L.DECAY_RULE).status == L.Status.INCOMPLETE
    assert not e.eligible_targets


def test_no_unregistered_update_or_risk_target_or_forged_application(chain):
    o, sources, cur, _ = chain
    e = get_evidence(o, sources)
    p = L.propose(e, L.Target.LIFECYCLE, cur, rule='loss_means_retire')
    assert p.status == L.Status.UNREGISTERED and p.proposed_json is None
    with pytest.raises(ValueError, match='unapproved_update_target'):
        L.propose(e, 'RISK_LIMITS', 1)
    with pytest.raises(ValueError):
        L.apply_to_isolated_journal(MemoryJournal(), o, sources, replace(p, status=L.Status.APPLICABLE), at_ms=o.observed_ms)


def test_stale_conflict_missing_sample_policy_and_code(chain):
    o, sources, cur, _ = chain
    e = get_evidence(o, sources)
    assert L.propose(e, L.Target.LIFECYCLE, dict(cur, spec_hash='changed'), rule=L.DECAY_RULE).status == L.Status.STALE
    assert L.propose(e, L.Target.LIFECYCLE, cur, rule=L.DECAY_RULE, conflicting=True).status == L.Status.CONFLICTING
    h = json.loads(e.historical_json)
    del h['risk_config']['strategies']['decay_min_trades']
    assert L.propose(replace(e, historical_json=L.canonical(h)), L.Target.LIFECYCLE, cur, rule=L.DECAY_RULE).status == L.Status.INCOMPLETE
    h = json.loads(e.historical_json)
    h['outcome']['code_manifest'] = {}
    assert L.propose(replace(e, historical_json=L.canonical(h)), L.Target.LIFECYCLE, cur, rule=L.DECAY_RULE).status == L.Status.INCOMPLETE


def test_immutable_retry_restart_artifacts(chain, tmp_path):
    o, sources, cur, _ = chain
    e = get_evidence(o, sources)
    p = L.propose(e, L.Target.LIFECYCLE, cur, rule=L.DECAY_RULE)
    for rec in (o, L.attribute(o), L.replay(o, sources), e, p):
        a = L.save_immutable(tmp_path, rec)
        assert a == L.save_immutable(tmp_path, rec)
    assert len(list(tmp_path.glob('*.json'))) == 5
    with pytest.raises(Exception):
        o.observed_ms = 1


def test_realized_requires_verified_execution_not_counterfactual(chain):
    o, sources, _, _ = chain
    actual = replace(o, kind=L.Kind.EXECUTED, boundary=L.Boundary.REALIZED, label='REALIZED')
    assert 'realized_accounting_unverified' in L.replay(actual, sources).faults


def test_exact_prior_inconclusive_research_recalled_and_tamper_refused(tmp_path, chain):
    from tests.test_strategy_decay_research_bank import _filed, _bank_row
    j, _ = _filed(tmp_path)
    row = _bank_row(j)
    bank_id = row['bank_object_id']
    o, sources, _, _ = chain
    e = replace(get_evidence(o, sources), provenance=((bank_id, 'bank-v1', row['canonical_sha256']),))
    result = L.exact_failed_research(j, bank_id, e)
    assert result['result_status'] == 'INCONCLUSIVE'
    assert result['authority'] == 'context_only'
    with j._tx() as c:
        c.execute("UPDATE research_bank_objects SET canonical_sha256='tampered'")
    with pytest.raises(ValueError):
        L.exact_failed_research(j, bank_id, e)


def test_file_backed_journal_application_refused(tmp_path, chain):
    from trader.core.journal import Journal
    o, sources, cur, _ = chain
    p = L.propose(get_evidence(o, sources), L.Target.LIFECYCLE, cur, rule=L.DECAY_RULE)
    j = Journal(tmp_path / 'isolated.db')
    with pytest.raises(ValueError, match='in_memory'):
        L.apply_to_isolated_journal(j, o, sources, p, at_ms=o.observed_ms)


def test_fresh_process_replay(chain, tmp_path):
    import subprocess
    import sys
    from dataclasses import asdict
    from scripts.learning_replay import run
    o, sources, cur, _ = chain
    raw = dict(outcome=asdict(o), retained=[dict(source_id=k[0], version=k[1], payload=v) for k, v in sources.items()],
        target=L.Target.LIFECYCLE, current=cur, rule=L.DECAY_RULE)
    bundle = tmp_path / 'bundle.json'
    bundle.write_text(L.canonical(raw))
    result = subprocess.check_output([sys.executable, 'scripts/learning_replay.py', str(bundle)], text=True)
    assert json.loads(result) == run(raw)


def test_structured_prior_context_recall_does_not_suppress(tmp_path):
    from tests.test_strategy_decay_research_recall import _timeline, _qid
    j = _timeline(tmp_path, other_spec=False)
    before = j.query('SELECT COUNT(*) AS n FROM research_runs')
    result = L.prior_failed_for_question(j, _qid(j, ms=5), max_objects=20)
    assert result['prior'] and result['prior'][0]['structured_context_equal']
    assert result['authority'] == 'context_only' and not result['suppression_authority']
    assert j.query('SELECT COUNT(*) AS n FROM research_runs') == before


def test_verified_realized_execution_stays_separate(chain):
    from trader.cognition import outcomes as O
    o, sources, _, _ = chain
    registration = dict(trade_id='t', symbol='S', venue='v', environment='demo', registered_ms=o.decision_ms)
    accounting = dict(schema_version='execution-accounting.v1', complete=True, trade_id='t', symbol='S', venue='v', environment='demo',
        reconciliation_version='receipt-v1', funding_complete=True, funding_net=-1, currency='USDT',
        resolved_ms=o.observed_ms, available_ms=o.observed_ms,
        fills=[dict(id='f', version='v1', trade_id='t', currency='USDT', event_ms=o.decision_ms,
                    available_ms=o.decision_ms, realized_pnl=10, commission=2)])
    receipt = O.verified_execution(registration, accounting, o.observed_ms)
    observed = {'net_pnl': 7, 'currency': 'USDT', 'environment': 'demo'}
    payload = dict(observation=observed, typed_receipt=receipt)
    sources[('outcome', 'old-v1')] = payload
    trade_payload = dict(trade_ids=['t'])
    sources[('trade', 'old-v1')] = trade_payload
    actual = replace(o, kind=L.Kind.EXECUTED, boundary=L.Boundary.REALIZED, label='REALIZED',
        lineage=replace(o.lineage, trade_ids=('t',)), observation_json=L.canonical(observed),
        sources=tuple(replace(s, sha256=L.digest(payload)) if s.role=='outcome' else s for s in o.sources) + (L.Source('trade', 'trade', 'old-v1', L.digest(trade_payload), o.observed_ms),))
    assert L.replay(actual, sources).status == 'INCOMPLETE'  # original research manifest cannot become a trade
    e = get_evidence(actual, sources)
    assert not e.eligible_targets
    assert L.propose(e, L.Target.CONFIDENCE, .8).status == L.Status.INCOMPLETE


def test_learning_shadow_is_read_only_and_idempotent(tmp_path):
    from trader.core.journal import Journal
    from scripts.learning_shadow import run
    source = tmp_path / 'source.db'
    j = Journal(source)
    with j._tx() as c:
        c.execute("INSERT INTO cycles(id,ts,symbol) VALUES('c','2026-01-01T00:00:00+00:00','S')")
        c.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,executed) VALUES('d','c','2026-01-01T00:00:00+00:00','S','BUY',0,0,0,0)")
        c.execute("INSERT INTO outcomes(decision_id,cycle_id,symbol,ts,action,entry_price,resolved_at,fwd_ret_1h) VALUES('d','c','S','2026-01-01T00:00:00+00:00','BUY',100,'2026-01-01T01:00:00+00:00',.01)")
    before = j.query('SELECT * FROM decisions'), j.query('SELECT * FROM outcomes'), j.query('SELECT * FROM state_kv')
    one = run(source, tmp_path / 'shadow', 1767229200000, 10)
    two = run(source, tmp_path / 'shadow', 1767229200000, 10)
    assert one == two
    later = run(source, tmp_path / 'shadow', 1767229201000, 10)
    assert later['events_sha256'] == one['events_sha256']
    assert one['counts']['counterfactual'] == 1
    assert one['counts']['realized'] == one['counts']['applicable_updates'] == 0
    assert one['counts']['replay_incomplete'] == 1
    assert before == (j.query('SELECT * FROM decisions'), j.query('SELECT * FROM outcomes'), j.query('SELECT * FROM state_kv'))


def test_detached_learning_has_no_runtime_caller_or_order_control_mutation():
    from pathlib import Path
    import ast
    root = Path('trader')
    for path in root.rglob('*.py'):
        if 'learning' not in path.parts:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if (node.module or '').startswith('trader.learning'):
                        assert node.module in ('trader.learning', 'trader.learning.capture_runtime', 'trader.learning.producers'), path
                        assert {a.name for a in node.names} <= {'capture','capture_runtime','runtime_inputs','producers'}, path
                if isinstance(node, ast.Import):
                    assert all(not alias.name.startswith('trader.learning') for alias in node.names), path
    tree = ast.parse(Path('trader/learning/foundation.py').read_text())
    banned = {'create_order', 'set_control', 'upsert_spec', 'log_decision', 'execute', 'executemany'}
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in banned for node in ast.walk(tree))
    # Prospective producers may capture evidence, never invoke update authority.
    for path in root.rglob('*.py'):
        if 'learning' not in path.parts:
            assert 'LearningUpdateProposal' not in path.read_text()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and (node.module or '').endswith('learning.foundation'):
                    # Runtime consumers may refuse legacy writes, never apply proposals.
                    assert {alias.name for alias in node.names} == {'refuse_legacy_learning'}, path


def test_lifecycle_proposal_cannot_carry_risk_changes(chain):
    o, sources, current, _ = chain
    ev = get_evidence(o, sources)
    p = L.propose(ev, L.Target.LIFECYCLE, dict(current, risk_limits={'max_leverage': 100}), rule=L.DECAY_RULE)
    assert p.status == L.Status.STALE and p.proposed_json is None
