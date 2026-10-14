"""DA-01 exact native identity, frozen-input and historical replay proofs."""
from copy import deepcopy
import gzip
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from trader import attention_admission as A
from trader.core import journal_evidence as E
from trader.core.journal import Journal
from trader.data import broad_crypto as B
from tests.test_hierarchical_admission import CUT, broad_kernel, cfg, policy, offline  # noqa: F401

PERP = 'BTC/USDT:USDT'
DATED = 'BTC/USDT:USDT-270326'


def market(key=PERP, native='BTCUSDT', **fields):
    row = dict(id=native,symbol=key,quote='USDT',spot=False,active=True,
               info=dict(symbol=native,underlyingType='COIN',status='TRADING',
                         contractType='PERPETUAL',onboardDate=CUT-100*86400000))
    for name,value in fields.items():
        if name in row['info']: row['info'][name] = value
        else: row[name] = value
    return row


def ticker(key=PERP, native='BTCUSDT', **fields):
    return dict(symbol=key,info={'symbol':native},last=10,quoteVolume=1e8,
                timestamp=CUT,percentage=5,**fields)


def project(ts, ms, **kwargs):
    details = {}
    rows,excluded = B.project(ts,ms,cut=CUT,blacklist=kwargs.pop('blacklist',set()),
                              stale_ms=300000,source_exclusions=details,**kwargs)
    return rows,excluded,details


