"""Objective-specific Factory probation from immutable prospective PAPER events.

Replays the exact recorded decisions/fills before scoring. Comparators are
modelled alternatives, never venue fills. No controls, approvals or orders.
"""
import json
import math
import numpy as np
from ..core.types import TF_MS
from ..research.closure import digest
from ..research.closure import hedge_qualification as HQ
from ..research.compose import hedge as H

MAX_BARS=8192
MAX_QUOTE_EVENTS=131072
RULE='prospective-objective-paper-probation.v1'


def replay_quote_events(hs,identity,install,events):
    """Re-run exact receipt/timing validation in RAM, with no live writer."""
    from contextlib import contextmanager,closing
    import sqlite3
    from types import SimpleNamespace
    from .coordinated_paper import CoordinatedPaper,QUOTE_TIMING
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory=sqlite3.Row
        @contextmanager
        def tx():
            with db:yield db
        journal=SimpleNamespace(_tx=tx,query=lambda sql,args=():[dict(r) for r in db.execute(sql,args)])
        runner=CoordinatedPaper(journal)
        key=runner.start(spec=hs,identity=identity,equity=install['starting_equity'],
                         primary_exit=install['primary_exit'],timing=QUOTE_TIMING)
        for event in events:
            request=event['request']
            actual=runner.advance(key,event['bar'],request['observation'],fills=request['fills'],allow_entry=request['allow_entry'])
            if actual!=event:
                raise ValueError('paper_quote_replay_differs')


