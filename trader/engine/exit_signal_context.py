"""Detached input context for replaying one exit signal, never an order.

Frames are the exact already-consumed inference frames, not a history fetch.
Only WorldModel cuts those frames can query are retained. A bounded caller
stores this once per signal decision, outside health/order transport.
"""
import ast
import math
from enum import Enum
from dataclasses import dataclass,replace,field
from types import MappingProxyType

from ..world.context import WorldContext,ObservationQuery
from ..world.model import Horizon,Scope,ScopeLevel
from ..world.observation import Observation,Quality
from ..world.replay import WorldHistory

import numpy as np
import pandas as pd


def pack(value):
    if isinstance(value,pd.DataFrame):
        return {'$frame':dict(columns=list(value.columns),index=pack(list(value.index)),
            dtypes=[str(t) for t in value.dtypes],index_dtype=str(value.index.dtype),index_name=pack(value.index.name),
            data=pack(list(value.itertuples(index=False,name=None))),attrs=pack(dict(value.attrs)))}
    if value is pd.NA:return {'$missing':'NA'}
    if value is pd.NaT:return {'$missing':'NaT'}
    if isinstance(value,pd.Timestamp):return {'$timestamp':value.isoformat()}
    if isinstance(value,np.generic):return pack(value.item())
    if isinstance(value,np.ndarray):return {'$array':dict(dtype=value.dtype.str,data=pack(value.tolist()))}
    if isinstance(value,Enum):return pack(value.value)
    if isinstance(value,float) and not math.isfinite(value):return {'$nonfinite':str(value)}
    if isinstance(value,tuple):return {'$tuple':[pack(v) for v in value]}
    if isinstance(value,list):return [pack(v) for v in value]
    if isinstance(value,dict):
        if any(not isinstance(k,str) for k in value):raise ValueError('signal_context_nonstring_key')
        return {'$dict':[[k,pack(v)] for k,v in value.items()]}
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise ValueError('signal_context_unserializable:'+type(value).__name__)


def unpack(value):
    if isinstance(value,list):return [unpack(v) for v in value]
    if not isinstance(value,dict):return value
    if len(value)!=1:raise ValueError('signal_context_invalid_tag')
    tag,raw=next(iter(value.items()))
    if tag=='$timestamp':return pd.Timestamp(raw)
    if tag=='$nonfinite':return float(raw)
    if tag=='$array':return np.array(unpack(raw['data']),dtype=raw['dtype'])
    if tag=='$missing':return pd.NA if raw=='NA' else pd.NaT
    if tag=='$tuple':return tuple(unpack(v) for v in raw)
    if tag=='$dict':return {k:unpack(v) for k,v in raw}
    if tag=='$frame':
        frame=pd.DataFrame(unpack(raw['data']),columns=raw['columns'],index=unpack(raw['index']))
        for column,dtype in zip(raw['columns'],raw['dtypes'],strict=True):frame[column]=frame[column].astype(dtype)
        frame.index=pd.Index(frame.index,dtype=raw['index_dtype'],name=unpack(raw['index_name']))
        frame.attrs.update(unpack(raw['attrs']))
        return frame
    raise ValueError('signal_context_unknown_tag')


def _artifacts(spec):
    from ..strategy import dsl,fitted,fill_timing
    out={}
    for expression in fill_timing._expressions(spec):
        for node in ast.walk(dsl.parse(expression)):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('fitted','fitted_pass'):
                key=node.args[0].value
                out[key]=pack(fitted.get(key))
    return out


@dataclass(frozen=True,slots=True)
class ExitWorldQueries(WorldContext):
    """Replay-only exact query projection; never synthesizes a WorldModel."""
    queries: tuple = ()
    _queries_by_key: object = field(init=False,repr=False,compare=False)

    def __post_init__(self):
        WorldContext.__post_init__(self)
        lookup={}
        for tf,query in self.queries:
            key=(tf,query.as_of_ms,query.scope,query.horizon,query.kind)
            if key in lookup:raise ValueError('ambiguous_exit_world_query')
            lookup[key]=query
        object.__setattr__(self,'_queries_by_key',MappingProxyType(lookup))

    def observations(self,as_of_ms,scope,horizon,*,kind,timeframe=None):
        query=self._queries_by_key.get((timeframe,as_of_ms,scope,horizon,kind))
        if query is not None:
            if query.quality is Quality.VALID and (len(query.observations)!=1
                    or not query.observations[0].is_fresh_at(as_of_ms)):
                return replace(query,quality=Quality.UNKNOWN,reason='EXIT_QUERY_OBSERVATION_UNUSABLE')
            return query
        return ObservationQuery(as_of_ms,scope,horizon,kind,Quality.UNKNOWN,'UNRECORDED_EXIT_QUERY')


