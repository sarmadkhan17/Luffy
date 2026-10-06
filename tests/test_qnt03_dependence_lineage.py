"""QNT-03: offline pseudo-replication, exact relationships and restart proofs."""
from dataclasses import replace
import json
import pickle
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from trader.research import portfolio_null as pn, referee as rf
from trader.research.dependence import assess_joint, relationship_controls
from trader.strategy.geometries import GEOS
from trader.world import WorldHistory
from trader.world.context import WorldContext
from tests.test_null_dependence import _legs, RISK
from tests.test_wrld07_relationship_state import relationship, model


def assess(legs, **kwargs):
    return assess_joint(legs, GEOS['trail'], RISK, '4h',
        hypothesis={'kind': 'TEST_ONLY shared-factor timing'},
        consistency_draws=40, dependence_draws=40, rotation_draws=199, **kwargs)


def test_lineage_retained_and_correlated_outcomes_deflated():
    out = assess(_legs(1, 1.0))
    assert len(out['contributors']) == 8
    assert len({r['evidence_id'] for r in out['contributors']}) == 8
    assert out['rho_bar'] > .5 and out['n_eff'] < 3
    assert out['consistency_p_dep'] >= out['consistency_p']
    assert out['common_rotation']['draws'] == 199
    assert out['protocol']['independent_shuffles'] == 'not an admissible substitute'
    assert out['protocol']['relationship_use'].endswith('return correlation is not null-vote rho')
    assert all(r['inputs']['frame']['sha256'] and r['inputs']['long']['sha256']
               for r in out['contributors'])


def test_alias_copies_do_not_create_independent_samples():
    one = _legs(1, 1.0, k=1)[0]
    aliases = [pn.Leg(f'alias{i}', one.long.copy(), one.short.copy(),
                     one.df.copy(), None) for i in range(8)]
    out = assess(aliases)
    assert len(out['contributors']) == 8  # keep every contributor
    assert out['unique_outcome_inputs'] == 1
    assert out['scored_symbols'] <= 1 and out['consistency_p_dep'] is None
    reading = rf.consistency(aliases, GEOS['trail'], RISK, '4h', draws=40)
    assert reading['scored_symbols'] <= 1 and reading['consistency_p_dep'] is None


def test_repeated_symbol_is_refused_even_if_inputs_differ():
    legs = _legs(1, 1.0)
    legs[1].symbol = legs[0].symbol
    out = assess(legs)
    assert out['consistency_p_dep'] is None and 'duplicate' in out['reason']


def test_equal_offsets_on_different_calendars_are_not_common_controls():
    legs = _legs(1, 1.0)
    legs[1].df['ts'] += pd.Timedelta(hours=4)
    out = assess(legs)
    assert out['common_rotation']['p'] is None and 'unaligned' in out['reason']
    pn._prepare(legs, GEOS['trail'], RISK, '4h')
    assert rf.null_dependence(legs, RISK)['rho_bar'] is None


def test_irregular_calendar_is_untestable():
    legs = _legs(1, 1.0)
    for leg in legs:
        leg.df.loc[700:, 'ts'] += pd.Timedelta(hours=4)
    assert 'irregular' in assess(legs)['reason']


def test_unmeasured_pairs_cannot_be_silently_discarded():
    legs = _legs(1, 1.0)
    legs[0].long = np.zeros(len(legs[0].df), bool)
    legs[0].short = legs[0].long.copy()
    pn._prepare(legs, GEOS['trail'], RISK, '4h')
    dep = rf.null_dependence(legs, RISK, draws=40)
    assert dep['rho_bar'] is None and dep['pairs'] > 0
    assert dep['reason'] == 'incomplete pairwise null dependence'


def test_relationship_versions_and_expiry_used_at_exact_cut(tmp_path):
    first = relationship()
    later = replace(first, as_of_ms=1100, value={'correlation': -.3},
                    uncertainty={'instability': 'sign reversal at measured cut'})
    expired = replace(first, as_of_ms=1501, quality=first.quality.STALE)
    history = WorldHistory((model(first), model(later), model(expired)))
    context = WorldContext(history)
    a = relationship_controls(context, [1000])
    b = relationship_controls(context, [1100])
    assert a['cuts'][0]['relationships'][0]['record']['value']['correlation'] == .99
    assert b['cuts'][0]['relationships'][0]['record']['value']['correlation'] == -.3
    assert a['cuts'][0]['relationships'][0]['record']['relationship_id'] != \
           b['cuts'][0]['relationships'][0]['record']['relationship_id']
    assert a['cuts'][0]['relationships'][0]['record']['evidence'][0]['record_sha256']
    assert relationship_controls(context, [1050])['status'] == 'EXACT_CONTEXT_UNAVAILABLE'
    stale = relationship_controls(context, [1501])['cuts'][0]
    assert stale['measured_control_groups'] == []
    assert not stale['relationships'][0]['current']
    path = tmp_path / 'history.json'
    path.write_text(history.to_json())
    restored = WorldContext(WorldHistory.from_json(path.read_text()))
    assert relationship_controls(restored, [1000, 1050, 1100, 1501]) == \
           relationship_controls(context, [1000, 1050, 1100, 1501])


