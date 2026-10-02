"""Bounded deterministic queue/checkpoint. No venue, LLM or policy invention."""
from dataclasses import asdict
import json
from . import foundation as L, application as A
from trader.strategy import factory_handoff as F

TABLE = 'learning_runtime_proposals'


def ensure(journal):
    A.ensure(journal)
    with journal._tx() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS learning_runtime_proposals(
          proposal_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS runtime_proposal_no_update BEFORE UPDATE ON learning_runtime_proposals
          BEGIN SELECT RAISE(ABORT,'runtime proposal immutable'); END;
        CREATE TRIGGER IF NOT EXISTS runtime_proposal_no_delete BEFORE DELETE ON learning_runtime_proposals
          BEGIN SELECT RAISE(ABORT,'runtime proposal immutable'); END;
        """)


def enqueue(journal, evidence, proposal, req):
    ensure(journal)
    body = dict(evidence=asdict(evidence), proposal=asdict(proposal), request=asdict(req))
    with journal._tx() as c:
        old = c.execute(f'SELECT payload FROM {TABLE} WHERE proposal_id=?', (proposal.proposal_id,)).fetchone()
        if old:
            if old[0] != L.canonical(body): raise ValueError('proposal_queue_conflict')
            return proposal.proposal_id
        c.execute(f'INSERT INTO {TABLE} VALUES(?,?,?)', (proposal.proposal_id,L.canonical(body),L.digest(body)))
    return proposal.proposal_id


def decode(row):
    body = json.loads(row['payload'])
    if L.digest(body) != row['sha256']: raise ValueError('proposal_queue_corrupt')
    e = body['evidence']
    for key in ('supported','unknown','provenance','eligible_targets'):
        e[key] = tuple(tuple(v) if isinstance(v,list) else v for v in e[key])
    ev = L.LearningEvidence(**e)
    p = body['proposal']; p['target']=L.Target(p['target']);p['status']=L.Status(p['status']);p['basis']=tuple(p['basis'])
    proposal = L.LearningUpdateProposal(**p)
    if proposal.proposal_id != row['proposal_id']: raise ValueError('proposal_identity_corrupt')
    return ev, proposal, A.Request(**body['request'])


def checkpoint(journal, cfg, *, at_ms, max_work=8, shadow=False):
    if type(max_work) is not int or not 1 <= max_work <= 64:
        raise ValueError('bounded_application_work_required')
    if not journal.query("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)):
        return []
    if not shadow: A.ensure(journal)
    receipts = journal.query("SELECT 1 FROM sqlite_master WHERE name=?", (A.TABLE,))
    from . import targets as T
    registered = {r.rule_id:r.target for r in L.REGISTERED_RULES.values()}
    registered.update({r.rule_id:r.target for r in T.PRODUCTION_RULES.values()})
    pairs = sorted(registered.items())
    clauses = " OR ".join("(json_extract(q.payload,'$.proposal.rule')=? AND json_extract(q.payload,'$.proposal.target')=?)" for _ in pairs)
    if not clauses: return []
    query = f'SELECT q.* FROM {TABLE} q WHERE ('+clauses+')'
    params = tuple(x for rid,target in pairs for x in (rid,target.value))
    if receipts:
        query += f" AND NOT EXISTS(SELECT 1 FROM {A.TABLE} a WHERE a.proposal_id=q.proposal_id)"
    rows = journal.query(query+' ORDER BY q.proposal_id LIMIT ?', params+(max_work,))
    result = []
    for row in rows:
        ev, p, req = decode(row)
        # Only registered production rules are even dispatched normally.
        if registered.get(p.rule) != p.target:
            continue
        result.append(A.apply(journal,cfg,ev,p,req,at_ms=at_ms,shadow=shadow))
    return result


def recent_decay(journal, cfg, version_id, frames, *, cutoff_ms, observed_ms=None):
    """Prospective registered producer for already-existing decay evaluation.

    It measures the exact immutable installed spec, complete retained frames,
    existing config and evaluator bytes. Unsupported replay refuses normally.
    """
    import pandas as pd
    from trader.strategy import rolling
    from trader.strategy.compile import compile_spec
    from trader.strategy.spec import StrategySpec
    from . import decision_sources as D, capture as C
    if type(cutoff_ms) is not int:
        raise ValueError('decay_capture_clock_required')
    from copy import deepcopy
    cfg = deepcopy(cfg)
    frames = {symbol:frame.copy(deep=True) for symbol,frame in frames.items() if frame is not None}
    target = F.lifecycle_target(journal,version_id)
    version = F.load_version(journal,version_id)
    compiled = compile_spec(StrategySpec.from_dict(version['spec']))
    policy = cfg['strategies']
    from trader.strategy.spec_evidence import risk_for
    dead, evaluation = rolling.has_decayed(compiled,frames,risk_for(cfg['risk'],compiled.spec.timeframe),compiled.spec.timeframe,
        recent_days=policy['decay_recent_days'],min_trades=policy['decay_min_trades'],
        floor_pf=policy['decay_floor_pf'])
    if observed_ms is None:
        import time
        observed_ms = int(time.time()*1000)
    if type(observed_ms) is not int or observed_ms <= cutoff_ms:
        raise ValueError('decay_capture_clock_required')
    # The same serialisation is used for measurement and replay. Float JSON
    # rounding must not quietly change an evaluator input.
    def records(frame):
        raw=frame.copy()
        raw['ts'] = pd.to_datetime(raw['ts'],utc=True).map(lambda v:v.isoformat())
        return raw.to_dict(orient='records')
    data = dict(frames={symbol:records(frame) for symbol,frame in frames.items() if frame is not None},
                cutoff_ms=cutoff_ms)
    observation = dict(evaluation=evaluation,measurement='simulated_recent_window_not_realized_money')
    snapshots = dict(data=data,strategy=version,risk_config=cfg,
        action=dict(action='REGISTERED_RECENT_DECAY_EVALUATION',version_id=version_id),
        outcome=dict(protocol=L.DECAY_RULE,evaluation=evaluation,observation=observation,code_manifest=L.code_manifest()))
    sources = tuple(L.Source(role,L.digest(raw),'recent-decay-source.v1',L.digest(raw),
        cutoff_ms if role in L.PRE_DECISION else observed_ms) for role,raw in snapshots.items())
    lineage=L.Lineage(None,None,None,strategy_id=version['strategy_id'],
        version_id=version_id,spec_hash=version['spec_hash'])
    deps=[dict(C.unavailable(s.role,'REGISTERED_DECAY_INPUT'),status='AVAILABLE',
        source_id=s.source_id,version=s.version,sha256=s.sha256,available_ms=s.available_ms)
        for s in sources if s.role in L.PRE_DECISION]
    reg=dict(event_key='decay:'+L.digest(dict(version=version_id,data=data,config=cfg)),
        profile='DECAY_EVALUATION',decision_ms=cutoff_ms,lineage=json.loads(L.canonical(asdict(lineage))),dependencies=deps)
    wrapper=dict(manifest=D.make(reg),registration=reg,outcome_sources=[asdict(s) for s in sources],
        kind=L.Kind.RESEARCH.value,boundary=L.Boundary.COUNTERFACTUAL.value,observed_ms=observed_ms,
        observation_json=L.canonical(observation),label='SIMULATED / UNREALIZED')
    source=L.Source('decision_manifest',wrapper['manifest']['manifest_id'],D.SCHEMA,L.digest(wrapper),cutoff_ms)
    retained={(s.source_id,s.version):snapshots[s.role] for s in sources}
    retained[(source.source_id,source.version)]=wrapper
    outcome=L.Outcome(L.Kind.RESEARCH,L.Boundary.COUNTERFACTUAL,lineage,cutoff_ms,observed_ms,
                      sources+(source,),L.canonical(observation),'SIMULATED / UNREALIZED')
    ev=L.evidence(outcome,L.attribute(outcome),L.replay(outcome,retained))
    current={k:v for k,v in target.items() if k!='history_sha256'}
    proposal=L.propose(ev,L.Target.LIFECYCLE,current,rule=L.DECAY_RULE)
    req=A.request(ev,proposal,target,A.source_versions(cfg))
    enqueue(journal,ev,proposal,req)
    return ev,proposal,req