@pytest.fixture(scope='module')
def frozen():
    raw = gzip.decompress(Path('tests/fixtures/da01-event2338.json.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest() == '542d63df388e978d0ee991b0a78f3c418425232673be32975b80c5f102dad6b4'
    return json.loads(raw)


def test_frozen_event_2338_restores_only_three_exact_instruments(frozen):
    obs = frozen['observation']; src = obs['source_receipts']
    assert frozen['receipt_id'] == '582e37df84dffa658c2d5c0fb1f1d74929c23312c8e174ec7329dc379bedb653'
    assert obs['source_cut_ms'] == 1791679020317
    assert obs['attention_event']['scan_id'] == 'broad:4812ff2429e4e132655bb05568de7b922352fd326327ea5b48f84b96b754bead'
    assert (len(src['tickers']),len(src['venue_metadata'])) == (789,741)
    assert A.digest([obs['source_cut_ms'],src['tickers'],src['venue_metadata']]) == obs['source_identity']
    # Retained refusal keys are explicit evidence, not independently retained
    # historical config/account inputs. Do not invent missing policy fields.
    kwargs = dict(cut=obs['source_cut_ms'],stale_ms=300000,
                  blacklist={s for s,r in obs['excluded'].items() if r=='deterministic_blacklist'},
                  capability_exclusions={s for s,r in obs['excluded'].items() if r=='account_symbol_ineligible'})
    details = {}
    rows,excluded = B.project(src['tickers'],src['venue_metadata'],source_exclusions=details,**kwargs)
    assert len(obs['rows']) == 439 and len(rows) == 442
    assert set(rows)-set(obs['rows']) == {'BTC/USDT','ETH/USDT','BSB/USDT'}
    assert all(rows[s] == row for s,row in obs['rows'].items())
    for base in ('BTC','ETH','BSB'):
        assert rows[base+'/USDT']['instrument_id'] == 'binance_usdm:futures:'+base+'USDT'
        assert base+'/USDT' not in excluded
    assert details[DATED]['reason'] == 'venue_not_trading'
    # BSB's dated metadata was retained, but there was no dated BSB ticker.
    # Its perpetual must not acquire a fabricated sibling refusal/observation.
    assert src['venue_metadata']['BSB/USDT:USDT-260625']['info']['contractType'] == 'NEXT_WEEK'
    assert 'BSB/USDT:USDT-260625' not in src['tickers'] and 'BSB/USDT:USDT-260625' not in details
    permuted = B.project(dict(reversed(list(src['tickers'].items()))),
                         dict(reversed(list(src['venue_metadata'].items()))),**kwargs)
    assert permuted == (rows,excluded)
    # Qualify repaired identities only: unavailable historical cached bars for
    # the restored symbols are not replaced with invented salience scores.
    repaired = dict(obs,schema=B.SCHEMA,rows=rows,
                    source_identity=A.digest([B.SCHEMA,obs['source_cut_ms'],src['tickers'],src['venue_metadata']]))
    B.verify_identity_projection(repaired)


@pytest.mark.parametrize('perp_active,dated_active', [(True,False),(False,True),(True,True),(False,False)])
@pytest.mark.parametrize('contract', ['NEXT_QUARTER','CURRENT_WEEK','NEXT_WEEK','CURRENT_QUARTER DELIVERING'])
def test_sibling_contracts_use_their_own_eligibility(perp_active,dated_active,contract):
    ts = {PERP:ticker(),DATED:ticker(DATED,'BTCUSDT_270326')}
    ms = {PERP:market(active=perp_active),DATED:market(DATED,'BTCUSDT_270326',
           active=dated_active,contractType=contract,status='TRADING' if dated_active else 'PENDING_TRADING')}
    expected = None
    for keys in itertools.permutations(ts):
        for market_keys in itertools.permutations(ms):
            result = project({k:ts[k] for k in keys},{k:ms[k] for k in market_keys})
            if expected is None: expected = result
            assert result == expected
    rows,excluded,details = expected
    assert set(rows) == ({'BTC/USDT'} if perp_active else set())
    if perp_active:
        assert rows['BTC/USDT']['ticker']['info']['symbol'] == 'BTCUSDT'
        assert 'BTC/USDT' not in excluded
    else:
        assert details[PERP]['reason'] == 'venue_not_trading'
    assert details[DATED]['reason'] == ('unsupported_instrument' if dated_active else 'venue_not_trading')


@pytest.mark.parametrize('field,value,reason', [
    ('active',False,'venue_not_trading'),('status','PENDING_TRADING','venue_not_trading'),
    ('contractType','NEXT_WEEK','unsupported_instrument'),('quote','USDC','unsupported_instrument'),
    ('spot',True,'unsupported_instrument'),('underlyingType',None,'non_crypto_or_unknown_classification')])
def test_existing_eligibility_reasons_preserved(field,value,reason):
    rows,excluded,details = project({PERP:ticker()},{PERP:market(**{field:value})})
    assert not rows and excluded == {'BTC/USDT':reason} and details[PERP]['reason'] == reason


def test_blacklist_and_account_masks_preserve_precedence():
    ms = {PERP:market(active=False)}
    assert project({PERP:ticker()},ms,blacklist={'BTC/USDT'},capability_exclusions=['BTC/USDT'])[1] == {'BTC/USDT':'deterministic_blacklist'}
    assert project({PERP:ticker()},ms,capability_exclusions=['BTC/USDT'])[1] == {'BTC/USDT':'account_symbol_ineligible'}


@pytest.mark.parametrize('where', ['market','ticker'])
@pytest.mark.parametrize('conflict', [False,True])
def test_duplicate_aliases_refuse_without_winner(where,conflict):
    ts,ms = {PERP:ticker()},{PERP:market()}
    records = ms if where=='market' else ts
    records['BTC/USDT'] = deepcopy(records[PERP])
    if conflict: records['BTC/USDT']['active' if where=='market' else 'last'] = False
    expected = project(ts,ms)
    assert not expected[0]
    assert expected[2][PERP]['reason'] == 'ambiguous_crypto_identity'
    assert project(dict(reversed(list(ts.items()))),dict(reversed(list(ms.items())))) == expected


def test_missing_exact_metadata_never_borrows_dated_sibling():
    rows,_,details = project({PERP:ticker()},{DATED:market(DATED,'BTCUSDT_270326',contractType='NEXT_QUARTER')})
    assert not rows and details[PERP]['reason'] == 'venue_metadata_missing'
    assert project({}, {PERP:market()}) == ({},{},{})


def test_two_eligible_native_ids_cannot_share_an_accepted_label():
    alias = 'BTC/USDT:OTHER'
    ts = {PERP:ticker(),alias:ticker(alias,'OTHERBTCUSDT')}
    ms = {PERP:market(),alias:market(alias,'OTHERBTCUSDT')}
    for keys in itertools.permutations(ts):
        with pytest.raises(ValueError,match='duplicate_crypto_identity'):
            project({k:ts[k] for k in keys},ms)


@pytest.mark.parametrize('value', [None,'',True,123,{},[], ' BTCUSDT','BTC USDT','BTC:USDT'])
@pytest.mark.parametrize('where', ['ticker','market_id','market_raw_id'])
def test_malformed_native_identity_is_explicitly_refused(value,where):
    ts,ms = {PERP:ticker()},{PERP:market()}
    if where=='ticker': ts[PERP]['info']['symbol'] = value
    elif where=='market_id': ms[PERP]['id'] = value
    else: ms[PERP]['info']['symbol'] = value
    rows,_,details = project(ts,ms)
    assert not rows and details[PERP]['reason'] == 'malformed_crypto_identity'


@pytest.mark.parametrize('where', ['metadata_native','metadata_unified','ticker_native','ticker_unified','ticker_key'])
def test_conflicting_identity_cannot_bind_another_contract(where):
    ts,ms = {PERP:ticker()},{PERP:market()}
    if where=='metadata_native': ms[PERP]['info']['symbol'] = 'ETHUSDT'
    elif where=='metadata_unified': ms[PERP]['symbol'] = 'ETH/USDT:USDT'
    elif where=='ticker_native': ts[PERP]['info']['symbol'] = 'ETHUSDT'
    elif where=='ticker_unified': ts[PERP]['symbol'] = 'ETH/USDT:USDT'
    else: ts = {'ETH/USDT:USDT':ts[PERP]}
    rows,_,details = project(ts,ms)
    assert not rows and next(iter(details.values()))['reason'] in {'conflicting_crypto_identity','malformed_crypto_identity'}


@pytest.mark.parametrize('key', [None,False,123,'',' BTC/USDT:USDT'])
def test_malformed_source_key_fails_explicitly(key):
    with pytest.raises(ValueError,match='malformed_crypto_identity'):
        project({key:ticker()},{PERP:market()})


@pytest.mark.parametrize('event,valid', [(CUT,True),(CUT-300000,True),(float(CUT),True),
    (CUT+1,False),(CUT-300001,False),(True,False),(str(CUT),False),
    (float('nan'),False),(float('inf'),False),(None,True)])
def test_provider_timestamp_rules_are_unchanged(event,valid):
    t = ticker(); t['timestamp'] = event
    rows,excluded,_ = project({PERP:t},{PERP:market()})
    assert bool(rows) == valid
    if valid:
        row = rows['BTC/USDT']; assert row['event_time_ms'] == event and row['available_at_ms'] == CUT
        assert ('provider_event_time_unknown' in row['missing_reasons']) == (event is None)
    else: assert excluded == {'BTC/USDT':'stale_or_invalid_ticker_clock'}


def new_observation(tmp_path,cfg,monkeypatch):
    monkeypatch.setattr(B.time,'time',lambda:CUT/1000)
    result = B.observe(broad_kernel(tmp_path,cfg,n=3),[])[0]
    assert result['schema'] == B.SCHEMA == 'broad-crypto.v2'
    return result


@pytest.mark.parametrize('mutation', ['instrument','metadata','ticker','event_clock','availability',
    'raw_ticker','duplicate_alias','missing_sources','source_hash','future_ticker','stale_ticker'])
def test_new_receipt_verifies_exact_identity_even_after_outer_rehash(tmp_path,cfg,policy,monkeypatch,mutation):
    obs = new_observation(tmp_path,cfg,monkeypatch)
    receipt = A.admit(obs,policy,A.initial_state())
    bad = deepcopy(receipt); o = bad['observation']; sym = sorted(o['rows'])[0]; row = o['rows'][sym]
    if mutation=='instrument': row['instrument_id'] = 'binance_usdm:futures:ETHUSDT'
    elif mutation=='metadata': row['metadata']['symbol'] = 'ETHUSDT'
    elif mutation=='ticker': row['ticker']['info']['symbol'] = 'ETHUSDT'
    elif mutation=='event_clock': row['event_time_ms'] += 1
    elif mutation=='availability': row['available_at_ms'] -= 1
    elif mutation=='raw_ticker':
        o['source_receipts']['tickers'][sym]['info']['symbol'] = 'ETHUSDT'
    elif mutation=='duplicate_alias':
        o['source_receipts']['tickers']['ALIAS/USDT'] = deepcopy(o['source_receipts']['tickers'][sym])
    elif mutation=='missing_sources': del o['source_receipts']
    elif mutation in ('future_ticker','stale_ticker'):
        event = CUT+1 if mutation=='future_ticker' else CUT-300001
        o['source_receipts']['tickers'][sym]['timestamp'] = row['event_time_ms'] = event
    else: o['source_identity'] = 'forged'
    if mutation in ('raw_ticker','duplicate_alias','future_ticker','stale_ticker'):
        src = o['source_receipts']; o['source_identity'] = A.digest([B.SCHEMA,CUT,src['tickers'],src['venue_metadata']])
    bad['receipt_id'] = A.digest({k:v for k,v in bad.items() if k!='receipt_id'})
    with pytest.raises(ValueError,match='admission_crypto_identity_invalid'):
        A.verify_receipt(bad)


def test_historical_frozen_receipt_does_not_invoke_repaired_semantics(frozen,monkeypatch):
    monkeypatch.setattr(B,'verify_identity_projection',lambda _:pytest.fail('v1 must not be reinterpreted'))
    before = A.canonical(frozen)
    assert A.canonical(A.verify_receipt(json.loads(before))) == before


def test_new_schema_preserves_ranking_policy_and_prior_state_for_same_inputs(tmp_path,cfg,policy,monkeypatch):
    obs = new_observation(tmp_path,cfg,monkeypatch)
    legacy = deepcopy(obs); legacy['schema'] = 'broad-crypto.v1'
    src = legacy['source_receipts']; legacy['source_identity'] = A.digest([CUT,src['tickers'],src['venue_metadata']])
    state = A.initial_state()
    for _ in range(3):
        new,old = A.admit(obs,policy,state),A.admit(legacy,policy,state)
        for field in ('policy_version','policy','roles','rows','admitted_symbols','next_state',
                      'tie_break_order','reserved_unused','required_deferred','capacity_used'):
            assert new[field] == old[field]
        state = new['next_state']


def test_v1_to_v2_persistence_preserves_frozen_prior_rotation(frozen,tmp_path,cfg,policy,monkeypatch):
    journal = Journal(tmp_path/'history.db')
    with journal._tx() as db:
        detail = E.store(db,frozen)
        db.execute('INSERT INTO state_kv(key,value) VALUES(?,?)',(A.RECEIPT_KEY,detail))
        db.execute('INSERT INTO state_kv(key,value) VALUES(?,?)',(A.STATE_KEY,A.canonical(frozen['next_state'])))
    assert A.canonical(A.latest(journal)) == A.canonical(frozen)
    obs = new_observation(tmp_path,cfg,monkeypatch)
    new = A.persist(journal,obs,policy)
    assert new['prior_state'] == frozen['next_state']
    assert new['next_state']['generation'] == frozen['next_state']['generation']+1
    reopened = Journal(tmp_path/'history.db')
    assert A.canonical(A.latest(reopened)) == A.canonical(new)
    assert A.verify_receipt(frozen)['receipt_id'] == frozen['receipt_id']


@pytest.mark.parametrize('version', ['v1', 'v2'])
def test_fresh_process_replays_persisted_receipt(version,frozen,tmp_path,cfg,policy,monkeypatch):
    journal = Journal(tmp_path / (version + '.db'))
    if version == 'v1':
        with journal._tx() as db:
            detail = E.store(db, frozen)
            db.execute('INSERT INTO state_kv(key,value) VALUES(?,?)', (A.RECEIPT_KEY, detail))
            db.execute('INSERT INTO state_kv(key,value) VALUES(?,?)',
                       (A.STATE_KEY, A.canonical(frozen['next_state'])))
        expected = frozen
    else:
        expected = A.persist(journal, new_observation(tmp_path, cfg, monkeypatch), policy)
    source = (
        'from trader import attention_admission as A\n'
        'from trader.core.journal import Journal\n'
        'import sys\n'
        'receipt = A.latest(Journal(sys.argv[1]))\n'
        'assert A.verify_receipt(receipt)["receipt_id"] == receipt["receipt_id"]\n'
        'print(receipt["observation"]["schema"], receipt["receipt_id"])\n'
    )
    result = subprocess.run(
        [sys.executable, '-B', '-c', source, str(journal.db_path)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
        text=True, capture_output=True, check=True)
    assert result.stdout.strip() == version.replace('v', 'broad-crypto.v') + ' ' + expected['receipt_id']
