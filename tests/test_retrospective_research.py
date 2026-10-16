"""Offline discovery through the existing feed, evaluator and ledger contracts."""
import hashlib
import sqlite3

import numpy as np
import pytest

from trader.research import evaluate as ev, job
from trader.research.combo import Combination
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner
from trader.research.vocab import Part
from trader.core.journal import Journal

TF = 14_400_000


@pytest.fixture
def discovery(tmp_path):
    candles = tmp_path / 'candles.db'
    con = sqlite3.connect(candles)
    con.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,taker_buy REAL)')
    symbols = [f'S{i}/USDT' for i in range(16)]
    for i, sym in enumerate(symbols + ['HELD/USDT']):
        rng = np.random.default_rng(i)
        px = 100 * np.exp(np.cumsum(rng.normal(.0005, .005, 800)))
        con.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)',
                        [(sym, '4h', j*TF, float(p),float(p*1.01),float(p*.99),float(p),10,5)
                         for j,p in enumerate(px)])
    con.commit();con.close()
    source = tmp_path / 'preserved.db'
    con = sqlite3.connect(source)
    con.execute('CREATE TABLE research_slices(tf TEXT,cut_ms INTEGER)')
    cut = 600*TF + TF//2
    con.execute('INSERT INTO research_slices VALUES (?,?)',('4h',cut))
    con.commit();con.close()
    cfg = {'research': {'referee': False, 'handoff': False, 'retrospective': {
        'dataset_id': 'synthetic-audited-discovery', 'cut_ms': cut,
        'discovery_cut_source': str(source),
        'discovery_cut_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()},
        'paths': {'candles':str(candles)}}, 'risk': {'real_funding':False}}
    return cfg, symbols


def test_complete_sixteen_symbol_bundle_and_evaluator_are_repeatable(discovery, monkeypatch):
    cfg, symbols = discovery
    from trader.data.feed import DataFeed
    reads=[]
    original=DataFeed.cached_ohlcv
    def bounded(self, symbol, *args, **kw):
        assert symbol != 'HELD/USDT'
        reads.append(symbol)
        return original(self, symbol, *args, **kw)
    monkeypatch.setattr(DataFeed,'cached_ohlcv',bounded)
    b=ev.load_bundle('4h',symbols,cfg,paths=cfg['research']['paths'],heldout_symbols=['HELD/USDT'])
    assert len(b.frames)==16 and len(reads)==16
    assert all(len(df)==600 for df in b.frames.values())
    assert all((df.event_time_ms+TF <= b.cut).all() for df in b.frames.values())
    assert b.heldout_bars == {'a': {'HELD/USDT':800}, 'b':{}}
    assert all('input_identity' in value for value in b.evidence['inputs'].values())
    p=Part('synthetic-ema','state','synthetic-ema','close > ema(20)','close < ema(20)')
    combo=Combination((p,),'4h','trail')
    payload={'tf':'4h','symbols':symbols,'cfg':cfg,'paths':cfg['research']['paths'],
             'requires':['ohlcv'],'combos':[combo.as_dict()],'min_symbols':16,'draws':20,'seed':17}
    one=job.evaluate_job(payload);two=job.evaluate_job(payload)
    assert one['admission']['admitted'] and one['done']==1
    assert one['results']==two['results']
    assert one['results'][0]['evidence']['factory_validated'] is False


def test_cut_source_mismatch_refuses_before_data_read(discovery,monkeypatch):
    cfg,symbols=discovery
    cfg['research']['retrospective']['cut_ms'] += TF
    from trader.data.feed import DataFeed
    monkeypatch.setattr(DataFeed,'cached_ohlcv',lambda *a,**k: pytest.fail('read before cut authentication'))
    with pytest.raises(ValueError,match='preserved_cut_mismatch'):
        ev.load_bundle('4h',symbols,cfg,paths=cfg['research']['paths'])


def test_missing_derivative_dataset_is_explicit(discovery):
    cfg,symbols=discovery
    with pytest.raises(ValueError,match='derivatives dataset path'):
        ev.load_bundle('4h',symbols,cfg,requires=['open_interest'],paths=cfg['research']['paths'])


def test_retrospective_runner_cannot_spend_or_reuse_old_ledger(discovery,tmp_path):
    cfg,_=discovery
    journal=Journal(tmp_path/'outputs.db')
    cfg['research']['referee']=True
    with pytest.raises(ValueError,match='referee_and_handoff_disabled'):
        ResearchRunner(journal,cfg)
    cfg['research']['referee']=False
    ledger=Ledger(journal);ledger.ensure();ledger.record_slices('4h',3,{})
    with pytest.raises(ValueError,match='fresh_isolated_research_ledger'):
        ResearchRunner(journal,cfg)


def test_retrospective_result_is_immutable(tmp_path):
    journal=Journal(tmp_path/'output.db');ledger=Ledger(journal);ledger.ensure()
    result={'hash':'immutable','tf':'4h','geo':'trail','portfolio':{},
            'evidence':{'dataset_id':'a','identity':'first'}}
    ledger.record_result(result,'prune','reason',{})
    ledger.record_result(result,'prune','reason',{})
    with pytest.raises(ValueError,match='immutable_retrospective'):
        ledger.record_result(dict(result,evidence={'dataset_id':'b'}),'prune','reason',{})
    assert ledger.result('immutable')['evidence']==result['evidence']


def test_runner_resumes_only_identical_retrospective_binding(discovery,tmp_path):
    cfg,_=discovery
    journal=Journal(tmp_path/'resume.db')
    one=ResearchRunner(journal,cfg)
    one.ledger.record_slices('4h',cfg['research']['retrospective']['cut_ms'],{})
    two=ResearchRunner(journal,cfg)
    assert two.ledger.slices('4h')['cut_ms']==cfg['research']['retrospective']['cut_ms']
    cfg['research']['retrospective']['dataset_id']='another-dataset'
    with pytest.raises(ValueError,match='ledger_binding_mismatch'):
        ResearchRunner(journal,cfg)


def test_unavailable_real_funding_is_untested_not_empty(discovery):
    cfg,symbols=discovery
    cfg['risk']['real_funding']=True
    b=ev.load_bundle('4h',symbols,cfg,paths=cfg['research']['paths'])
    p=Part('funding-check','state','funding-check','close > ema(20)','close < ema(20)')
    out=ev.evaluate(Combination((p,),'4h','trail'),b,draws=20)
    assert out['verdict']=='untested' and out['trades']==0
    assert 'funding' in str(out['context_gaps'])


@pytest.mark.parametrize('mutation', ['append', 'correct'])
def test_projection_only_change_reopens_deterministic_refusal(discovery, mutation):
    from trader.research import refusal
    cfg, symbols = discovery
    path = cfg['research']['paths']['candles']
    initial = refusal.retrospective_stamp(path, '4h', symbols)
    assert initial is not None
    clock = [0.0]
    memory = refusal.Refusals(lambda: clock[0], stamp_check_s=1)
    slot = memory.slot('4h', 'single', 'min_discovery_symbols', symbols)
    admission = {'admitted':False, 'deterministic':True,
                 'reason':'insufficient_symbol_coverage', 'loaded_symbols':15,
                 'required_symbols':16}
    assert memory.record(slot, 'same-input', initial, admission)
    assert memory.decide(slot, 'same-input', lambda: initial) is not None
    con = sqlite3.connect(path)
    if mutation == 'append':
        con.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)',
                    (symbols[0], '4h', 801*TF,100,101,99,100,10,5))
    else:
        con.execute('UPDATE candles SET volume=11 WHERE symbol=? AND ts=0', (symbols[0],))
    con.commit();con.close()
    clock[0] = 2.0
    changed = refusal.retrospective_stamp(path, '4h', symbols)
    assert changed != initial
    assert memory.decide(slot, 'same-input', lambda: changed) is None


