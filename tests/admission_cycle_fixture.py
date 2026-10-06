"""Explicit frozen admission seam for legacy state/telemetry cycle fixtures.

These tests exercise control behavior, not admission discovery. Admission itself
and the independent exposure path are exercised by test_hierarchical_admission.
No production compatibility fallback is introduced to satisfy old mocks.
"""
from types import SimpleNamespace
from trader import attention_admission as A


def install(kernel, symbols, frames=None):
    policy=A.AdmissionPolicy(12,2,2,0)
    cut=1800000000000
    rows={s:dict(symbol=s,asset_class='CRYPTO',quality='VALID',available_at_ms=cut,missing_reasons=[]) for s in symbols}
    o=dict(schema='broad-crypto.v1',source_cut_ms=cut,source_identity='TEST-ONLY',rows=rows,
        peer_cohort=list(symbols),strategy_relevance={s:[] for s in symbols},context_anchors=['BTC/USDT','ETH/USDT'],
        exposure_required=[],salience_rows={s:dict(eligible=True,status='ok',salience=3,components={'test':3}) for s in symbols},
        salience_config={'min_salience':2},salience_anchor_ms=cut,status='TEST-ONLY')
    r=A.admit(o,policy,A.initial_state())
    kernel._symbol_roles=A.SymbolRoles(**{k:tuple(v) for k,v in r['roles'].items()})
    kernel._admission_receipt=r
    kernel._admission_plan=lambda:(r,frames or {})
    kernel._peer_frames=lambda _:frames or {}


def bind_market(market, cfg, cut):
    symbol=market.symbol
    o=dict(schema='broad-crypto.v1',source_cut_ms=cut,source_identity='TEST-ONLY-market',
        rows={symbol:dict(symbol=symbol,asset_class='CRYPTO',quality='VALID',available_at_ms=cut,missing_reasons=[])},
        peer_cohort=[symbol],strategy_relevance={symbol:['TEST-ONLY']},context_anchors=['BTC/USDT','ETH/USDT'],
        exposure_required=[],salience_rows={symbol:dict(eligible=True,status='ok',salience=3,components={'test':3})},
        salience_config={'min_salience':2},salience_anchor_ms=cut,status='TEST-ONLY')
    receipt=A.admit(o,A.AdmissionPolicy.from_config(cfg),A.initial_state())
    market.admission_context=dict(receipt_id=receipt['receipt_id'],category='event')
    return receipt
