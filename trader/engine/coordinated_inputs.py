"""Exact frozen held-leg demand, independent of scan rank and paper intake.

Only bounded inference windows are requested. A held version remains manageable
when admission changes; this module grants neither entry nor promotion.
"""
import json
from dataclasses import replace

from ..core.types import TF_MS
from ..strategy import factory_handoff as F
from ..strategy.compile import compile_spec
from ..strategy.spec import StrategySpec
from ..scheduling.scan_plan import Inputs,declared_inputs
from ..research.compose import managed_hedge as M


def management_plans(version):
    from ..strategy import fill_timing
    from ..research.compose.experiments import needs_btc
    hs=version['lineage']['research']['multi_leg']['hedge_spec']
    spec=StrategySpec.from_dict(version['spec']);compiled=compile_spec(spec)
    primary=declared_inputs(spec,compiled.data_requires)
    bars=max(primary.limits[spec.timeframe],int(hs['ratio']['lookback'])+2)
    if bars>fill_timing.INFERENCE_HISTORY_MAX:raise ValueError('coordinated_active_history_bound')
    primary.limits[spec.timeframe]=bars
    packet=version['lineage']['research']['multi_leg']['packet']
    if needs_btc(packet.get('candidate') or packet['freeze']['candidate']):
        primary.btc[spec.timeframe]=bars
    hedge_spec=replace(spec,entry_long='',entry_short='',filters=[],exit=M.hedge_exit_spec(hs['management']))
    hedge=declared_inputs(hedge_spec,set())
    hedge.limits[spec.timeframe]=max(hedge.limits[spec.timeframe],bars)
    return {hs['primary']['symbol']:primary,hs['hedge']['symbol']:hedge}


def plans(journal):
    out={};errors={};versions={}
    rows=journal.query("SELECT * FROM trades WHERE status='open' AND "
        "json_extract(CASE WHEN json_valid(entry_identity_json) THEN entry_identity_json ELSE '{}' END,"
        "'$.coordinated.group_id') IS NOT NULL ORDER BY id LIMIT 65")
    if len(rows)>64:raise ValueError('coordinated_held_input_bound_exceeded')
    for row in rows:
        try:
            identity=json.loads(row['entry_identity_json']);group=identity['coordinated']
            vid=identity.get('version_id')
            if not vid:continue  # legacy ownership paths retain their own contract
            if vid not in versions:versions[vid]=F.load_version(journal,vid)
            version=versions[vid]
            hs=version['lineage']['research']['multi_leg']['hedge_spec']
            leg=group.get('leg')
            if (identity.get('status')!='VERIFIED' or leg not in ('primary','hedge')
                    or row['strategy_id']!=version['strategy_id']
                    or identity.get('strategy_id')!=version['strategy_id']
                    or identity.get('spec_sha256')!=version['spec_hash']
                    or identity.get('spec_hash')!=version['spec_hash']
                    or identity.get('exit_semantics_id')!=F._expected_exit_semantics(version)
                    or row['symbol']!=hs[leg]['symbol']):
                raise ValueError('coordinated_held_input_identity_differs')
            install=F.verify_install(journal,version,current=False)
            if identity.get('install_id')!=install['install_id']:
                raise ValueError('coordinated_held_input_install_differs')
            if leg=='primary' and (hs.get('management') or {}).get('version')==M.CURRENT_VERSION:
                # Even without an open hedge, the held main position needs
                # both native series to evaluate conditional management.
                for symbol,demand in management_plans(version).items():
                    out.setdefault(symbol,Inputs()).merge(demand)
                continue
            spec=StrategySpec.from_dict(version['spec'])
            if leg=='hedge':
                policy=hs.get('management')
                if (policy or {}).get('version')!=M.CURRENT_VERSION:continue
                # This opposite leg owns its own stop; the primary entry rule
                # and its external inputs do not become hedge stop inputs.
                spec=replace(spec,entry_long='',entry_short='',filters=[],exit=M.hedge_exit_spec(policy))
                demand=declared_inputs(spec,set())
            else:
                compiled=compile_spec(spec)
                demand=declared_inputs(spec,compiled.data_requires)
            out.setdefault(row['symbol'],Inputs()).merge(demand)
        except Exception as exc:
            errors[row['symbol']]=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
    return out,errors


def collector_needs(input_plans):
    from ..strategy.spec_evidence import _SERIES_FOR
    candles={};derivs=[]
    for symbol,plan in input_plans.items():
        candles.setdefault(symbol,set()).update(plan.limits)
        for tf in plan.btc:candles.setdefault('BTC/USDT',set()).add(tf)
        for name in sorted(plan.requires):
            if name in _SERIES_FOR:
                span=plan.spans.get(name,max((bars*TF_MS[tf] for tf,bars in plan.limits.items()),default=0))
                derivs.append((symbol,_SERIES_FOR[name],span))
    return candles,derivs


def active_plans(journal,receipt,*,at_ms):
    """Warm only exact active shortlisted books, before a first position exists.

    Collector demand confers no entry authority. Ratio history is finite and
    shared by both legs; primary feature dependencies remain primary-owned.
    """
    from .coordinated_intake import candidates,admission
    versions=candidates(journal)
    if not versions:return {},{}
    admitted=admission(receipt,at_ms);out={};errors={};count=0
    for version in versions:
        hs=version['lineage']['research']['multi_leg']['hedge_spec']
        if hs['primary']['symbol'] not in admitted['admitted_symbols']:continue
        count+=1
        if count>2:break
        try:
            F.verify_install(journal,version)
            for symbol,demand in management_plans(version).items():
                out.setdefault(symbol,Inputs()).merge(demand)
        except Exception as exc:
            errors[version['version_id']]=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
    return out,errors