def test_changed_source_refuses_resume_without_replacing_gauges(discovery,tmp_path):
    cfg,symbols=discovery
    journal=Journal(tmp_path/'source-resume.db')
    runner=ResearchRunner(journal,cfg)
    runner.ledger.record_slices('4h',cfg['research']['retrospective']['cut_ms'],{})
    original=runner.ledger.slices('4h')
    con=sqlite3.connect(cfg['research']['paths']['candles'])
    con.execute('UPDATE candles SET volume=12 WHERE symbol=? AND ts=0',(symbols[0],))
    con.commit();con.close()
    with pytest.raises(ValueError,match='ledger_binding_mismatch'):
        ResearchRunner(journal,cfg)
    assert runner.ledger.slices('4h')==original


def test_bound_ledger_measurements_are_immutable_and_idempotent(discovery,tmp_path):
    cfg,_=discovery
    runner=ResearchRunner(Journal(tmp_path/'measurements.db'),cfg)
    ledger=runner.ledger
    ledger.record_gauges('4h',{'rsi(14)':{'n':100,'usable':True}})
    ledger.record_gauges('4h',{'rsi(14)':{'n':100,'usable':True}})
    with pytest.raises(ValueError,match='immutable_retrospective_gauge'):
        ledger.record_gauges('4h',{'rsi(14)':{'n':101,'usable':True}})
    ledger.record_slices('4h',3,{'discovery':{'A':5}})
    ledger.record_slices('4h',3,{'discovery':{'A':5}})
    with pytest.raises(ValueError,match='immutable_retrospective_slice'):
        ledger.record_slices('4h',4,{'discovery':{'A':5}})
    ledger.record_control('4h','ohlcv',{'consistency_p':.5},True)
    ledger.record_control('4h','ohlcv',{'consistency_p':.5},True)
    with pytest.raises(ValueError,match='immutable_retrospective_control'):
        ledger.record_control('4h','ohlcv',{'consistency_p':.6},True)


