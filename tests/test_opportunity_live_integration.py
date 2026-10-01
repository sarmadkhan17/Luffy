"""Synthetic integration fixtures. No real economic/eligibility claims."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from trader.cognition import opportunity_context as oc
from trader.portfolio import opportunity_live as L, economics as E
from trader.portfolio.opportunity_registry import Registry
from trader.portfolio.allocator import (Source, Evidence, Status, Inputs, Portfolio, Position,
                                       allocate, canonical, digest, inputs_from_payload)
from trader.strategy import factory_handoff as F
from trader.strategy.signal_occurrence import spec_fingerprint
from trader.strategy.spec import StrategySpec
from trader.world import WorldModel, WorldState, HierarchyNode, Scope, ScopeLevel
from trader.world.replay import WorldModelRecord
from tests.test_compile import _spec
from tests.test_opportunity_context import evidence, _signal  # noqa: F401
from tests.test_market_investigation import prefix  # noqa: F401

IID = 'binance_usdm:futures:BTCUSDT'
SYMBOL = 'BTC/USDT:USDT'


def snapshot(side='long', cut=1000):
    from trader.engine.evidence_capture import position_snapshot
    from trader.observability.portfolio_observation import observe_positions
    from trader.core.types import MarketType
    rows = [] if side is None else [dict(info={'symbol': 'BTCUSDT', 'entryPrice': '10'},
                                        symbol=SYMBOL, contracts=1, side=side)]
    obs = observe_positions(rows, exchange_id='binanceusdm', market_type=MarketType.FUTURES,
                            environment='demo', source_ref='https://demo-fapi.binance.com',
                            request_start_ms=cut-20, response_received_ms=cut)
    return position_snapshot(obs, rows)


@pytest.fixture
def live_inputs():
    spec, sh = F._frozen_spec(_spec(timeframe='4h', direction='both', entry_short='close < ema(20)'))
    version = F._version_record(spec['id'], spec, sh, None, {'kind': 'TEST-ONLY', 'id': 'fixture'}, [])
    row = F._version_row(version, 900)
    signal = {'symbol': SYMBOL, 'action': 'BUY', 'params': dict(spec_id=spec['id'],
                spec_fingerprint=spec_fingerprint(StrategySpec.from_dict(spec)),
                signal_timeframe='4h', signal_bar_close_ms=950)}
    return dict(as_of_ms=1000, symbol=SYMBOL, instrument_id=IID,
                cycle_id='cycle-1', candidate_id='candidate-1',
                sources=(L.source('signals', [signal], 960, 2000),
                         L.source('strategy_version', row, 900, 2000),
                         L.source('portfolio', snapshot(), 1000, 2000)),
                required_roles=('signals', 'strategy_version', 'portfolio'))


def economics(receipt):
    from scripts.opportunity_context_shadow import DIMENSIONS
    binding = L.economic_binding(receipt, **DIMENSIONS)
    return E.Inputs(binding, context=(receipt.as_source(),))


def test_v1_unchanged_and_compatible(live_inputs):
    r = L.produce(**live_inputs)
    args = {k: live_inputs[k] for k in ('as_of_ms', 'symbol', 'instrument_id')}
    signal = json.loads(live_inputs['sources'][0].payload_json)['data']
    assert r.context == oc.build(**args, signals=signal)
    assert r.context.to_dict()['schema_version'] == 'opportunity-context.v1'
    assert r.context.to_dict()['authority'] == 'NONE'


def test_full_attention_investigation_bound_at_cut(evidence):  # noqa: F811
    e = evidence
    roles = [L.source('attention_scan', e['scan'], e['scan']['persisted_at_ms']),
             L.source('allocation', e['allocation'], e['cut']),
             L.source('investigation', asdict(e['inv']), e['cut']),
             L.source('investigation_update', asdict(e['update']), e['cut'])]
    r = L.produce(as_of_ms=e['cut'], symbol=e['symbol'], instrument_id=None,
                  cycle_id='c', candidate_id='d', sources=roles)
    assert r.context.to_dict()['investigation']['investigation_id'] == e['inv'].investigation_id
    assert r.context.to_dict()['attention']['scan_id'] == e['scan']['scan_id']


@pytest.mark.parametrize('change', ['availability', 'signal_close', 'portfolio_time', 'version_time'])
def test_future_sources_refused(live_inputs, change):
    sources = list(live_inputs['sources'])
    index = {'availability': 0, 'signal_close': 0, 'portfolio_time': 2, 'version_time': 1}[change]
    raw = json.loads(sources[index].payload_json)
    if change == 'availability':
        raw['known_at_ms'] = 1001
    elif change == 'signal_close':
        raw['data'][0]['params']['signal_bar_close_ms'] = 1001
    elif change == 'portfolio_time':
        raw['data'] = snapshot(cut=1001)
    else:
        raw['data']['recorded_at_ms'] = 1001
    sources[index] = Source.freeze(sources[index].source_id, raw)
    with pytest.raises(ValueError):
        L.produce(**{**live_inputs, 'sources': sources})


def test_other_instrument_and_mutated_object_refused(live_inputs):
    s = live_inputs['sources'][0]
    raw = json.loads(s.payload_json)
    raw['data'][0]['symbol'] = 'ETH/USDT:USDT'
    with pytest.raises(ValueError):
        L.produce(**{**live_inputs, 'sources': (Source.freeze(s.source_id, raw), *live_inputs['sources'][1:])})
    forged = replace(s)
    object.__setattr__(forged, 'payload_json', '{}')
    with pytest.raises(ValueError):
        L.produce(**{**live_inputs, 'sources': (forged, *live_inputs['sources'][1:])})


def world(cut=1000, symbol=SYMBOL):
    scope = Scope(ScopeLevel.INSTRUMENT, symbol)
    global_scope = Scope(ScopeLevel.GLOBAL, 'world')
    asset = Scope(ScopeLevel.ASSET_CLASS, 'crypto')
    return WorldModelRecord.from_model(WorldModel(cut, (HierarchyNode(global_scope), HierarchyNode(asset, global_scope), HierarchyNode(scope, asset)),
                                                  (WorldState(symbol, cut),))).to_json()


def test_world_cut_and_instrument_fail_closed(live_inputs):
    for record in (world(999), world(1000, 'ETH/USDT:USDT')):
        with pytest.raises(ValueError, match='WORLD_MODEL_'):
            L.produce(**{**live_inputs, 'sources': (*live_inputs['sources'], L.source('world_model', record, 1000))})
    r = L.produce(**{**live_inputs, 'sources': (*live_inputs['sources'], L.source('world_model', world(), 1000))})
    assert r.context.to_dict()['world_model']['as_of_ms'] == 1000


def test_stale_required_and_optional_evidence(live_inputs):
    sources = list(live_inputs['sources'])
    sources[2] = L.source('portfolio', snapshot(cut=800), 800, 900)
    with pytest.raises(ValueError, match='REQUIRED_CONTEXT_EVIDENCE'):
        L.produce(**{**live_inputs, 'sources': sources})
    r = L.produce(**{**live_inputs, 'sources': sources, 'required_roles': ('signals', 'strategy_version')})
    assert json.loads(r.payload_json)['portfolio_status'] == 'UNKNOWN'
    r = L.produce(**live_inputs)
    with pytest.raises(ValueError, match='STALE'):
        L.replay(r, live_inputs['sources'], 2001)


def test_registry_exact_repeat_distinct_and_restart(live_inputs, tmp_path):
    registry = Registry(tmp_path / 'registry.db')
    one = L.produce(**live_inputs)
    a = registry.observe(one.context, 'cycle-1', 'candidate-1')
    two = L.produce(**{**live_inputs, 'as_of_ms': 1100, 'cycle_id': 'cycle-2', 'candidate_id': 'candidate-2'})
    b = registry.observe(two.context, 'cycle-2', 'candidate-2')
    assert a['opportunity_id'] == b['opportunity_id']
    assert b['related_cycle_ids'] == ['cycle-1', 'cycle-2']
    assert b['opened_at'] == 1000
    registry.close()
    registry = Registry(tmp_path / 'registry.db')
    assert registry.observe(two.context, 'cycle-2', 'candidate-2') == b
    for field, value in [('action', 'SELL'), ('signal_bar_close_ms', 951), ('signal_timeframe', '1h'), ('spec_fingerprint', 'e'*64)]:
        data = json.loads(live_inputs['sources'][0].payload_json)['data']
        if field == 'action':
            data[0][field] = value
        else:
            data[0]['params'][field] = value
        other = oc.build(as_of_ms=1000, symbol=SYMBOL, instrument_id=IID, signals=data)
        assert registry.observe(other, 'cycle-1', 'candidate-1')['opportunity_id'] != a['opportunity_id']
    registry.close()


def test_unproven_setup_never_merges_and_terminal_immutable(tmp_path):
    registry = Registry(tmp_path / 'registry.db')
    ctx = oc.build(as_of_ms=1000, symbol=SYMBOL, instrument_id=IID)
    a = registry.observe(ctx, 'cycle-a', 'candidate')
    b = registry.observe(ctx, 'cycle-b', 'candidate')
    assert a['opportunity_id'] != b['opportunity_id']
    done = registry.resolve(a['opportunity_id'], 'SKIPPED', 'decision-id', 1001)
    assert registry.resolve(a['opportunity_id'], 'SKIPPED', 'decision-id', 1001) == done
    with pytest.raises(ValueError):
        registry.resolve(a['opportunity_id'], 'TRADED', 'other', 1002)
    with pytest.raises(ValueError):
        registry.observe(ctx, 'cycle-a', 'candidate')
    registry.close()


@pytest.mark.parametrize('side,expected', [('long', 'SUPPORTS_EXISTING'), ('short', 'CONFLICTS_EXISTING'), (None, 'NO_ACTION')])
def test_venue_position_interaction_only(live_inputs, side, expected):
    sources = (*live_inputs['sources'][:2], L.source('portfolio', snapshot(side), 1000, 2000))
    r = L.produce(**{**live_inputs, 'sources': sources})
    p = json.loads(r.payload_json)
    assert p['existing_position_interaction'] == expected
    assert p['authority'] == 'NONE'


def test_context_economics_allocator_exact_binding_and_cash(live_inputs):
    r = L.produce(**live_inputs)
    ei = economics(r)
    er = E.build(ei)
    result = json.loads(er.result_json)
    assert result['context_id'] == r.context.context_id
    assert result['economic_status'] == 'UNAVAILABLE'
    assert E.verify(er, ei, 1500)
    assert not E.verify(er, ei, 2001)
    c, sources = L.candidate(r, er)
    assert c.context_id == r.context.context_id
    sources = tuple({s.source_id: s for s in sources}.values())
    unknown = Evidence(Status.UNKNOWN, ())
    portfolio = Portfolio('missing', 1000, None, unknown, (), ())
    ai = Inputs(1000, (c,), portfolio, unknown, unknown, 'FROZEN', sources)
    proposal = allocate(ai)
    result = json.loads(proposal.result_json)
    assert result['decision'] == 'NO_ALLOCATION'
    assert result['cash_candidate']['expression'] == 'CASH'
    assert result['candidates'][0]['context_id'] == c.context_id
    assert allocate(inputs_from_payload(json.loads(proposal.inputs_json))) == proposal
    changed = replace(c, opportunity_context_json=oc.build(as_of_ms=1000, symbol=SYMBOL, instrument_id=IID).canonical_json)
    result = json.loads(allocate(replace(ai, candidates=(changed,))).result_json)
    assert 'EXPECTED_ECONOMICS_RECEIPT_REFUSED' in result['candidates'][0]['refusal_reasons']
    tampered = replace(c, validation=Evidence(Status.ESTABLISHED, (sources[0].source_id,)))
    result = json.loads(allocate(replace(ai, candidates=(tampered,))).result_json)
    assert 'EXPECTED_ECONOMICS_RECEIPT_REFUSED' in result['candidates'][0]['refusal_reasons']
    with pytest.raises(ValueError):
        L.candidate(L.produce(**{**live_inputs, 'as_of_ms': 1100}), er)


def test_required_context_missing_stays_unavailable(live_inputs):
    r = L.produce(**live_inputs)
    ei = economics(r)
    for binding, context in [(replace(ei.binding, context_json='{}'), ei.context), (ei.binding, ())]:
        bad = E.Inputs(binding, context=context)
        er = E.build(bad)
        assert json.loads(er.result_json)['economic_status'] == 'UNAVAILABLE'
        assert not E.verify(er, bad, 1000)


def test_replay_missing_mutated_and_after_new_process(live_inputs, tmp_path):
    r = L.produce(**live_inputs)
    path = L.persist(r, tmp_path / 'contexts')
    assert L.persist(r, tmp_path / 'contexts') == path
    assert L.replay(r, live_inputs['sources']) == r
    with pytest.raises(ValueError):
        L.replay(r, live_inputs['sources'][:-1])
    payload = json.loads(r.payload_json)
    payload['authority'] = 'RISK'
    with pytest.raises(ValueError):
        L.replay(L.LiveReceipt(digest(payload), canonical(payload)), live_inputs['sources'])
    script = """import json,sys