def capture(ctx,spec):
    from ..core.types import TF_MS
    from ..strategy import compile as C
    groups={k:getattr(ctx,k) for k in ('frames','btc','derivs','universe','market')}
    world=None
    if ctx.world is not None:
        queries=[]
        deps=C.compile_spec(spec).deps['world']
        for tf,frame in (ctx.frames or {}).items():
            if tf not in TF_MS or frame is None:continue
            cuts=[int(pd.Timestamp(t).timestamp()*1000)+TF_MS[tf] for t in frame['ts']]
            if cuts and ctx.as_of_ms is not None and frame.attrs.get('read_mode') not in ('replay','retrospective'):
                cuts[-1]=ctx.as_of_ms
            for cut in sorted(set(cuts)):
                for kind,horizon in deps:
                    scope=Scope(ScopeLevel.INSTRUMENT,ctx.symbol)
                    query=ctx.world.observations(cut,scope,Horizon(horizon),kind=kind,timeframe=tf)
                    queries.append(dict(timeframe=tf,as_of_ms=cut,scope=scope.to_dict(),horizon=horizon,kind=kind,
                        quality=query.quality.value,reason=query.reason,model_id=query.model_id,
                        observations=[o.to_dict() for o in query.observations]))
        world=dict(schema='retained-exit-world-queries.v1',queries=queries,source_status=ctx.world.source_status,
            as_of_ms=ctx.world.as_of_ms,allow_reconstruction=ctx.world.allow_reconstruction,dataset_id=ctx.world.dataset_id)
    return dict(schema='exit-signal-context.v1',spec=spec.to_dict(),tf=ctx.tf,symbol=ctx.symbol,
        as_of_ms=ctx.as_of_ms,groups=pack(groups),world=world,artifacts=_artifacts(spec),
        compiler=dict(compiler_version=C.COMPILER_VERSION,feature_version=C.FEATURE_VERSION,
                      feature_contract=C.feature_contract_sha256()))


def replay(body,bar_ms):
    """Re-evaluate the recorded exit; equality is not external authenticity."""
    from ..strategy import compile as C,fitted
    from ..strategy.spec import StrategySpec
    expected=dict(compiler_version=C.COMPILER_VERSION,feature_version=C.FEATURE_VERSION,
                  feature_contract=C.feature_contract_sha256())
    if body.get('schema')!='exit-signal-context.v1' or body['compiler']!=expected:
        raise ValueError('signal_context_compiler_differs')
    for key,artifact in body['artifacts'].items():
        if pack(fitted.get(key))!=artifact:raise ValueError('signal_context_artifact_differs')
    groups=unpack(body['groups']);world=None
    if body['world'] is not None:
        raw=body['world']
        if raw.get('schema')!='retained-exit-world-queries.v1':raise ValueError('signal_world_query_schema')
        queries=[]
        for q in raw['queries']:
            scope=Scope(ScopeLevel(q['scope']['level']),q['scope']['identifier'])
            query=ObservationQuery(q['as_of_ms'],scope,Horizon(q['horizon']),q['kind'],Quality(q['quality']),
                q['reason'],q['model_id'],tuple(Observation.from_dict(o) for o in q['observations']))
            queries.append((q['timeframe'],query))
        world=ExitWorldQueries(WorldHistory(()),raw['source_status'],raw['as_of_ms'],
                              raw['allow_reconstruction'],raw['dataset_id'],tuple(queries))
    spec=StrategySpec.from_dict(body['spec'])
    frame=groups['frames'][spec.timeframe]
    selected=[i for i,t in enumerate(frame['ts']) if int(pd.Timestamp(t).timestamp()*1000)==bar_ms]
    if len(selected)!=1:raise ValueError('signal_context_bar_missing')
    signal,available=C.compile_spec(spec).exit_signal_detail(groups['frames'],btc=groups['btc'],derivs=groups['derivs'],
        universe=groups['universe'],market=groups['market'],symbol=body['symbol'],world=world)
    i=selected[0]
    return dict(signal=bool(signal[i]) if signal is not None else False,
                available=bool(available[i]) if available is not None else False)
