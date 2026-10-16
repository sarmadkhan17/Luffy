"""SG-05: synthetic chronology and frozen-result compatibility, no providers."""
from copy import deepcopy
from itertools import permutations
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pytest

from trader.strategy.portfolio_evidence import (
    ACCOUNTING_VERSION, AccountingMismatch, Fill, RESEARCH_CLOCK,
    accounting_provenance, portfolio_curve, require_compatible_accounting,
    require_current_accounting,
)
from trader.research import growth, portfolio_null as pn, referee
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner
from trader.core.journal import Journal
from trader.strategy.spec import ExitSpec
from trader.strategy.vector_backtest import simulate


@pytest.mark.parametrize("fills,final,dd", [
    ([Fill(0,10,1,'A'), Fill(1,5,-1,'B')], 1000, 10),
    ([Fill(0,10,1,'A'), Fill(1,5,1,'B')], 1200, 0),
    ([Fill(0,10,-1,'A'), Fill(1,5,1,'B')], 1000, 100/1100*100),
    ([Fill(0,10,1,'A'), Fill(1,9,-1,'B'), Fill(2,3,-1,'C')], 900, 20),
    ([Fill(0,10,1,'A'), Fill(1,3,-1,'B'), Fill(4,6,1,'C')], 1090, 10),
    ([Fill(0,10,1,'A'), Fill(1,10,-1,'B')], 1000, 0),
])
def test_overlap_chronology(fills, final, dd):
    for offered in permutations(fills):
        got = portfolio_curve(list(offered), 1000, 10, 3)
        assert got['final'] == pytest.approx(final)
        assert got['max_dd_pct'] == pytest.approx(dd)
        assert got['order'] == [f.symbol for f in fills]


def test_exit_before_same_timestamp_entry_sizes_from_settled_equity():
    got = portfolio_curve([Fill(0,5,1,'A'), Fill(5,7,-1,'B')],1000,10,1)
    assert got['final'] == 990 and got['taken'] == 2
    assert got['max_dd_pct'] == 10


def test_simultaneous_entries_do_not_size_from_each_others_outcomes():
    fills = [Fill(0,10,1,f'S{i}') for i in reversed(range(10))]
    got = portfolio_curve(fills,1000,10,4)
    assert got['final'] == 1400
    assert got['taken'] == 4 and got['order'] == ['S0','S1','S2','S3']


def test_pending_exit_settles_after_last_entry_and_refused_fill_never_books():
    got = portfolio_curve([Fill(0,10,1,'A'),Fill(1,5,100,'B')],1000,10,1)
    assert got['final'] == 1100 and got['order'] == ['A']


def test_simultaneous_exits_settle_before_new_entries_as_one_batch():
    got = portfolio_curve([Fill(0,5,1,'A'),Fill(0,5,-1,'B'),
                           Fill(5,8,1,'C')],1000,10,2)
    assert got['final'] == 1100 and got['max_dd_pct'] == 0


def test_loss_floor_does_not_resize_an_already_accepted_winner():
    got = portfolio_curve([Fill(0,5,-20,'A'),Fill(1,10,1,'B')],1000,10,2)
    assert got['final'] == pytest.approx(100 + 1e-9)
    assert 0 < got['final'] and got['max_dd_pct'] <= 100


@pytest.mark.parametrize('fill', [Fill(1,1,1,'A'),Fill(2,1,1,'A'),
                                  Fill(0,1,float('nan'),'A')])
def test_malformed_or_collapsed_clocks_are_explicitly_refused(fill):
    with pytest.raises(ValueError,match='portfolio_fill_invalid'):
        portfolio_curve([fill],1000,1,4)


@pytest.mark.parametrize('side,rate,slip,expected_r', [('long',.002,0,-.3),
    ('short',.002,0,.1), ('long',float('nan'),0,-.11),
    # 0.06 x 0.04 ATR is 0.0024 paid twice; funding uses modeled exit price.
    ('long',.002,.06,-.3047952), ('short',.002,.06,.0952048),
    ('long',float('nan'),.06,-.11479976)])
