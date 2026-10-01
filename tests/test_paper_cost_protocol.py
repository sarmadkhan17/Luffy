"""Protocol fixtures are authoritative TEST-ONLY; no production cost model."""
import copy
import json
import sqlite3

import pytest

from trader.engine import paper_cost_evidence as C
from trader.strategy import factory_handoff as F
from tests.test_exit_semantics_probation_binding import prepared, assess
from tests.paper_cost_evidence_fixture import freeze_complete, register_test_cost_evidence, test_sources as sources_for
from tests.test_strategy_factory_handoff import T0, DAY


def context(tmp_path):
    j,cfg,v = prepared(tmp_path)
    t=j.query("SELECT * FROM trades WHERE id='t0'")[0]
    inst=F.verify_install(j,v,current=False)
    return j,cfg,v,t,inst


def read(j,t,v,inst):
    return C.verify(j,t,v,inst)


def test_unknown_never_zero_and_gross_only_blocks(tmp_path):
    j,cfg,v,t,inst=context(tmp_path)
    with j._tx() as db:
        body=C.finalize(db,t,v,inst)
    for dim in ('commission','slippage','funding'):
        assert body['dimensions'][dim]['status']=='UNAVAILABLE'
        assert body['dimensions'][dim]['amount'] is None
    assert body['dimensions']['commission']['legs']['entry']['reason']=='NO_AUTHORITATIVE_COMMISSION_SOURCE'
    assert body['net_pnl'] is None and body['net_pnl_status']=='UNAVAILABLE'
    assert body['diagnostics']['entry']['qualification']=='NOT_VALIDATED_EXECUTION_COST'
    result=assess(j,cfg,v)
    assert result['status']==F.P_COST_INCOMPLETE and result['request_id'] is None
    receipt=F._load(j.query('SELECT * FROM strategy_probation_receipts')[0])
    assert len(receipt['trades'])==15 and receipt['gross_stats']['wins']==9
    assert receipt['stats'] is None


def test_structural_spot_nonfunding_and_unknown_market(tmp_path):
    j,cfg,v,t,inst=context(tmp_path)
    for market, status in (('spot','NOT_APPLICABLE'),(None,'UNAVAILABLE'),('unknown','UNAVAILABLE')):
        bound=C.binding({**t,'market_type':market},v,inst)
        with j._tx() as db:
            C.ensure(db)
            body=C.build(db,bound)
        assert body['dimensions']['funding']['status']==status
        assert body['net_pnl'] is None


@pytest.mark.parametrize('events', [False,True])
def test_proven_funding_interval(tmp_path,monkeypatch,events):
    j,cfg,v,t,inst=context(tmp_path)
    register_test_cost_evidence(monkeypatch)
    bound=C.binding(t,v,inst)
    carry=sources_for(bound)[2]
    if not events:
        carry['events']=[]
    with j._tx() as db:
        C.ensure(db); C.freeze_source(db,carry)
        result=C.funding_cost(db,bound,carry['source_id'])
    assert result['status']==('ESTABLISHED' if events else 'NOT_APPLICABLE')
    assert result['amount']==(pytest.approx(.03) if events else None)


@pytest.mark.parametrize('mutation', ['timestamp','instrument','quantity','coverage','basis','boundary'])
def test_wrong_funding_evidence_refused(tmp_path,monkeypatch,mutation):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    bound=C.binding(t,v,inst);carry=sources_for(bound)[2]
    if mutation=='timestamp':carry['events'][0]['timestamp_ms']=bound['exit']['time_ms']+1
    if mutation=='boundary':carry['events'][0]['timestamp_ms']=bound['entry']['time_ms']
    if mutation=='instrument':carry['events'][0]['instrument']='OTHER/USDT'
    if mutation=='quantity':carry['events'][0]['quantity']+=1
    if mutation=='coverage':carry['interval_complete']=False
    if mutation=='basis':carry['events'][0]['basis_provenance']=None
    with j._tx() as db:
        C.ensure(db);C.freeze_source(db,carry)
        with pytest.raises(ValueError):C.funding_cost(db,bound,carry['source_id'])


