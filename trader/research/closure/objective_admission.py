"""Frozen objective comparisons using bounded test-supermartingales.

The statistical claim is episode reliability, not an assumption-free claim
about future profit or expected maximum drawdown. For each comparator the
null is E[sign(objective advantage) | preceding episodes] <= 0. Nonoverlapping
closed episodes preserve chronology. With fixed bet 1/2, each wealth factor
is 1 + sign/2, so conditional expected wealth cannot increase under that
null. Ville's bound gives p <= min(1, 1/max wealth). Requiring every comparator
to reject is an intersection-union test; comparisons never share winnings.
Full-window economics, marks, costs and cash dominance remain separate gates.

Reference: Waudby-Smith & Ramdas, JRSS-B 86(1), 2024,
https://doi.org/10.1093/jrsssb/qkad009 . No independence claim is made about
market episodes; the conditional-null assumption is explicit and material.
"""
from pathlib import Path
import hashlib
import json
import math
import numpy as np

from . import digest

METHOD_ID='objective-episode-betting.v1'
PROTOCOL=dict(method_id=METHOD_ID,version='1',episode_bars=64,bet=.5,max_episodes=256,
              null='conditional expected signed objective advantage <= 0 for each required comparator',
              selection='rule and objective frozen before independent data; one shared error-budget look',
              claim='episode reliability plus separate full-window economic assessment; not expected future profit')


def protocol_id():
    return digest(PROTOCOL)


def episode_values(equity, *, start_equity, gross, metric, episode_bars=64):
    """Metrics on the continuous book, including the boundary mark; no refit/reset."""
    if type(episode_bars) is not int or episode_bars<=0:
        raise ValueError('objective_episode_clock_invalid')
    eq=np.asarray(equity,dtype=float);g=np.asarray(gross,dtype=float)
    if eq.ndim!=1 or len(eq)!=len(g) or not len(eq) or not np.isfinite(start_equity) or start_equity<=0:
        raise ValueError('objective_episode_path_invalid')
    if not np.isfinite(eq).all() or not np.isfinite(g).all() or (eq<=0).any():
        raise ValueError('objective_episode_marks_unknown')
    out=[]
    for lo in range(0,len(eq)-episode_bars+1,episode_bars):
        hi=lo+episode_bars
        path=np.r_[start_equity if lo==0 else eq[lo-1],eq[lo:hi]]
        returns=np.diff(path)/path[:-1]*100
        if metric=='net_return_pct':value=(path[-1]/path[0]-1)*100
        elif metric=='max_drawdown_pct':value=float(np.max((1-path/np.maximum.accumulate(path))*100))
        elif metric=='worst_bar_pct':value=float(-returns.min())
        elif metric=='cvar5_pct':value=float(-np.sort(returns)[:max(1,math.ceil(len(returns)*.05))].mean())
        else:raise ValueError('objective_episode_metric_unknown')
        out.append(dict(start=lo,end=hi,value=float(value),active=bool((g[lo:hi]>0).any())))
    return out


def evaluate(core):
    """Pure deterministic re-derivation; caller supplies a previously spent alpha."""
    result=core.get('result') or {};alpha=core.get('alpha')
    if isinstance(alpha,bool) or not isinstance(alpha,(int,float)) or not 0<alpha<1:
        return dict(admitted=False,evidence=dict(status='UNAVAILABLE',reason='registered_alpha_required'))
    episodes=result.get('objective_episodes')
    if result.get('admission_protocol_id')!=protocol_id() or not isinstance(episodes,list) or not episodes:
        return dict(admitted=False,evidence=dict(status='UNAVAILABLE',reason='frozen_episode_evidence_required'))
    if len(episodes)>PROTOCOL['max_episodes']:
        raise ValueError('objective_episode_budget_exceeded')
    if result.get('objective') not in ('net_return_pct','max_drawdown_pct','cvar5_pct','worst_bar_pct','stress_loss_pct'):
        raise ValueError('objective_episode_metric_unknown')
    relative=result.get('objective')=='net_return_pct'
    names=('unhedged','reduced','cash') if relative else ('unhedged','reduced')
    logs={name:0. for name in names};peaks=dict(logs);counts={name:dict(win=0,loss=0,tie=0) for name in names}
    end=0
    for episode in episodes:
        if episode['start']!=end or episode['end']-end!=PROTOCOL['episode_bars']:
            raise ValueError('objective_episode_clock_differs')
        end=episode['end']
        if type(episode['active']) is not bool:
            raise ValueError('objective_episode_activity_unknown')
        h=episode['hedged']
        for name in names:
            control=episode[name]
            if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and np.isfinite(v) for v in (h,control)):
                raise ValueError('objective_episode_value_unknown')
            advantage=h-control if relative else control-h
            sign=int(np.sign(advantage)) if episode['active'] else 0
            counts[name]['win' if sign>0 else 'loss' if sign<0 else 'tie']+=1
            logs[name]+=math.log1p(PROTOCOL['bet']*sign)
            peaks[name]=max(peaks[name],logs[name])
    p={name:min(1.,math.exp(-peaks[name])) for name in names}
    evidence=dict(method_id=METHOD_ID,protocol_id=protocol_id(),status='ASSESSED',alpha=float(alpha),
                  episodes=len(episodes),counts=counts,p_by_comparator=p,p=max(p.values()),
                  log_max_e=peaks,input_id=digest(episodes),null=PROTOCOL['null'],claim=PROTOCOL['claim'])
    return dict(admitted=all(v<=alpha for v in p.values()),evidence=evidence)