def test_nonoverlap_cost_inclusive_engine_equivalence(side,rate,slip,expected_r):
    n = 220
    df = pd.DataFrame(dict(ts=pd.date_range('2024-01-01',periods=n,freq='4h',tz='UTC'),
                           open=np.full(n,100.),high=np.full(n,100.02),
                           low=np.full(n,99.98),close=np.full(n,100.),volume=np.ones(n)))
    fire = np.zeros(n,bool); fire[[210,215]] = True
    zero = np.zeros(n,bool)
    risk = dict(risk_per_trade_pct=.5,taker_fee_pct=.05,slippage_atr_frac=slip,
                funding_rate_8h=.0001,bar_minutes=240)
    geo = ExitSpec(stop=dict(kind='pct',v=.01),target=dict(kind='none'),
                   trail=dict(kind='none'),time=dict(max_bars=2))
    fills = []
    result = simulate(fire if side=='long' else zero,fire if side=='short' else zero,
                      df,geo,risk,equity=2000,funding=np.full(n,rate),fills_out=fills)
    assert len(fills) == 2
    assert [f.r_multiple for f in fills] == pytest.approx([expected_r]*2)
    curve = portfolio_curve(fills,2000,.5,1)
    assert curve['final'] == pytest.approx(2000+result.pnl_usdt)
    assert curve['final'] == pytest.approx(2000*(1+.005*expected_r)**2)
    assert curve['max_dd_pct'] == pytest.approx(result.max_dd_pct)


def test_provenance_even_for_empty_input_and_no_mtm_claim():
    got = portfolio_curve([],1000,1,4)
    require_current_accounting(got)
    a = got['accounting']
    assert a['version'] == ACCOUNTING_VERSION
    assert len(a['implementation_sha256']) == 64
    assert 'closed equity' in a['drawdown']
    assert any('mark-to-market' in s for s in a['limitations'])


@pytest.mark.parametrize('metadata', [None,{},'old',{'version':ACCOUNTING_VERSION}])
def test_incomplete_provenance_is_not_upgraded_to_current(metadata):
    with pytest.raises(AccountingMismatch):
        require_current_accounting({'accounting':metadata})


def _result(h='new'):
    return dict(hash=h,tf='4h',geo='fixed',k=1,verdict='scored',testable=True,
                trades=100,consistency_p=.01,
                portfolio=dict(total_pct=20,max_dd_pct=5,
                    accounting=accounting_provenance(2000,.5,8,RESEARCH_CLOCK)))


def test_legacy_parent_ablation_and_null_comparisons_refuse_without_relabeling():
    child = _result()
    legacy = deepcopy(child); legacy['portfolio'].pop('accounting')
    saved = deepcopy(legacy)
    with pytest.raises(AccountingMismatch): growth.earns_place(child,legacy)
    with pytest.raises(AccountingMismatch): growth.ablation(child,{'part':legacy})
    with pytest.raises(AccountingMismatch):
        require_compatible_accounting(child['portfolio'],legacy['portfolio'])
    assert legacy == saved
    # Historical-only replay remains available under its original assumptions.
    require_compatible_accounting(legacy['portfolio'],deepcopy(legacy['portfolio']))


@pytest.mark.parametrize('field,value', [('version','old'),('clock','floored 4h'),
                                       ('risk_pct',1),('implementation_sha256','old')])
def test_clock_inputs_and_implementation_must_also_match(field,value):
    a = _result()['portfolio']; b = deepcopy(a); b['accounting'][field] = value
    with pytest.raises(AccountingMismatch): require_compatible_accounting(a,b)