from pathlib import Path
from trader.portfolio.opportunity_live import LiveReceipt,replay
from trader.portfolio.allocator import Source,digest,canonical
p=json.loads(Path(sys.argv[1]).read_text());r=LiveReceipt(digest(p),canonical(p))
print(replay(r,tuple(Source(**s) for s in p['sources'])).context.context_id)
"""
    result = subprocess.run([sys.executable, '-c', script, str(path)], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == r.context.context_id


def test_shadow_read_only_and_exact_snapshot_chain(tmp_path, monkeypatch):
    from scripts import opportunity_context_shadow as S
    from trader.core.journal import Journal
    from trader.engine.evidence_capture import record_snapshot
    j = Journal(tmp_path / 'sources' / 'journal.db')
    record_snapshot(j, snapshot(), at_ms=1000)
    j.kv_set('control_state', 'FROZEN')
    journal = tmp_path / 'sources' / 'journal.db'
    attention = tmp_path / 'sources' / 'absent-attention.db'
    inv = tmp_path / 'sources' / 'absent-investigation.db'
    before = j.query('SELECT * FROM state_kv')
    monkeypatch.setattr(S.time, 'time_ns', lambda: 1000*1000000)
    authorizer_calls = []
    original = S._read
    def reader(*args):
        db = original(*args)
        def authorize(action, a, b, c, d):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                authorizer_calls.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        return db
    monkeypatch.setattr(S, '_read', reader)
    out = S.run(journal, attention, inv, {'attention': {'stale_seconds': 300}}, tmp_path / 'output')
    assert out['decision'] == 'NO_ALLOCATION'
    assert out['candidate_count'] == 0 and out['context_count'] == 0
    assert not authorizer_calls
    assert j.query('SELECT * FROM state_kv') == before
    assert not attention.exists() and not inv.exists()
    with pytest.raises(ValueError, match='OUTSIDE_PRODUCTION_STORAGE'):
        S.run(journal, attention, inv, {}, journal.parent / 'output')


def test_registry_exact_version_lineage_is_separate(live_inputs, tmp_path):
    registry = Registry(tmp_path / 'registry.db')
    ctx = L.produce(**live_inputs).context
    a = registry.observe(ctx, 'same-cycle', 'same-candidate', 'version-a')
    b = registry.observe(ctx, 'same-cycle', 'same-candidate', 'version-b')
    assert a['opportunity_id'] != b['opportunity_id']
    registry.close()


def test_context_required_by_registered_model_not_optional(monkeypatch):
    from tests.economics_fixtures import frozen, install_models
    install_models(monkeypatch)
    validator = lambda raw: raw.get('test_only') is True
    validator.requires_opportunity_context = True
    monkeypatch.setitem(E.GROSS_MODELS, ('TEST-ONLY-distribution', '1'), validator)
    ei = frozen()
    r = E.build(ei)
    assert json.loads(r.result_json)['economic_status'] == 'UNAVAILABLE'
    assert not E.verify(r, ei, 1000)


def test_complete_test_only_economics_preserve_context_binding(live_inputs, monkeypatch):
    from tests.economics_fixtures import frozen, install_models
    install_models(monkeypatch)
    live = L.produce(**live_inputs)
    b = economics(live).binding
    ei = frozen('2', b)
    ei = replace(ei, context=(*ei.context, live.as_source()))
    receipt = E.build(ei)
    assert json.loads(receipt.result_json)['economic_status'] == 'POSITIVE'
    assert json.loads(receipt.result_json)['context_id'] == live.context.context_id
    c, sources = L.candidate(live, receipt)
    assert c.context_id == live.context.context_id
    # Complete TEST-ONLY economics gives no eligibility/Risk/sizing authority.
    assert c.validation.status == Status.UNKNOWN
    assert c.capacity.status == Status.UNKNOWN
    assert c.bounds == ()
    assert L.verify_candidate(c, live, receipt)


def test_live_producer_no_mutating_authority_imports():
    import ast
    root = Path(__file__).resolve().parents[1]
    for name in ['trader/portfolio/opportunity_live.py', 'trader/portfolio/opportunity_registry.py',
                 'scripts/opportunity_context_shadow.py']:
        imports = [node.module or '' for node in ast.walk(ast.parse((root/name).read_text())) if isinstance(node, ast.ImportFrom)]
        assert not any(x in ('trader.kernel', 'trader.engine.executor', 'trader.engine.risk',
                             'trader.engine.control_fence') for x in imports)


def test_signal_horizon_must_match_frozen_version(live_inputs):
    sig = json.loads(live_inputs['sources'][0].payload_json)['data']
    sig[0]['params']['signal_timeframe'] = '1h'
    with pytest.raises(ValueError, match='HORIZON_DIRECTION_OR_MARKET'):
        L.produce(**{**live_inputs, 'sources': (L.source('signals', sig, 960, 2000), *live_inputs['sources'][1:])})


def test_persisted_context_filename_and_portfolio_projection_are_verified(live_inputs, tmp_path):
    r = L.produce(**live_inputs)
    path = L.persist(r, tmp_path)
    assert L.load(path) == r
    body = json.loads(path.read_text())
    body['candidate_id'] = 'changed'
    path.write_text(canonical(body) + '\n')
    with pytest.raises(ValueError, match='PERSISTED_CONTEXT_ID'):
        L.load(path)
    snap = snapshot()
    snap['positions'][0]['side'] = 'short'
    snap['snapshot_id'] = digest({k: v for k, v in snap.items() if k != 'snapshot_id'})
    with pytest.raises(ValueError, match='PROJECTION_DIFFERS'):
        L.produce(**{**live_inputs, 'sources': (*live_inputs['sources'][:2], L.source('portfolio', snap, 1000, 2000))})
