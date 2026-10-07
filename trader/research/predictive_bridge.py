"""Normal opt-in asynchronous Research Bank consumer, independent of Kernel.

python -m trader.research.predictive_bridge --enable --once --source ...
  --bank ... --candles ... --config ... --max-sources 4 --max-experiments 1

Without --once it consumes at a bounded cadence. No service is installed.
Sources are read-only. One flock protects restart-safe checkpoints and the
shared, cumulative referee error budget across isolated experiment ledgers.
"""
import argparse
from contextlib import closing
from dataclasses import asdict
import fcntl
import hashlib
import json
from pathlib import Path
import signal
import sqlite3
import time

from trader.cognition import predictive as P
from trader.observability import investigation_research as R
from trader.core.journal import Journal
from trader.core.child import run_child
from . import predictive_experiment as E
from .runner import ResearchRunner
from . import predictive_receipt as M

MAX_SOURCES, MAX_EXPERIMENTS, MAX_RECORDS = 16, 4, 4096
TABLES = ('bridge_sources', 'bridge_hypotheses', 'bridge_experiments',
          'bridge_bank_results', 'bridge_questions', 'bridge_tests', 'bridge_snapshots',
          'bridge_runtime_bindings', 'bridge_measurements', 'bridge_registration_checks', 'bridge_translations')


def store(path):
    """Append-only Bank extensions; never initialize an existing foreign DB."""
    db = sqlite3.connect(path, timeout=1)
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if names and 'bridge_sources' not in names:
        db.close()
        raise ValueError('foreign_bridge_store')
    db.execute('PRAGMA max_page_count=65536')
    for table in TABLES:
        db.execute(f'CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        for operation in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable'); END")
    db.commit()
    from trader.learning import capture as C
    C.ensure(db)
    return db


def append(db, table, key, body):
    text = P.canonical(body)
    old = db.execute(f'SELECT payload FROM {table} WHERE id=?', (key,)).fetchone()
    if old:
        if old[0] != text:
            raise ValueError('checkpoint_conflict')
        return False
    if db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] >= MAX_RECORDS:
        raise ValueError('checkpoint_capacity')
    db.execute(f'INSERT INTO {table} VALUES (?,?)', (key, text))
    return True


def records(db, table):
    return [json.loads(p) for (p,) in db.execute(f'SELECT payload FROM {table} ORDER BY rowid')]


def read_sources(path, known, limit, now_ms):
    """Exact persisted Bank records only; a made-up chain is never an input."""
    sources, refusals = [], []
    with closing(E.readonly(path)) as db:
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='investigation_research_records'").fetchone():
            return [], [dict(reason='no_retained_investigation_bank')]
        rows = db.execute('SELECT record_id,investigation_id,recorded_at_ms,CASE WHEN length(canonical_json)<=2097152 THEN canonical_json ELSE NULL END,canonical_sha256 '
            'FROM investigation_research_records WHERE record_type=? ORDER BY recorded_at_ms,record_id',
            (R.BANK_SCHEMA,))
        selected = []
        for row in rows:
            if row[0] not in known:
                selected.append(row)
            if len(selected) >= limit:
                break
        for bid, iid, recorded, raw, sha in selected:
            try:
                if raw is None:
                    raise ValueError('source_payload_bound')
                if recorded > now_ms or type(recorded) is not int:
                    raise ValueError('source_not_available')
                if R.sha256(raw) != sha:
                    raise ValueError('source_row_integrity')
                chain = R.chain(path, iid)
                matches = [r for r in chain['runs'] if r['bank']['bank_object_id'] == bid] if chain else []
                if len(matches) != 1 or P.canonical(matches[0]['bank']) != raw:
                    raise ValueError('source_chain_missing_or_changed')
                source = P.source_from_chain(chain, matches[0], recorded)
                # Existing exact prior-research recall was frozen at investigation
                # registration. Do not look up latest analogous outcomes here.
                prior = db.execute('SELECT payload FROM memory_contexts WHERE case_id=?', (iid,)).fetchone()
                if prior:
                    from trader.observability import memory as M
                    M.replay_export(M.export_case(db, iid))
                    fields = asdict(source)
                    fields['prior_research_json'] = P.canonical(json.loads(prior[0]))
                    fields.pop('provenance_hash')
                    source = P.ResearchSource(**fields, provenance_hash=P.digest(fields))
                if source.available_ms > now_ms:
                    raise ValueError('source_not_available')
                sources.append(source)
            except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                refusals.append(dict(bank_id=bid, reason=str(exc)[:120]))
    return sources, refusals