def test_complete_receipt_both_legs_and_net_pf(tmp_path,monkeypatch):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    body=freeze_complete(j,t,v)
    assert body['known_costs']==pytest.approx(.06)
    assert body['net_pnl']==pytest.approx(t['realized_pnl']-.06)
    for kind in ('commission','slippage'):
        assert all(x['status']=='ESTABLISHED' for x in body['dimensions'][kind]['legs'].values())
    assert read(j,t,v,inst)['net_pnl']==body['net_pnl']
    result=assess(j,cfg,v);assert result['status']==F.P_SATISFIED
    receipt=F._load(j.query('SELECT * FROM strategy_probation_receipts')[0])
    assert receipt['stats']['profit_factor']==pytest.approx(9*(10-.06)/(6*(5+.06)))
    assert receipt['stats']['winrate_basis']=='NET'
    assert receipt['policy']['min_trades']==15 and receipt['policy']['min_winrate']==.4 and receipt['policy']['min_profit_factor']==1.15
    F.record_owner_decision(j,cfg,result['request_id'],'APPROVED',actor='operator',decided_at_ms=T0+31*DAY)
    assert not F.eligible_for_first_live(j,v['version_id'],cfg=cfg,available_inputs={'ohlcv'}).eligible
    assert F.live_entry_block(j,v['strategy_id']) is not None


@pytest.mark.parametrize('field', ['market_type','amount','entry_price','exit_price','reference_price','exit_reference_price','id','version_id','install_id','side','opened_at','closed_at'])
def test_changed_trade_or_wrong_version_refused(tmp_path,monkeypatch,field):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch);freeze_complete(j,t,v)
    changed=copy.deepcopy(t)
    changed[field]=changed[field]+1 if isinstance(changed.get(field),(int,float)) else 'wrong'
    with pytest.raises((ValueError,KeyError)):read(j,changed,v,inst)


@pytest.mark.parametrize('table', [C.TABLE,C.SOURCES])
@pytest.mark.parametrize('action', ['UPDATE','DELETE','REPLACE'])
def test_sql_finalized_immutability(tmp_path,monkeypatch,table,action):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch);freeze_complete(j,t,v)
    key='trade_id' if table==C.TABLE else 'source_id'
    with pytest.raises(sqlite3.IntegrityError):
        with j._tx() as db:
            if action=='UPDATE':db.execute(f"UPDATE {table} SET canonical_json='tampered'")
            elif action=='DELETE':db.execute(f'DELETE FROM {table}')
            else:
                row=dict(db.execute(f'SELECT * FROM {table} LIMIT 1').fetchone())
                db.execute(f'INSERT OR REPLACE INTO {table} VALUES(?,?,?)',(row[key],'tampered','bad'))


@pytest.mark.parametrize('part', ['receipt','rate','funding_event'])
@pytest.mark.parametrize('rehash', [False,True])
def test_tamper_replay_and_approval_fail_closed(tmp_path,monkeypatch,part,rehash):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    result=assess(j,cfg,v);assert result['status']==F.P_SATISFIED
    table=C.TABLE if part=='receipt' else C.SOURCES
    key='trade_id' if part=='receipt' else 'source_id'
    ident=t['id'] if part=='receipt' else 'TEST-ONLY:t0:'+('commission' if part=='rate' else 'funding')
    row=j.query(f'SELECT * FROM {table} WHERE {key}=?',(ident,))[0]
    body=json.loads(row['canonical_json'])
    if part=='receipt':body['net_pnl']+=1
    elif part=='rate':body['entry']['rate']+=.1
    else:body['events'][0]['timestamp_ms']=body['binding']['exit']['time_ms']+1
    text=C.canonical(body)
    # Simulate corruption outside supported writes; separate tests enforce SQL.
    with j._tx() as db:
        db.execute(f'DROP TRIGGER {table}_update_immutable')
        db.execute(f'UPDATE {table} SET canonical_json=?,canonical_sha256=? WHERE {key}=?',
                   (text,C.digest(text) if rehash else row['canonical_sha256'],ident))
    with pytest.raises(ValueError):read(j,t,v,inst)
    with pytest.raises(F.HandoffRefused,match='probation_evidence_changed'):
        F.record_owner_decision(j,cfg,result['request_id'],'APPROVED',actor='operator',decided_at_ms=T0+31*DAY)
    assert not F.eligible_for_first_live(j,v['version_id'],cfg=cfg).eligible


