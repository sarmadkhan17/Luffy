"""Objective-specific admission to prospective coordinated PAPER research.

Development evidence is explicitly reused history, not an independent
qualification. This receipt cannot grant a venue order or live authority.
"""
import json
from copy import deepcopy
from contextlib import closing

from ...engine.protection_snapshot import ro_connect
from ..combo import Combination
from ..compose import graph, hedge as H
from . import ClosureRefused, digest, manifest
from . import hedge_qualification as HQ

KIND = 'coordinated_paper_research'
RULE = 'development-objective-to-prospective-paper.v1'


def verify(packet):
    if packet.get('trial_id') != digest({k:v for k,v in packet.items() if k!='trial_id'}):
        raise ClosureRefused('paper_trial_integrity')
    if packet.get('rule') != RULE or packet.get('authority') != 'PAPER_RESEARCH_ONLY':
        raise ClosureRefused('paper_trial_policy')
    env,candidate,result = packet['envelope'],packet['candidate'],packet['result']
    manifest.verify_envelope(env)
    graph.verify(candidate)
    if env['policy_class'] != 'CONFIGURED' or candidate['structure'] not in graph.HEDGE_STRUCTURES:
        raise ClosureRefused('paper_trial_source')
    if result.get('result_id') != digest({k:v for k,v in result.items() if k not in ('result_id','cached')}):
        raise ClosureRefused('paper_trial_result_integrity')
    if (result.get('stage'),result.get('outcome'),result.get('candidate_id'),result.get('envelope_id')) != (
            'DEVELOPMENT','HEDGE_SUPPORTED',candidate['candidate_id'],env['envelope_id']):
        raise ClosureRefused('paper_trial_development_not_supported')
    combo=Combination.from_dict(result['combo'])
    if (combo.hash,combo.trigger,combo.evaluation_scope)!=(result['combo_hash'],candidate['candidate_id'],env['envelope_id']):
        raise ClosureRefused('paper_trial_combination_differs')
    pairs=result['hedge']['per_symbol']
    if len(pairs)!=1:
        raise ClosureRefused('paper_trial_exact_pair_required')
    symbol,per=next(iter(pairs.items()))
    spec=H.validate(per['effective_spec'])
    from ..compose.managed_hedge import require_current
    require_current(spec)
    if symbol!=spec['primary']['symbol'] or spec['ratio']['method'] not in ('fixed','rolling_beta'):
        raise ClosureRefused('paper_trial_ratio_not_frozen')
    expected=deepcopy(candidate['hedge'])
    if expected['ratio']['method']=='ols_beta':
        value=spec['ratio'].get('value')
        if not isinstance(value,(int,float)) or not 0<=value<=expected['ratio']['max_ratio']:
            raise ClosureRefused('paper_trial_ratio_outside_declared_bounds')
        expected['ratio']=dict(expected['ratio'],method='fixed',value=value)
    expected['fee_bps']=max(expected['fee_bps'],float(env['risk'].get('taker_fee_pct',0))*100)
    if {k:v for k,v in expected.items() if k!='spec_id'}!={k:v for k,v in spec.items() if k!='spec_id'}:
        raise ClosureRefused('paper_trial_definition_differs')
    assessment=dict(per,objective=spec['objective']['metric'])
    if HQ.assess(assessment,spec)[0]!='ASSESSED_SUPPORTED':
        raise ClosureRefused('paper_trial_objective_not_supported')
    if per.get('funding_basis')!='RETAINED_SIGNED_HISTORY':
        raise ClosureRefused('paper_trial_actual_funding_required')
    cost=per.get('cost_model') or {}
    if spec['fee_bps']<float(env['risk'].get('taker_fee_pct',0))*100 or cost.get('fee_bps')!=spec['fee_bps']:
        raise ClosureRefused('paper_trial_cost_model_differs')
    return combo,spec


def make(candidate,env,result):
    body=dict(rule=RULE,candidate=candidate,envelope=env,result={k:v for k,v in result.items() if k!='cached'},
              authority='PAPER_RESEARCH_ONLY',independent_qualification='NOT_ESTABLISHED',
              scope='Retrospective objective support; collect new paper observations before any live decision')
    packet=dict(body,trial_id=digest(body))
    verify(packet)
    return packet


def proposals(path,*,limit=2,exclude=()):
    """Bounded SELECT-only retained leads, independently checked from numbers."""
    with closing(ro_connect(path)) as db:
        db.execute('BEGIN')
        rows=db.execute("SELECT payload FROM runs WHERE json_extract(payload,'$.stage')='DEVELOPMENT' "
                        "AND json_extract(payload,'$.outcome')='HEDGE_SUPPORTED' "
                        "AND json_extract(payload,'$.candidate_id') NOT IN (SELECT value FROM json_each(?)) "
                        "ORDER BY rowid DESC LIMIT ?",(json.dumps(sorted(exclude)),limit*4)).fetchall()
        out=[]
        for row in rows:
            result=json.loads(row[0])
            c=db.execute('SELECT payload FROM candidates WHERE id=?',(result['candidate_id'],)).fetchone()
            e=db.execute('SELECT payload FROM envelopes WHERE id=?',(result['envelope_id'],)).fetchone()
            if c is None or e is None:
                continue
            try:
                packet=make(json.loads(c[0]),json.loads(e[0]),result)
            except ClosureRefused:
                continue  # no support/coverage remains a research lead, no paper admission
            out.append(packet)
            if len(out)>=limit:
                break
        return out