def calibrate(*,replicates=30000,episodes=128,alpha=.0025,seed=928321):
    """Numerical calibration of THIS inferential component, not a search report.

    Includes a history-dependent conditional null. Independent final-search
    calibration is still owned by closure.calibration and is not fabricated.
    """
    from .calibration import clopper_pearson
    if not isinstance(alpha,(int,float)) or isinstance(alpha,bool) or not 0<alpha<1:
        raise ValueError('objective_calibration_alpha_invalid')
    if replicates<10000 or episodes<64 or episodes>PROTOCOL['max_episodes']:
        raise ValueError('objective_calibration_budget_too_small')
    rng=np.random.default_rng(seed);cases={}
    for name in ('null_independent','null_dependent','planted_reliable'):
        log=np.zeros(replicates);peak=log.copy();previous=np.zeros(replicates,dtype=bool)
        for _ in range(episodes):
            probability=.9 if name=='planted_reliable' else (np.where(previous,.25,.5) if name=='null_dependent' else .5)
            win=rng.random(replicates)<probability
            log+=np.where(win,math.log(1.5),math.log(.5));peak=np.maximum(peak,log);previous=win
        n=int((peak>=-math.log(alpha)).sum())
        lo,hi=clopper_pearson(n,replicates)
        cases[name]=dict(rejections=n,replicates=replicates,rate=n/replicates,interval95=[lo,hi])
    passed=all(cases[k]['interval95'][1]<=alpha for k in ('null_independent','null_dependent')) and cases['planted_reliable']['interval95'][0]>=.5
    body=dict(schema='objective-admission-calibration.v1',protocol=PROTOCOL,protocol_id=protocol_id(),
              source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),seed=seed,episodes=episodes,
              alpha=alpha,cases=cases,status='PASS' if passed else 'INCONCLUSIVE',
              scope='INFERENTIAL_COMPONENT_ONLY',financial_search_calibration=False)
    return dict(body,calibration_id=digest(body))


def report_valid(report,expected_id):
    try:
        from .calibration import clopper_pearson
        if not 0<report['alpha']<1 or not 64<=report['episodes']<=PROTOCOL['max_episodes']:
            return False
        for name in ('null_independent','null_dependent','planted_reliable'):
            case=report['cases'][name];n=case['replicates'];k=case['rejections']
            if (type(n) is not int or type(k) is not int or n<10000 or not 0<=k<=n
                    or case['rate']!=k/n or case['interval95']!=list(clopper_pearson(k,n))):
                return False
        return (report['calibration_id']==expected_id==digest({k:v for k,v in report.items() if k!='calibration_id'})
            and report['protocol_id']==protocol_id() and report['protocol']==PROTOCOL and report['status']=='PASS'
            and report['source_sha256']==hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            and report['scope']=='INFERENTIAL_COMPONENT_ONLY' and report['financial_search_calibration'] is False
            and all(report['cases'][k]['replicates']>=10000 and report['cases'][k]['interval95'][1]<=report['alpha']
                    for k in ('null_independent','null_dependent'))
            and report['cases']['planted_reliable']['interval95'][0]>=.5)
    except (KeyError,TypeError,ValueError):return False