def test_missing_source_or_receipt_blocks_previously_satisfied(tmp_path,monkeypatch):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    result=assess(j,cfg,v)
    from tests.paper_cost_evidence_fixture import _REAL_READER
    monkeypatch.setattr(F, '_paper_cost_evidence', _REAL_READER)
    with j._tx() as db:db.execute(f'ALTER TABLE {C.TABLE} RENAME TO missing_cost_receipts')
    with pytest.raises(F.HandoffRefused,match='probation_evidence_changed'):
        F.record_owner_decision(j,cfg,result['request_id'],'APPROVED',actor='operator',decided_at_ms=T0+31*DAY)


def test_unregistered_source_and_legacy_are_never_reconstructed(tmp_path):
    j,cfg,v,t,inst=context(tmp_path)
    bound=C.binding(t,v,inst)
    with j._tx() as db:
        with pytest.raises(ValueError,match='authority'):C.freeze_source(db,sources_for(bound)[0])
    with pytest.raises(ValueError,match='missing'):read(j,t,v,inst)
    assert F._paper_cost_evidence(j,t,v)['net_pnl_status']=='UNAVAILABLE'


def test_ambiguous_policy_blocks_economic_satisfaction(tmp_path,monkeypatch):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    monkeypatch.setitem(C.WIN_RATE_POLICY,'basis','UNSPECIFIED')
    result=assess(j,cfg,v)
    assert result['status']=='AMBIGUOUS_WIN_RATE_POLICY' and result['request_id'] is None


@pytest.mark.parametrize('kind', ['commission','slippage'])
def test_entry_only_costs_cannot_establish_economics(tmp_path,monkeypatch,kind):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    bound=C.binding(t,v,inst)
    source=next(s for s in sources_for(bound) if s['kind']==kind)
    source.pop('exit')
    with j._tx() as db:
        C.ensure(db);C.freeze_source(db,source)
        with pytest.raises(KeyError):
            C.build(db,bound,{kind:source['source_id']})


def test_unknown_maker_taker_is_never_inferred(tmp_path,monkeypatch):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    bound=C.binding(t,v,inst);source=sources_for(bound)[0]
    source['entry']['classification']='UNKNOWN'
    with j._tx() as db:
        C.ensure(db);C.freeze_source(db,source)
        with pytest.raises(ValueError,match='classification'):
            C.build(db,bound,{'commission':source['source_id']})


def test_depth_and_simulated_difference_are_not_validated_costs(tmp_path):
    j,cfg,v,t,inst=context(tmp_path)
    bound=C.binding(t,v,inst);bound['entry']['reference']-=1
    raw=sources_for(bound)[1]
    raw['method']='depth_snapshot_unvalidated'
    raw['provenance']={'source':'depth','quantity':bound['entry']['quantity']}
    with j._tx() as db:
        C.ensure(db)
        with pytest.raises(ValueError,match='authority'):C.freeze_source(db,raw)
        body=C.build(db,bound)
    assert body['diagnostics']['entry']['difference']==1
    assert body['dimensions']['slippage']['status']=='UNAVAILABLE'
    assert body['known_costs'] is None and body['net_pnl'] is None