def prior_classification(db, h):
    prior = [r for r in records(db, 'bridge_hypotheses')
             if r['hypothesis']['transformation'] == h.transformation]
    exact = next((r for r in prior if r['hypothesis']['semantic_hash'] == h.semantic_hash
                  and r['hypothesis']['source_hash'] == h.source_hash), None)
    if exact:
        return 'EXACT_DUPLICATE', [exact['hypothesis']['hypothesis_id']]
    outcomes = records(db, 'bridge_bank_results')
    contradicted = [r['hypothesis']['hypothesis_id'] for r in prior if any(
        o['hypothesis_id'] == r['hypothesis']['hypothesis_id'] and o['classification'] == 'UNSUPPORTED'
        for o in outcomes)]
    if contradicted:
        return 'CONTRADICTED_BY_PRIOR', contradicted
    return ('RELATED_PRIOR', [r['hypothesis']['hypothesis_id'] for r in prior]) if prior else ('NEW', [])


def feedback(db, h, experiment_id, classification, measured, referee, now_ms):
    if classification not in P.CLASSIFICATIONS:
        raise ValueError('feedback_classification')
    body = M.build(db,h,experiment_id,classification,measured,referee)
    key = body['result_id']
    # An unchanged pending result is idempotent, regardless of wall clock.
    if append(db, 'bridge_bank_results', key, body):
        source = P.source_from_dict(json.loads(h.provenance_json)['source'])
        for q in body['next_questions']:
            append(db, 'bridge_questions', q['question_id'], q)
        # Reuse Stage7 RESEARCH profile. No learned priority enters any truth gate.
        from trader.learning import capture as C, capture_runtime as Lr, foundation as L
        event = 'predictive-research:'+key
        question=dict(schema='predictive-research-question.v1', question=h.mechanism_description,
                      source_bank_id=h.bank_id, hypothesis_id=h.hypothesis_id)
        ex=db.execute('SELECT payload FROM bridge_experiments WHERE id=?',(experiment_id,)).fetchone()
        plan=json.loads(ex[0]) if ex else dict(status=classification,experiment_id=None)
        chain=dict(question=question,plan=plan,evidence=asdict(h),result=body,
                   receipt=dict(measured=body['measured_result'],referee=body['referee_disposition']))
        deps = [Lr.freeze(db, role, value, now_ms, 'predictive research bridge') for role, value in (
            ('question', question),
            ('plan', plan),
            ('research_evidence', asdict(h)))]
        C.register(db, event, L.Kind.RESEARCH.value, asdict(L.Lineage(None,None,None)), now_ms, deps, profile='RESEARCH')
        observation = dict(classification=classification, hypothesis_id=h.hypothesis_id,
                           experiment_id=experiment_id, authority='RESEARCH_ONLY')
        deps = [Lr.freeze(db, role, value, now_ms, 'predictive research feedback') for role, value in (
            ('research_run', dict(chain=chain)), ('falsifier_result', body),
            ('bank', body), ('outcome', dict(observation=observation)))]
        C.attach(db, event, event, L.Kind.RESEARCH.value, L.Boundary.REALIZED.value, observation, now_ms, deps)
    return body