def test_original_and_all_null_draws_share_accounting(monkeypatch):
    n = 500
    df = pd.DataFrame({'ts':pd.date_range('2024-01-01',periods=n,freq='4h',tz='UTC')})
    leg = pn.Leg('A',np.zeros(n,bool),np.zeros(n,bool),df,None)
    monkeypatch.setattr(pn,'fills_for',lambda *a,**k: ([Fill(0,10,1,'A'),Fill(1,5,1,'B')],2))
    calls = []
    def observed(*args):
        got = portfolio_curve(*args); calls.append(got); return got
    monkeypatch.setattr(pn,'portfolio_curve',observed)
    got = pn.common_rotation([leg],ExitSpec(),{},'4h',1000,10,2,draws=7)
    assert len(calls) == 8 and all(c['final']==1200 for c in calls)
    assert got['actual_total_pct'] == got['null_median'] == 20
    assert got['p'] == 1 and got['percentile'] == 0
    assert got['accounting']['clock'] == RESEARCH_CLOCK
    # A stale/mismatched draw must be refused before producing a p-value.
    def stale(*args):
        c = observed(*args)
        if len(calls)>9: c.pop('accounting')
        return c
    monkeypatch.setattr(pn,'portfolio_curve',stale)
    with pytest.raises(AccountingMismatch):
        pn.common_rotation([leg],ExitSpec(),{},'4h',1000,10,2,draws=7)


def test_mixed_timeframe_fills_retain_exits_inside_candidate_grid(monkeypatch):
    def leg(sym,step,entry,exit_,r):
        n = 214
        clock = pd.date_range('2024-01-01',periods=n,freq=f'{step}min',tz='UTC')
        df = pd.DataFrame({'ts':clock})
        lo = np.zeros(n,bool); lo[entry] = True
        ex = np.full(n,-1); ex[entry] = exit_
        rs = np.zeros(n); rs[entry] = r
        table = {'long':(ex,rs,np.full(n,100)),
                 'short':(np.full(n,-1),np.zeros(n),np.zeros(n))}
        return pn.Leg(sym,lo,np.zeros(n,bool),df,None,table=table)
    # These 15m events would all collapse onto one 4h grid cell.
    a = leg('A',15,210,211,1); b = leg('B',15,212,213,-1)
    out,_ = pn.fills_for([a,b],ExitSpec(),dict(risk_per_trade_pct=10),'4h')
    assert [(f.entry_i,f.exit_i) for f in out] == [(189000,189900),(190800,191700)]
    assert portfolio_curve(out,1000,10,1)['final'] == 990
    # Shared candidate/book origin also preserves ordering across actual TFs.
    slow = leg('C',240,210,211,1)
    mixed,_ = pn.fills_for([a,slow],ExitSpec(),dict(risk_per_trade_pct=10),'4h')
    assert mixed[1].entry_i == 3024000
    assert mixed[0].exit_i < mixed[1].entry_i


def test_legacy_refusal_preserves_rows_budget_and_historical_identifiers(tmp_path):
    journal = Journal(tmp_path/'offline.db'); led = Ledger(journal); led.ensure()
    legacy = _result('frozen'); legacy['portfolio'].pop('accounting')
    led.record_result(legacy,'grow','frozen historical evidence',{})
    led.record_test('spent','4h','fixed','gate1',1,.0025,False,
                    {'cut_ms':1000})
    before = {t:journal.query(f'SELECT * FROM {t}') for t in
              ('research_combos','research_tests','research_budget','research_candidates')}
    with pytest.raises(AccountingMismatch):
        led.record_result(_result('frozen'),'grow','must not replace',{})
    calls=[]
    runner=ResearchRunner(journal,{'research':{'enabled':True,'horizons':['4h']}},
                          run=lambda *a,**k: calls.append(a))
    assert runner.step()['skipped'] == 'portfolio_accounting_incompatible'
    assert runner._select('4h','fixed',led.rows('4h'))['skipped'] == 'portfolio_accounting_incompatible'
    assert runner._examine('4h','fixed',{'hash':'frozen'},1000)['skipped'] == 'portfolio_accounting_incompatible'
    assert calls == []
    assert {t:journal.query(f'SELECT * FROM {t}') for t in before} == before
    assert led.has('frozen') and led.known('4h','fixed') == {'frozen'}