def test_finalized_unknown_receipt_cannot_be_upgraded_by_later_rates(tmp_path,monkeypatch):
    j,cfg,v,t,inst=context(tmp_path)
    with j._tx() as db:C.finalize(db,t,v,inst)
    register_test_cost_evidence(monkeypatch)
    with pytest.raises(ValueError,match='conflict'):freeze_complete(j,t,v)
    assert read(j,t,v,inst)['net_pnl_status']=='UNAVAILABLE'


def test_simulated_reference_difference_is_not_double_counted(tmp_path,monkeypatch):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    t={**t, 'reference_price':t['entry_price']-1}
    bound=C.binding(t,v,inst);sources=sources_for(bound)
    with j._tx() as db:
        C.ensure(db)
        for source in sources:C.freeze_source(db,source)
        body=C.build(db,bound,{s['kind']:s['source_id'] for s in sources})
    assert body['diagnostics']['entry']['difference']==1
    assert body['dimensions']['slippage']['amount']==pytest.approx(.02)
    assert body['net_pnl']==pytest.approx(body['gross_pnl']-.06)


def test_commission_uses_both_executable_notionals_and_corrected_net_pf(tmp_path,monkeypatch):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    assert t['amount']==1 and t['entry_price']==100 and t['exit_price']==110
    fee,execution,_=sources_for(C.binding(t,v,inst))
    body=freeze_complete(j,t,v)
    entry=body['dimensions']['commission']['legs']['entry']
    exit=body['dimensions']['commission']['legs']['exit']
    assert execution['entry']['executable_price']==100.01
    assert execution['exit']['executable_price']==109.99
    assert entry['inputs']['notional']==100.01
    assert exit['inputs']['notional']==109.99
    entry_fee=100.01*fee['entry']['rate']
    exit_fee=109.99*fee['exit']['rate']
    assert entry['amount']==entry_fee
    assert exit['amount']==exit_fee
    assert body['dimensions']['commission']['amount']==entry_fee+exit_fee
    for leg in ('entry','exit'):
        inputs=body['dimensions']['commission']['legs'][leg]['inputs']
        assert inputs['quantity']==1
        assert inputs['executable_price_evidence']==body['dimensions']['slippage']['legs'][leg]['provenance']
        assert inputs['rate']==fee[leg]['rate'] and inputs['classification']=='taker'
    expected_net=10-(entry_fee+exit_fee+.02+.03)
    assert body['net_pnl']==pytest.approx(expected_net,rel=1e-14,abs=1e-14)
    assert read(j,t,v,inst)=={**body,'receipt_sha256':C.digest(C.canonical(body))}
    result=assess(j,cfg,v)
    receipt=F._load(j.query('SELECT * FROM strategy_probation_receipts')[0])
    assert result['status']==F.P_SATISFIED
    expected=[]
    for trade in j.query("SELECT * FROM trades WHERE status='closed' ORDER BY opened_at,id"):
        fee,execution,_=sources_for(C.binding(trade,v,inst))
        quantity=trade['amount']
        commission=sum(execution[leg]['executable_price']*quantity*fee[leg]['rate']
                       for leg in ('entry','exit'))
        # Independent achievable-fill P&L, without subtracting paper-price
        # slippage again. All rates/prices come from the existing fixture.
        net=(execution['exit']['executable_price']-execution['entry']['executable_price'])*quantity-commission-.03
        expected.append(net)
    expected_pf=sum(x for x in expected if x>0)/sum(-x for x in expected if x<=0)
    assert receipt['stats']['pnl']==pytest.approx(sum(expected),rel=1e-14,abs=1e-14)
    assert receipt['stats']['profit_factor']==pytest.approx(expected_pf,rel=1e-14,abs=1e-14)
    assert receipt['stats']['profit_factor'] != 9*(10-.06)/(6*(5+.06))