def replay_feedback(chain, bank):
    """Stage7 replays existing receipts, never assigns new statistical gates."""
    h=P.hypothesis_from_dict(chain['evidence'])
    if (bank['schema'] not in ('predictive-research-bank-result.v1',M.SCHEMA) or bank['bank_kind']!='predictive_experiment'
            or bank['bank_id']!=h.bank_id or bank['hypothesis_id']!=h.hypothesis_id
            or bank['classification'] not in P.CLASSIFICATIONS or bank['authority']!='RESEARCH_ONLY'
            or chain['result']!=bank or chain['question']['hypothesis_id']!=h.hypothesis_id
            or chain['receipt']!=dict(measured=bank['measured_result'],referee=bank['referee_disposition'])):
        raise ValueError('predictive_feedback_binding')
    if bank['experiment_id']:
        E.validate(chain['plan'])
        if chain['plan']['experiment_id']!=bank['experiment_id'] or chain['plan']['source_hypothesis_id']!=h.hypothesis_id:
            raise ValueError('predictive_feedback_experiment')
    if bank['schema']==M.SCHEMA:
        M.validate(h,chain['plan'] if bank['experiment_id'] else None,bank)
    supported=bank['classification']=='SUPPORTED'
    if bank['predictive_strategy_validation']!=supported or bank['stage']!=(P.SUPPORTED_RESULT if supported else 'MEASURED_RESULT'):
        raise ValueError('predictive_feedback_stage')
    if supported:
        from . import referee as registered_referee
        from .combo import Combination
        ev=bank['referee_disposition']['evidence']
        g1,look=ev['gate1'],ev['gate1_look']
        c=Combination.from_dict(ev['evaluated']['combo'])
        measured=bank['measured_result']
        actual=registered_referee.gate1(g1['a'],g1['b'],g1['rotation'])
        if (ev['candidate']['state']!='referee_passed' or ev['candidate']['hash']!=c.hash
                or c.evaluation_scope!=bank['experiment_id'] or c.trigger!=h.hypothesis_id
                or c.keys!=tuple(sorted(h.predictor_definition)) or c.geo!='fixed'
                or measured['hash']!=c.hash or measured['predictive_combo']!=c.as_dict()
                or actual['p']!=look['p'] or look['p']!=g1['p'] or not look['rejected']
                or not look['p']<=look['alpha_t'] or ev['gate3']['passed'] is not True):
            raise ValueError('predictive_feedback_referee_receipt')
    return bank


def classify(runner, h, experiment):
    from .combo import Combination
    try:
        c = Combination(P.parts(h, runner.ledger.gauges('4h')), '4h', 'fixed',
                        trigger=h.hypothesis_id, round='predictive', evaluation_scope=experiment['experiment_id'])
    except ValueError:
        return 'DATA_INSUFFICIENT', None, dict(state='NOT_SUBMITTED')
    result = runner.ledger.result(c.hash)
    if result and (result.get('outcome')=='UNTESTED' or result.get('verdict') in ('untested','untestable','empty') or not result.get('testable')):
        return 'DATA_INSUFFICIENT', result, dict(state='POWER_UNAVAILABLE')
    cand = runner.ledger.candidate(c.hash)
    if cand:
        disposition = dict(cand)
        if cand['state'] == 'referee_passed':
            from trader.strategy.factory_handoff import gate_evidence
            ev = gate_evidence(runner.journal, c.hash)
            return 'SUPPORTED', result, dict(state='referee_passed', evidence=ev,
                factory_candidate=dict(stage=P.STRATEGY_CANDIDATE, source_kind='research_candidate',
                    hash=c.hash, experiment_id=experiment['experiment_id'], hypothesis_id=h.hypothesis_id,
                    authority='CANDIDATE_ONLY', quantitative_ledger=str(runner.journal.db_path)))
        if cand['state'] in ('gate1_fail', 'gate3_fail'):
            g1 = json.loads(cand.get('gate1') or '{}')
            status = 'DATA_INSUFFICIENT' if str(g1.get('reason','')).startswith('untestable:') else 'UNSUPPORTED'
            if 'failed' in (cand.get('reason') or ''):
                status = 'INCONCLUSIVE'
            return status, result, disposition
        return 'INCONCLUSIVE', result, disposition
    if result:
        control = runner.ledger.control('4h', c.window)
        if not control or not control['powered']:
            return 'INCONCLUSIVE', result, dict(state='CONTROL_UNDERPOWERED')
        if result.get('verdict') in ('untestable', 'empty') or not result.get('testable'):
            return 'DATA_INSUFFICIENT', result, dict(state='POWER_UNAVAILABLE')
        if result.get('verdict') == 'error':
            return 'INCONCLUSIVE', result, dict(state='EVALUATION_ERROR')
        # Discovery never validates a prediction. Pruned/grow-only expressions
        # remain inconclusive at the protected evidence boundary.
        rows = runner.journal.query('SELECT verdict FROM research_combos WHERE hash=?', (c.hash,))
        if rows and rows[0]['verdict']=='survivor':
            return 'INCONCLUSIVE', result, dict(state='AWAITING_REFEREE')
        return 'INCONCLUSIVE', result, dict(state='DISCOVERY_ONLY')
    return 'INCONCLUSIVE', None, dict(state='PENDING_QUANTITATIVE_PIPELINE')