def test_mixed_ledger_ranking_refuses_even_with_limit(tmp_path):
    led=Ledger(Journal(tmp_path/'offline.db')); led.ensure()
    legacy=_result('old'); legacy['portfolio'].pop('accounting')
    led.record_result(legacy,'grow','',{})
    led.record_result(_result(),'grow','',{})
    with pytest.raises(AccountingMismatch): led.rows('4h',limit=1)
    assert led.result('old') == legacy
    assert led.result('new')['portfolio']['accounting']['version'] == ACCOUNTING_VERSION


def test_referee_empty_and_combined_books_keep_provenance():
    got=referee.gate3([Fill(0,10,1,'A')],[],2000,.5,8)
    require_current_accounting(got['without']); require_current_accounting(got['with'])
    require_compatible_accounting(got['without'],got['with'])


def test_stale_worker_null_receipt_uses_existing_failed_look_budget_rule(tmp_path,monkeypatch):
    from tests.test_research_runner import _setup, _passing
    from trader.research import universe
    monkeypatch.setattr(universe,'coverage',lambda tfs=None: {
        tf:{s:11000 for s in universe.DISCOVERY+universe.HELDOUT}
        for tf in (tfs or ['4h'])})
    stale=_passing(); stale['rotation'].pop('accounting')
    runner,calls,_=_setup(tmp_path,examine=stale)
    assert runner.step()['kind']=='referee_select'
    for _ in range(runner._r('referee_max_attempts')):
        assert runner.step()['ok'] is False
    assert len(runner.ledger.tests())==1
    look=runner.ledger.tests()[0]
    assert look['p']==1 and not look['rejected']
    assert 'portfolio_accounting_incompatible' in look['detail']
    assert runner.ledger.next_alpha(.1,.05)[0]==2
    assert sum(c['fn']=='referee_job' for c in calls)==3


def test_statistical_policy_and_frozen_evidence_unchanged():
    # Retrospective mode adds source-bound input handling to the backtester.
    # Keep its statistical decision threshold exactly pinned while retaining
    # byte-level guards for statistical policy, configuration and evidence.
    paths = ['trader/strategy/vector_backtest.py',
             'trader/research/combo.py','trader/research/fdr.py',
             'trader/research/thresholds.py','docs/tracker',
             'docs/superpowers/evidence','docs/superpowers/specs']
    paths.remove('trader/strategy/vector_backtest.py')
    subprocess.run(['git','diff','--exit-code','fc6c0b2','--',*paths],check=True,capture_output=True)
    # config.yaml: the research budget, the risk policy and every strategies
    # key are pinned; only the owner-authorized lifecycle edits may differ
    # (ten-trade probation, three unread keys removed).
    import yaml
    then = yaml.safe_load(subprocess.check_output(
        ['git','show','fc6c0b2:config.yaml'], text=True))
    now = yaml.safe_load(Path(__file__).resolve().parents[1]
                         .joinpath('config.yaml').read_text())
    for section in then:
        if section != 'strategies':
            assert now[section] == then[section], section
    assert set(now) == set(then)
    lifecycle = {'paper_probation_trades', 'tv_validation',
                 'auto_demote_drawdown_pct', 'max_active'}
    assert {k: v for k, v in now['strategies'].items() if k not in lifecycle} \
        == {k: v for k, v in then['strategies'].items() if k not in lifecycle}
    assert now['strategies']['paper_probation_trades'] == 10
    before = subprocess.check_output(
        ['git','show','fc6c0b2:trader/strategy/vector_backtest.py'], text=True)
    after = Path(__file__).resolve().parents[1].joinpath(
        'trader/strategy/vector_backtest.py').read_text()
    decision_gate = ('ok_test, f_test = test.passes(min_trades=max(3, int(train.trades * 0.2)),\n'
                     '                                  min_pf=1.0)')
    assert decision_gate in before
    assert decision_gate in after
