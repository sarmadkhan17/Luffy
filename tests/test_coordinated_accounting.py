"""Synthetic venue accounting, no actual trading performance claims."""
from copy import deepcopy
from datetime import datetime,timezone
import pytest
from tests.test_whole_trade_accounting import source,Venue,seal
from trader.engine import trade_accounting as T, coordinated_accounting as G
from trader.engine.entry_authority import digest
from trader.cognition import outcomes


def component(monkeypatch,*,role='primary',symbol='BTC/USDT',offset=0,base=0,
              strategy_id='strategy',decision_id='decision',version_id='version',spec_hash='spec',cycle_id='cycle'):
    s,v=source(),Venue()
    tid=role+'-trade';venue_symbol=symbol.replace('/','')
    for trade in [s['trade']]+[t for r in s['receipts'] for t in (r['before'],r['after']) if t]:
        trade.update(id=tid,symbol=symbol,strategy_id=strategy_id,decision_id=decision_id)
        if role=='hedge':trade['side']='short'
        for key in ('opened_at','closed_at'):
            if trade[key]:
                trade[key]=datetime.fromtimestamp((outcomes.timestamp(trade[key])+base+offset)/1000,timezone.utc).isoformat()
    for r in s['receipts']:
        r.update(trade_id=tid,observed_ms=r['observed_ms']+base+offset);seal(r)
    s['observed_ms']+=base+offset;seal(s)
    for rows,key in ((v.fills,'time'),(v.funding,'time'),(v.events,'fundingTime')):
        for row in rows:row[key]+=base+offset;row['symbol']=venue_symbol
    if role=='hedge':
        for fill in v.fills:
            fill['side']='SELL' if fill['side']=='BUY' else 'BUY'
            fill['realizedPnl']=-fill['realizedPnl']
    v.fapiPrivateV2GetPositionRisk=lambda _: [dict(symbol=venue_symbol,positionSide='BOTH',positionAmt='0')]
    monkeypatch.setattr(T.time,'time',lambda:8+base/1000)
    capture=T.capture(s,v)
    assert T.replay(capture)['learning_eligible'],capture['assessment']
    binding=dict(logical_id=role,instrument_id='binance_usdm:futures:'+venue_symbol,capability=dict(account_scope='demo-account'))
    group=dict(group_id='economic-group',leg=role)
    identity=dict(status='VERIFIED',strategy_id=strategy_id,version_id=version_id,spec_sha256=spec_hash,
        install_id='install',execution_binding=binding,coordinated=group,
        decision=dict(decision_id=decision_id,cycle_id=cycle_id))
    intent=dict(strategy_identity=deepcopy(identity),symbol=symbol,strategy_id=strategy_id,
                capability=deepcopy(binding['capability']),instrument_id=binding['instrument_id'])
    return dict(capture=capture,identity=identity,entry_request=dict(logical_id=role,intent=intent,
        intent_hash=digest(intent),state='TERMINAL',order_id='1',result=dict(position_id=tid)))


@pytest.fixture
def group(monkeypatch):
    parts=[component(monkeypatch),component(monkeypatch,role='hedge',symbol='ETH/USDT',offset=1200)]
    return assembled(parts)


def assembled(parts):
    members=[dict(trade_id=p['capture']['bookings']['trade']['id'],leg=p['identity']['coordinated']['leg'],
        symbol=p['capture']['bookings']['trade']['symbol'],instrument_id=p['identity']['execution_binding']['instrument_id'],
        entry_identity_sha256=digest(p['identity'])) for p in parts]
    plan=dict(group_id='economic-group',logical_id='economic-close',reason='combined_position_exit',members=members)
    return dict(schema_version=G.VERSION,observed_ms=max(p['capture']['captured_ms'] for p in parts),
        closure=dict(plan=plan,plan_sha256=digest(plan),state='CLOSED'),components=parts)


def test_only_combined_net_result_is_one_economic_outcome(group):
    r=G.verified_outcome(group,9000)
    assert r['actual_execution']['net_pnl']==-10 # +25 primary, -35 hedge; six fees, signed funding -4
    assert outcomes.replay(r)==r
    assert len(G.accounting(group)['fills'])==6
    assert len({f['id'] for f in G.accounting(group)['fills']})==6 # venue fill IDs overlap across instruments
    assert {f['trade_id'] for f in G.accounting(group)['fills']}=={'primary-trade'}


@pytest.mark.parametrize('change',['open','member','identity','request','account','version','shared','pending','early'])
def test_incomplete_or_conflicting_group_is_not_money(group,change):
    part=group['components'][1]
    if change=='open':group['closure']['state']='UNWINDING'
    elif change=='member':group['components'].pop()
    elif change=='identity':part['identity']['coordinated']['group_id']='other'
    elif change=='request':part['entry_request']['result']['position_id']='other'
    elif change in ('account','version'):
        if change=='account':part['identity']['execution_binding']['capability']['account_scope']='other'
        else:part['identity']['version_id']='other'
        group['closure']['plan']['members'][1]['entry_identity_sha256']=digest(part['identity'])
    elif change=='shared':
        instrument=group['components'][0]['identity']['execution_binding']['instrument_id']
        part['identity']['execution_binding']['instrument_id']=instrument
        member=group['closure']['plan']['members'][1];member.update(instrument_id=instrument,entry_identity_sha256=digest(part['identity']))
    elif change=='pending':part['capture']['funding_history']['pages'][0]['rows']=[]
    elif change=='early':group['components'][0]['capture']['bookings']['receipts'][0]['observed_ms']=7000
    group['closure']['plan_sha256']=digest(group['closure']['plan'])
    with pytest.raises((ValueError,KeyError)):
        G.verified_outcome(group,9000)