def cycle(source_path, bank_path, candles_path, cfg, *, now_ms, max_sources=4,
          max_experiments=1, run=run_child, submit=True, quantitative_source_path=None):
    if type(max_sources) is not int or not 1 <= max_sources <= MAX_SOURCES:
        raise ValueError('source_budget')
    if type(max_experiments) is not int or not 1 <= max_experiments <= MAX_EXPERIMENTS:
        raise ValueError('experiment_budget')
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError('cycle_clock')
    bank_path = Path(bank_path).resolve()
    quantitative_source_path=Path(quantitative_source_path or Path(source_path).parent/'luffy.db').resolve()
    for source in (source_path, candles_path, quantitative_source_path):
        p = Path(source).resolve()
        if bank_path == p or (bank_path.exists() and p.exists() and bank_path.samefile(p)):
            raise ValueError('source_destination_alias')
    bank_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(bank_path)+'.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(store(bank_path)) as db:
            binding=dict(schema='bridge-runtime-binding.v1',source=str(Path(source_path).resolve()),
                         quantitative_source=str(quantitative_source_path))
            with closing(E.readonly(quantitative_source_path)) as quantitative:
                quantitative.row_factory=sqlite3.Row
                base_tests=[dict(r) for r in quantitative.execute('SELECT * FROM research_tests ORDER BY seq')]
                base_budget=[dict(r) for r in quantitative.execute('SELECT * FROM research_budget ORDER BY id')]
            with db:
                append(db,'bridge_runtime_bindings','source',binding)
                append(db,'bridge_runtime_bindings','initial-protected-budget',dict(tests=base_tests))
                if base_budget:
                    append(db,'bridge_runtime_bindings','registered-error-budget',base_budget)
                for test in base_tests:
                    append(db,'bridge_tests',str(test['seq']),test)
            # Recover spent looks before offering ANY new experiment. A crash
            # between the quantitative commit and feedback must not reset LORD.
            for ex in records(db, 'bridge_experiments'):
                work = bank_path.parent/(bank_path.stem+'-experiments')/ex['experiment_id']
                path = work/'quantitative.db'
                if path.exists():
                    with closing(E.readonly(path)) as journal:
                        journal.row_factory=sqlite3.Row
                        tests=journal.execute('SELECT * FROM research_tests ORDER BY seq').fetchall()
                        budget=[dict(r) for r in journal.execute('SELECT * FROM research_budget ORDER BY id')]
                    with db:
                        if budget:
                            append(db,'bridge_runtime_bindings','registered-error-budget',budget)
                        for test in tests:
                            append(db,'bridge_tests',str(test['seq']),dict(test))
            counts = dict(eligible_research_results=0, hypotheses_proposed=0, exact_duplicates=0,
                experiment_shape_supported=0, data_insufficient=0, planner_submissions=0,
                supported=0, unsupported=0, inconclusive=0, not_testable=0)
            known = {s['bank_id'] for s in records(db, 'bridge_sources')}
            sources, refusals = read_sources(source_path, known, max_sources, now_ms)
            for source in sources:
                counts['eligible_research_results'] += 1
                with db:
                    append(db, 'bridge_sources', source.bank_id, asdict(source))
                    for q in P.next_questions(source):
                        append(db, 'bridge_questions', q['question_id'], q)
                    for transformation in P.TRANSFORMS:
                        h = P.propose(source, transformation, now_ms)
                        status, related = prior_classification(db, h)
                        check=dict(source_bank_id=source.bank_id,transformation=transformation,
                            proposal_id=h.hypothesis_id,classification=status,related_prior=related)
                        append(db,'bridge_registration_checks',P.digest([source.bank_id,transformation]),check)
                        if status == 'EXACT_DUPLICATE':
                            counts['exact_duplicates'] += 1
                            continue
                        append(db, 'bridge_hypotheses', h.hypothesis_id,
                               dict(hypothesis=asdict(h), novelty=status, related_prior=related))
                        counts['hypotheses_proposed'] += 1
            offered = 0
            translated = 0
            for saved in records(db, 'bridge_hypotheses'):
                h = P.hypothesis_from_dict(saved['hypothesis'])
                finished = [o for o in records(db,'bridge_bank_results') if o['hypothesis_id']==h.hypothesis_id
                            and (o['classification'] in ('SUPPORTED','UNSUPPORTED','NOT_TESTABLE')
                                 or o['referee_disposition'].get('state') in ('DISCOVERY_ONLY','CONTROL_UNDERPOWERED','POWER_UNAVAILABLE','EVALUATION_ERROR','CONTROL_DATA_INSUFFICIENT','PROTECTED_CUT_INCOMPATIBLE'))]
                if finished:
                    continue
                if P.TRANSFORMS[h.transformation]['shape']=='combination':
                    if translated>=max_experiments:
                        continue
                    translated+=1
                existing = [e for e in records(db,'bridge_experiments') if e['source_hypothesis_id']==h.hypothesis_id]
                ex = existing[0] if existing else E.translate(h, candles_path, cfg, now_ms)
                with db:
                    translation=E.retain_translation(h,ex,now_ms if not existing else ex['split']['end_ms']+14_400_000)
                    append(db,'bridge_translations',translation['translation_id'],translation)
                if existing and ex['minimum_evidence']['registered_policy_hash'] != P.digest(cfg.get('research') or {}):
                    raise ValueError('stale_or_incompatible_policy')
                if ex['status'] == 'UNSUPPORTED_EXPERIMENT_SHAPE':
                    counts['not_testable'] += 1
                    with db:
                        feedback(db,h,None,'NOT_TESTABLE',None,dict(state='UNSUPPORTED_EXPERIMENT_SHAPE'),now_ms)
                    continue
                counts['experiment_shape_supported'] += 1
                if ex['status'] == 'DATA_INSUFFICIENT':
                    counts['data_insufficient'] += 1
                    with db:
                        feedback(db,h,None,'DATA_INSUFFICIENT',None,dict(state=ex['reason']),now_ms)
                    continue
                if not submit or offered >= max_experiments:
                    continue
                offered += 1
                work = bank_path.parent/(bank_path.stem+'-experiments')/ex['experiment_id']
                work.mkdir(parents=True,exist_ok=True)
                candle_copy = work/'candles.db'
                numerical_path = work/'quantitative.db'
                if work.is_symlink() or candle_copy.is_symlink() or numerical_path.is_symlink():
                    raise ValueError('experiment_destination_alias')
                if numerical_path.exists():
                    with closing(E.readonly(numerical_path)) as check:
                        if not check.execute("SELECT 1 FROM sqlite_master WHERE name='predictive_experiment_binding'").fetchone():
                            raise ValueError('foreign_quantitative_store')
                        if check.execute('SELECT id FROM predictive_experiment_binding').fetchall()!=[(ex['experiment_id'],)]:
                            raise ValueError('quantitative_store_binding')
                else:
                    with sqlite3.connect(numerical_path) as check:
                        check.execute('CREATE TABLE predictive_experiment_binding(id TEXT PRIMARY KEY)')
                        check.execute('INSERT INTO predictive_experiment_binding VALUES (?)',(ex['experiment_id'],))
                if ex.get('evaluation_version') and ex['evaluation_version'] != E.evaluation_version():
                    raise ValueError('stale_evaluation_version')
                E.freeze_candles(candles_path,candle_copy,ex)
                snapshot = dict(experiment_id=ex['experiment_id'], candle_sha256=hashlib.sha256(candle_copy.read_bytes()).hexdigest())
                with db:
                    append(db,'bridge_snapshots',ex['experiment_id'],snapshot)
                    append(db,'bridge_experiments',ex['experiment_id'],ex)
                local_cfg = json.loads(ex['evaluation_config_json'])
                local_cfg.setdefault('research',{}).update(enabled=True, horizons=['4h'],geometries=['fixed'],
                    handoff=False,predictive_split=ex['split'],paths=dict(candles=str(candle_copy),derivs=str(work/'unused-derivs.db')))
                measurement=[]
                def measured_run(fn,payload,**kwargs):
                    res=run(fn,payload,**kwargs)
                    receipt=M.step(ex,fn,payload,res)
                    try:
                        M.validate_step(ex,receipt)
                    except (ValueError,KeyError,TypeError) as exc:
                        receipt['acceptance']='REFUSED'
                        receipt['refusal_reason']=str(exc)
                        receipt['measurement_id']=P.digest({k:v for k,v in receipt.items() if k!='measurement_id'})
                        from trader.core.child import ChildResult
                        if fn.__name__=='referee_job' and res.ok:
                            # The worker already looked. Charge the registered
                            # look as untestable; refuse its returned evidence.
                            from . import referee as registered
                            from .combo import Combination
                            value=dict(hash=Combination.from_dict(payload['combo']).hash,
                                       cut_ms=ex['split']['cut_ms'],looked=True,
                                       a={},b={},rotation={},gate1=registered.gate1({}, {}, {}),
                                       gate3=dict(passed=False,reason='refused quantitative receipt'))
                            res=ChildResult(ok=True,value=value,elapsed_s=res.elapsed_s)
                        else:
                            res=ChildResult(ok=False,value=None,error='quantitative_receipt_refused:'+str(exc),elapsed_s=res.elapsed_s)
                    measurement.append(receipt)
                    with db:
                        append(db,'bridge_measurements',receipt['measurement_id'],receipt)
                    return res
                runner = ResearchRunner(Journal(numerical_path),local_cfg,run=measured_run,predictive_experiment=ex)
                # Import all previously spent tests into this pipeline's existing
                # LORD ledger. There is one budget, never a fresh alpha per source.
                with runner.journal._tx() as tx:
                    reg=db.execute("SELECT payload FROM bridge_runtime_bindings WHERE id='registered-error-budget'").fetchone()
                    if reg:
                        b=json.loads(reg[0])[0]
                        tx.execute('INSERT OR IGNORE INTO research_budget VALUES (1,?,?,?)',(b['alpha'],b['w0'],b['registered_at']))
                    for old in records(db,'bridge_tests'):
                        keys=list(old)
                        tx.execute('INSERT OR IGNORE INTO research_tests('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',tuple(old[k] for k in keys))
                counts['planner_submissions'] += 1
                spent_cut=runner.ledger.spent_cut('4h')
                if spent_cut is not None and spent_cut!=ex['split']['cut_ms']:
                    with db:
                        feedback(db,h,ex['experiment_id'],'DATA_INSUFFICIENT',None,
                                 dict(state='PROTECTED_CUT_INCOMPATIBLE'),now_ms)
                    counts['data_insufficient']+=1
                    continue
                result = runner.step()
                with db:
                    budget=runner.journal.query('SELECT * FROM research_budget ORDER BY id')
                    if budget:
                        append(db,'bridge_runtime_bindings','registered-error-budget',budget)
                    for test in runner.ledger.tests():
                        append(db,'bridge_tests',str(test['seq']),test)
                    classification, measured, disposition = classify(runner,h,ex)
                    if measurement and measured is None:
                        step=measurement[-1]
                        raw=(step.get('value') or {}).get('results') or []
                        if raw and all(r.get('round')=='control' for r in raw):
                            measured=dict(measurement_kind='window_control',step_receipt=step,
                                          predictive_target_assessed=False)
                            if all(r.get('verdict') in ('empty','untestable') for r in raw):
                                classification='DATA_INSUFFICIENT'
                                disposition=dict(state='CONTROL_DATA_INSUFFICIENT')
                    disposition['last_step']=result
                    feedback(db,h,ex['experiment_id'],classification,measured,disposition,now_ms)
                counts[classification.lower()] += 1
            return dict(mode='ASYNC_RESEARCH_CONSUMER' if submit else 'READ_ONLY_SOURCE_SHADOW',
                counts=counts,refusals=refusals,source_mutations=0,order_submissions=0,
                trading_behavior_changed=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--enable',action='store_true')
    ap.add_argument('--once',action='store_true')
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--bank',type=Path,required=True)
    ap.add_argument('--candles',type=Path,required=True)
    ap.add_argument('--quantitative-journal',type=Path,required=True)
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--max-sources',type=int,required=True)
    ap.add_argument('--max-experiments',type=int,required=True)
    ap.add_argument('--deadline-seconds',type=int,default=30)
    ap.add_argument('--interval-seconds',type=int,default=60)
    args = ap.parse_args()
    if not args.enable:
        ap.error('--enable is required; no deployment or trading authority is implied')
    if not 1 <= args.deadline_seconds <= 60 or args.interval_seconds < 1:
        ap.error('invalid runtime bounds')
    import yaml
    cfg=yaml.safe_load(args.config.read_text())
    cfg.setdefault('research',{})['batch_seconds']=max(1,args.deadline_seconds-2)
    def deadline(*_):
        raise TimeoutError('bridge_cycle_deadline')
    signal.signal(signal.SIGALRM,deadline)
    while True:
        signal.alarm(args.deadline_seconds)
        try:
            result=cycle(args.source,args.bank,args.candles,cfg,now_ms=int(time.time()*1000),
                         max_sources=args.max_sources,max_experiments=args.max_experiments,
                         quantitative_source_path=args.quantitative_journal)
            print(P.canonical(result),flush=True)
        finally:
            signal.alarm(0)
        if args.once:
            return
        time.sleep(args.interval_seconds)


if __name__=='__main__':
    main()