def test_known_rates_without_executable_prices_never_use_paper_notionals(tmp_path,monkeypatch):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    for trade in j.query("SELECT * FROM trades WHERE status='closed' ORDER BY id"):
        fee,_,funding=sources_for(C.binding(trade,v,inst))
        with j._tx() as db:
            C.ensure(db)
            C.freeze_source(db,fee);C.freeze_source(db,funding)
            body=C.finalize(db,trade,v,inst,{'commission':fee['source_id'],'funding':funding['source_id']})
        assert body['dimensions']['commission']['status']=='UNAVAILABLE'
        for leg in ('entry','exit'):
            item=body['dimensions']['commission']['legs'][leg]
            assert item['status']=='UNAVAILABLE' and item['amount'] is None
            assert item['reason']=='EXECUTABLE_COMMISSION_NOTIONAL_UNAVAILABLE'
            assert item['inputs']['rate']==fee[leg]['rate']
            assert item['inputs']['executable_price'] is None and item['inputs']['notional'] is None
        assert body['net_pnl_status']=='UNAVAILABLE' and body['net_pnl'] is None
        assert read(j,trade,v,inst)['net_pnl_status']=='UNAVAILABLE'
    result=assess(j,cfg,v)
    assert result['status']==F.P_COST_INCOMPLETE and result['request_id'] is None
    assert F.approval_request(j,v['version_id']) is None
    assert not F.eligible_for_first_live(j,v['version_id'],cfg=cfg).eligible
    assert F.live_entry_block(j,v['strategy_id']) is not None


@pytest.mark.parametrize('leg', ['entry','exit'])
@pytest.mark.parametrize('field', ['executable_price','quantity','notional','rate','amount','executable_price_evidence'])
def test_commission_basis_rehashed_tampering_blocks_replay_and_approval(tmp_path,monkeypatch,leg,field):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    result=assess(j,cfg,v)
    row=j.query(f"SELECT * FROM {C.TABLE} WHERE trade_id='t0'")[0]
    body=json.loads(row['canonical_json'])
    item=body['dimensions']['commission']['legs'][leg]
    if field=='amount':
        item['amount']+=1
    elif field=='executable_price_evidence':
        item['inputs'][field]['sha256']='wrong'
    else:
        item['inputs'][field]+=1
    text=C.canonical(body)
    with j._tx() as db:
        db.execute(f'DROP TRIGGER {C.TABLE}_update_immutable')
        db.execute(f"UPDATE {C.TABLE} SET canonical_json=?,canonical_sha256=? WHERE trade_id='t0'",
                   (text,C.digest(text)))
    with pytest.raises(ValueError,match='replay'):read(j,t,v,inst)
    with pytest.raises(F.HandoffRefused,match='probation_evidence_changed'):
        F.record_owner_decision(j,cfg,result['request_id'],'APPROVED',actor='operator',decided_at_ms=T0+31*DAY)
    assert not F.eligible_for_first_live(j,v['version_id'],cfg=cfg).eligible


@pytest.mark.parametrize('leg', ['entry','exit'])
def test_changed_executable_source_invalidates_commission_receipt(tmp_path,monkeypatch,leg):
    j,cfg,v,t,inst=context(tmp_path);register_test_cost_evidence(monkeypatch)
    freeze_complete(j,t,v)
    source_id='TEST-ONLY:t0:slippage'
    row=j.query(f'SELECT * FROM {C.SOURCES} WHERE source_id=?',(source_id,))[0]
    source=json.loads(row['canonical_json']);source[leg]['executable_price']+=1
    text=C.canonical(source)
    with j._tx() as db:
        db.execute(f'DROP TRIGGER {C.SOURCES}_update_immutable')
        db.execute(f'UPDATE {C.SOURCES} SET canonical_json=?,canonical_sha256=? WHERE source_id=?',
                   (text,C.digest(text),source_id))
    with pytest.raises(ValueError,match='replay'):read(j,t,v,inst)