def test_rebuild_after_disk_reload_reproduces_treatment_and_receipt(tmp_path):
    # Retain raw synthetic inputs, context and result, then rebuild new Legs.
    legs = _legs(1, 1.0, k=4)
    for leg in legs:
        leg.df['ts'] = pd.date_range(pd.Timestamp(1000, unit='ms', tz='UTC'),
                                    periods=len(leg.df), freq='4h')
    history = WorldHistory((model(relationship()),))
    world = WorldContext(history)
    original = assess(legs, world=world)
    assert original['relationships']['cuts'][0]['relationships'][0]['current']
    payload = {'history': history.to_json(), 'result': original,
               'legs': [{'symbol': l.symbol, 'df': l.df,
                         'long': l.long, 'short': l.short} for l in legs]}
    path = tmp_path / 'frozen.pkl'; path.write_bytes(pickle.dumps(payload))
    loaded = pickle.loads(path.read_bytes())
    rebuilt = [pn.Leg(r['symbol'], r['long'], r['short'], r['df'], None)
               for r in loaded['legs']]
    replay = assess(rebuilt, world=WorldContext(WorldHistory.from_json(loaded['history'])))
    # A new interpreter has no cached tables or previous runtime objects.
    script = """
import json, pickle, sys
from trader.research.portfolio_null import Leg
from trader.world import WorldHistory
from trader.world.context import WorldContext
from tests.test_qnt03_dependence_lineage import assess
with open(sys.argv[1], 'rb') as f: p = pickle.load(f)
legs = [Leg(r['symbol'], r['long'], r['short'], r['df'], None) for r in p['legs']]
print(json.dumps(assess(legs, world=WorldContext(WorldHistory.from_json(p['history'])))))
"""
    restarted = json.loads(subprocess.check_output([sys.executable, '-c', script, str(path)], text=True))
    assert restarted == replay
    assert replay == loaded['result']
    assert assess(list(reversed(rebuilt)), world=world) == replay
    rebuilt[0].long[800] = not rebuilt[0].long[800]
    changed = assess(rebuilt, world=world)
    assert changed['receipt_id'] != replay['receipt_id']
    assert changed['contributors'][0]['evidence_id'] != replay['contributors'][0]['evidence_id']


def test_insufficient_draws_does_not_substitute_independent_votes():
    out = assess_joint(_legs(1, 1.0), GEOS['trail'], RISK, '4h',
                       hypothesis={'kind': 'timing'}, rotation_draws=0)
    assert out['consistency_p_dep'] is None and out['common_rotation']['p'] is None


def test_analyst_producer_forwards_common_slice_context_and_funding(monkeypatch):
    from types import SimpleNamespace
    import tests.test_null_dependence as fixture
    import trader.brain.analyst as am
    from tests.test_admission_null_gate import _spec
    monkeypatch.setattr(fixture, 'N', 3000)
    legs = fixture._legs(1, 1.0, k=4)
    frames = {l.symbol: l.df for l in legs}
    source = WorldContext(WorldHistory(()), 'TEST_ONLY')
    import trader.world.context as wc
    monkeypatch.setattr(wc, 'load_context', lambda **kw: source)
    spec = _spec(list(frames))
    calls = []
    def entries(sf, **ctx):
        calls.append(ctx)
        l = next(l for l in legs if l.symbol == ctx['symbol'])
        return l.long, l.short
    monkeypatch.setattr(am, 'compile_spec', lambda spec: SimpleNamespace(spec=spec, entries=entries))
    funding = np.linspace(-.0001, .0001, 3000)
    monkeypatch.setattr(am, 'funding_for', lambda *a: funding)
    a = am.Analyst.__new__(am.Analyst); a.null_max_p = .01
    a._ctx = lambda *args: (frames, None, lambda s: {}, RISK)
    out = a._null_evidence(spec, '4h')
    assert 'receipt_id' in out and out['common_rotation']['p'] is not None
    assert out['protocol']['hypothesis']['universe']['include'] == list(frames)
    assert out['protocol']['rotation_draws'] == 399
    assert len(out['contributors']) == 4
    assert all(r['inputs']['funding']['sha256'] for r in out['contributors'])
    assert all(r['inputs']['first_bar'] == 210 for r in out['contributors'])
    assert all(ctx['world'] is source and set(ctx['universe']) == set(frames) for ctx in calls)
    assert set(out['upstream_sources']['universe']) == set(frames)