@pytest.mark.parametrize('closed_bars',[420,499])
def test_original_discovery_bar_threshold_is_preserved(discovery,closed_bars):
    from trader.data.feed import DataFeed
    from trader.research.universe import coverage,DISCOVERY
    cfg,symbols=discovery
    old=symbols[0];sym=DISCOVERY[0]
    con=sqlite3.connect(cfg['research']['paths']['candles'])
    con.execute('UPDATE candles SET symbol=? WHERE symbol=?',(sym,old))
    con.commit();con.close()
    cut=closed_bars*TF + TF//2
    source=cfg['research']['retrospective']['discovery_cut_source']
    con=sqlite3.connect(source);con.execute('UPDATE research_slices SET cut_ms=?',(cut,))
    con.commit();con.close()
    cfg['research']['retrospective']['cut_ms']=cut
    cfg['research']['retrospective']['discovery_cut_source_sha256']=hashlib.sha256(__import__('pathlib').Path(source).read_bytes()).hexdigest()
    feed=DataFeed(db_path=cfg['research']['paths']['candles'],retrospective=cfg['research']['retrospective'])
    assert coverage(['4h'],feed=feed)['4h'][sym]==closed_bars
    b=ev.load_bundle('4h',[sym],cfg,paths=cfg['research']['paths'])
    assert len(b.frames[sym])==closed_bars


def test_short_retrospective_bundle_refuses_before_missing_dependency(discovery):
    cfg, symbols = discovery
    p = Part("ema", "state", "ema", "close > ema(20)", "close < ema(20)")
    out = job.evaluate_job({"tf": "4h", "symbols": symbols[:1], "cfg": cfg,
        "paths": cfg["research"]["paths"], "requires": ["open_interest"],
        "combos": [Combination((p,), "4h", "trail").as_dict()], "min_symbols": 16})
    assert out["admission"]["reason"] == job.INSUFFICIENT_SYMBOL_COVERAGE
    assert out["done"] == 0