def assess(journal,cfg,version,since_ms,at_ms):
    from ..strategy import factory_handoff as F
    policy=F._policy(cfg)
    base=dict(policy=policy,trades=[],trades_sha256=F._jsha([]),excluded_trades=[],stats=None,
              trade_identity='exact coordinated version/install and immutable paper events')
    if policy is None:return dict(base,status=F.P_NO_POLICY)
    install=F.verify_install(journal,version,current=False)
    if since_ms!=install['installed_at_ms'] or install.get('execution')!='COORDINATED_PAPER_ONLY':
        return dict(base,status=F.P_INSTALLED_DIFFERS)
    hs=version['lineage']['research']['multi_leg']['hedge_spec']
    packet=version['lineage']['research']['multi_leg']['packet']
    per=(next(iter(packet['result']['hedge']['per_symbol'].values())) if 'result' in packet
         else packet['freeze']['development_comparators'])
    # An exposure-matched size is chosen in development, never fitted on
    # prospective paper outcomes. Missing legacy evidence is not zero.
    reduced=(per.get('comparators',per).get('reduced') or {}).get('fraction')
    if isinstance(reduced,bool) or not isinstance(reduced,(int,float)) or not math.isfinite(reduced) or not 0<=reduced<=1:
        return dict(base,status='PAPER_BENCHMARK_UNAVAILABLE')
    policy=dict(policy,rule=RULE,primary_objective=hs['objective']['metric'],reduced_fraction=float(reduced),
                acceptance='declared objective improves on both controls and majority of fixed chronological folds; cash comparison retained',
                threshold_basis='existing min_trades applied to completed two-leg cycles; directional winrate/PF do not judge risk reduction')
    base['policy']=policy
    identity=dict(exec_mode='paper',version_id=version['version_id'],spec_hash=version['spec_hash'],
        install_id=install['install_id'],admission_id=install['validation_receipt_id'],first_bar_ms=install['first_bar_ms'])
    from .coordinated_paper import QUOTE_TIMING,QUOTE_PROTOCOL_ID
    prospective=install.get('paper_timing')==QUOTE_TIMING
    ident=dict(spec=hs,identity=identity,starting_equity=install['starting_equity'],primary_exit=install['primary_exit'])
    if prospective:ident['paper_execution_protocol']=QUOTE_PROTOCOL_ID
    key=digest(ident)
    end_bar=(int(at_ms)-install['first_bar_ms'])//TF_MS[version['spec']['timeframe']]-1
    limit=MAX_QUOTE_EVENTS if prospective else MAX_BARS
    clock="json_extract(payload,'$.decision_ms')" if prospective else 'bar'
    rows=journal.query(f'SELECT bar,payload,sha256 FROM coordinated_paper_events WHERE book_id=? AND {clock}<=? ORDER BY bar LIMIT ?',
                       (key,int(at_ms) if prospective else end_bar,limit+1))
    if not rows:return dict(base,status=F.P_INSUFFICIENT)
    if len(rows)>limit:return dict(base,status='PAPER_ASSESSMENT_BOUND_EXCEEDED')
    events=[];closed=[];was_held=False;start=0
    for index,row in enumerate(rows):
        event=json.loads(row['payload'])
        if row['bar']!=index or event['bar']!=index or event['book_id']!=key or digest(event)!=row['sha256']:
            return dict(base,status=F.P_INCOMPLETE,reason='paper_event_integrity_or_clock')
        obs=event['request']['observation']
        expected_cut=install['first_bar_ms']+((event['native_bar'] if prospective else index)+1)*TF_MS[version['spec']['timeframe']]
        if obs.get('signal_cut_ms')!=expected_cut:
            return dict(base,status=F.P_INCOMPLETE,reason='paper_observation_cut_differs')
        fill_basis='SIMULATED_FIRST_SUBSEQUENT_BID_ASK' if prospective else 'SIMULATED_NEXT_BAR_OPEN'
        if (event['real_order_submissions']!=0 or event['fill_basis']!=fill_basis
                or obs.get('input_gap') or event.get('incomplete_marks') or event.get('liquidated')):
            return dict(base,status=F.P_COST_INCOMPLETE,reason='paper_marks_inputs_or_margin_incomplete')
        if obs.get('funding') is None or event['net_economics']!='MODELLED_PAPER_ONLY':
            return dict(base,status=F.P_COST_INCOMPLETE,reason='paper_signed_funding_unavailable')
        held=any(event['positions'].values())
        if held and not was_held:start=index
        if was_held and not held:
            closed.append(dict(id=key+':'+str(index),opened_bar=start,closed_bar=index,exec_mode='paper',
                               version_id=version['version_id'],install_id=install['install_id']))
        was_held=held;events.append(event)
    observations=[e['request']['observation'] for e in events]
    base.update(trades=closed,trades_sha256=digest(dict(cycles=closed,event_hashes=[r['sha256'] for r in rows])))
    if len(closed)<policy['min_trades']:
        return dict(base,status=F.P_INSUFFICIENT,stats=dict(completed_cycles=len(closed),required_cycles=policy['min_trades']))
    arr=lambda key:np.array([o[key] if o[key] is not None else np.nan for o in observations],float)
    class RecordedFills:
        def fill(self,instructions,t):
            fills=dict(events[t]['fills'])
            # Aggregating floating-point fees per bar versus per fill can
            # move a later simulated request by a few ULPs. Normalize only
            # that numerical replay difference, never a genuine overfill.
            for instruction in instructions:
                value=fills.get(instruction.oid,0.)
                if value>instruction.qty and math.isclose(value,instruction.qty,rel_tol=1e-12,abs_tol=1e-12):
                    fills[instruction.oid]=instruction.qty
            return fills
    kwargs=dict(equity=install['starting_equity'],opens=(arr('po'),arr('ho')),
        funding=tuple(np.array([o['funding'][leg] for o in observations],float) for leg in ('primary','hedge')),
        ratios=arr('ratio'),correlations=np.array([o.get('corr') for o in observations],object),
        entry_allowed=np.array([e['request']['allow_entry'] for e in events],bool),primary_exit=install['primary_exit'],
        decision_bars=[o.get('primary_exit_observation') for o in observations])
    try:
        if prospective:
            replay_quote_events(hs,identity,install,events)
            kwargs.update(native_bars=np.array([e['native_bar'] for e in events],int),
                quote_times=np.array([e['decision_ms'] for e in events],np.int64),
                bid_ask={leg:tuple(np.array([o['quotes'][leg][side] for o in observations],float) for side in ('bid','ask'))
                         for leg in ('primary','hedge')},
                funding_time_weights=np.r_[0.,np.diff([e['decision_ms'] for e in events])]/TF_MS[version['spec']['timeframe']])
        actual=H.replay(hs,arr('p'),arr('h'),arr('signal').astype(int),arr('exit').astype(bool),venue=RecordedFills(),**kwargs)
        curve=np.array([e['equity'] for e in events],float)
        if (not np.isfinite(curve).all() or not np.allclose(actual['equity'],curve,rtol=1e-10,atol=1e-7)
                or any(not math.isclose(actual['book'].pos[leg],events[-1]['positions'][leg],rel_tol=1e-10,abs_tol=1e-9) for leg in ('primary','hedge'))):
            return dict(base,status=F.P_INCOMPLETE,reason='paper_replay_differs_from_recorded_execution')
        result=H.evaluate(hs,arr('p'),arr('h'),arr('signal').astype(int),arr('exit').astype(bool),
            reduced_fraction=reduced,venue=RecordedFills(),continuous_folds=True,**kwargs)
        status,reason=HQ.assess(result,hs)
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        return dict(base,status=F.P_INCOMPLETE,reason='paper_objective_unavailable:'+getattr(exc,'code',type(exc).__name__))
    return dict(base,status=F.P_SATISFIED if status=='ASSESSED_SUPPORTED' else F.P_NOT_SATISFIED,
        stats=dict(completed_cycles=len(closed),objective=hs['objective']['metric'],comparators=result['comparators'],
                   folds=result['folds'],reason=reason,basis='MODELLED_PROSPECTIVE_PAPER_ONLY',venue_execution='NOT_ESTABLISHED'),
        objective_assessment=status)