def test_primary_only_group_accounts_without_inventing_a_hedge(group):
    group['components'].pop();group['closure']['plan']['members'].pop()
    group['closure']['plan_sha256']=digest(group['closure']['plan'])
    assert G.verified_outcome(group,9000)['actual_execution']['net_pnl']==25


from tests.test_decision_sources import producer, exact  # noqa: E402,F401 -- real learning registration


def test_worker_delivers_one_combined_result_once_after_all_components_close(producer,tmp_path,monkeypatch):
    import json,sqlite3
    from trader.learning import capture as C,producers,foundation as L
    from trader.observability import accounting as W
    from trader.engine.entry_authority import canonical
    j,d,_,cfg,_,_,ai,_,version=producer
    opts=dict(base=ai.as_of_ms,strategy_id=version['strategy_id'],decision_id=d.id,
              cycle_id=d.cycle_id,version_id=version['version_id'],spec_hash=version['spec_hash'])
    source=assembled([component(monkeypatch,**opts),component(monkeypatch,role='hedge',symbol='ETH/USDT',offset=1200,**opts)])
    with j._tx() as db:
        for part in source['components']:
            trade=part['capture']['bookings']['trade'];cols=list(trade)+['entry_identity_json']
            db.execute('INSERT INTO trades('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',
                       list(trade.values())+[json.dumps(part['identity'])])
            req=part['entry_request']
            db.execute('INSERT INTO execution_requests(logical_id,decision_id,intent_hash,intent_json,client_order_id,state,order_id,result_json) '
                'VALUES(?,?,?,?,?,?,?,?)',(req['logical_id'],req['logical_id'],req['intent_hash'],canonical(req['intent']),
                    req['logical_id'],req['state'],req['order_id'],json.dumps(req['result'])))
        db.execute('CREATE TABLE coordinated_group_unwinds(group_id TEXT PRIMARY KEY,logical_id TEXT,plan_json TEXT,plan_sha256 TEXT,state TEXT)')
        close=source['closure'];plan=close['plan']
        db.execute('INSERT INTO coordinated_group_unwinds VALUES(?,?,?,?,?)',
            (plan['group_id'],plan['logical_id'],canonical(plan),close['plan_sha256'],'UNWINDING'))
        C.record_action(db,'decision:'+d.id,dict(action='EXECUTED'),ai.as_of_ms+1000)
        C.record_action(db,'decision:'+d.id,dict(ok=True,config_risk_sha256=L.digest(cfg['risk'])),ai.as_of_ms+1,risk=True)
    queue=sqlite3.connect(tmp_path/'queue.db');W.initialize(queue)
    for i,part in enumerate(source['components']):
        path=tmp_path/(str(i)+'.json');path.write_text(json.dumps(part['capture']))
        queue.execute('INSERT INTO attempts(id,trade_id,path,status,started_ms,finished_ms) VALUES(?,?,?,?,?,?)',
            (str(i),part['capture']['bookings']['trade']['id'],str(path),'complete',i,i))
    queue.commit()
    assert W.deliver_completed(queue,j.db_path)['retry']==2
    assert not j.query("SELECT * FROM learning_outcome_captures WHERE outcome_key LIKE 'verified-group:%'")
    with pytest.raises(ValueError,match='coordinated_group_outcome_required'):
        producers.deliver_accounting(j.db_path,source['components'][0]['capture'])
    with j._tx() as db:db.execute("UPDATE coordinated_group_unwinds SET state='CLOSED'")
    monkeypatch.setattr(T.time,'time',lambda:(ai.as_of_ms+310000)/1000)
    report=W.deliver_completed(queue,j.db_path)
    assert report==dict(delivered=1,unbound=0,retry=0),report
    rows=j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'verified-group:%'")
    assert len(rows)==1
    assert not j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'verified-trade:%'")
    raw=producers.materialize(j._conn(),rows[0]['outcome_id'])
    assert raw['authoritative'],[(r['role'],r['reason']) for r in C.manifest(j._conn(),rows[0]['outcome_id'])['dependencies'] if r['required'] and r['status']!='AVAILABLE']
    obs=json.loads(raw['outcome']['observation_json'])
    assert (obs['gross_pnl'],obs['fees'],obs['funding'],obs['net_pnl'],obs['trade_count'],obs['leg_count'])==(0,6,-4,-10,1,2)
    assert W.deliver_completed(queue,j.db_path)['delivered']==0
    assert producers.deliver_group_accounting(j.db_path,source)==rows[0]['outcome_id']
    queue.close()


def test_pending_group_delivery_does_not_starve_an_unrelated_outcome(tmp_path,monkeypatch):
    """Queue fairness only; accounting and journal provenance tested above."""
    import json,sqlite3
    from trader.observability import accounting as W
    from trader.learning import producers
    queue=sqlite3.connect(':memory:');W.initialize(queue)
    for i in range(3):
        path=tmp_path/(str(i)+'.json');path.write_text(json.dumps(dict(grouped=i<2)))
        queue.execute('INSERT INTO attempts(id,trade_id,path,status,started_ms) VALUES(?,?,?,?,?)',
                      (str(i),str(i),str(path),'complete',i))
    queue.commit()
    monkeypatch.setattr(W,'group_source',lambda db,journal,a:(a['grouped'],None))
    monkeypatch.setattr(producers,'deliver_accounting',lambda journal,a:'unrelated-outcome')
    assert W.deliver_completed(queue,'unused')==dict(delivered=0,retry=2,unbound=0)
    assert W.deliver_completed(queue,'unused')==dict(delivered=1,retry=0,unbound=0)
    assert queue.execute('SELECT * FROM learning_deliveries').fetchall()==[('2','unrelated-outcome')]
    queue.close()
