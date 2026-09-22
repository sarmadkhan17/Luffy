"""Synthetic tests only; never import a market-data loader or search runner."""
from dataclasses import replace
import ast
from pathlib import Path
import itertools
import json
import subprocess
import sys

import numpy as np
import pytest

from trader.cognition import m32_block_calibration as C


def lab(s=None, **kw):
    return C.generate(s or C.SCENARIOS[0],17,calendar_mode='laboratory_repeated_strata',**kw)


@pytest.mark.parametrize('scenario',[s for s in C.SCENARIOS if s.name.endswith('_invalid')])
def test_invalid_process_refuses_before_any_draw(scenario):
    r=C.test_batch(lab(scenario),123,draws=31)
    assert r['status']=='refused' and r['reasons']
    assert r['p'] is None and r['attempted']==r['successful']==0


def test_literal_utc_month_topology_has_no_exchangeable_blocks():
    b=C.generate(C.SCENARIOS[0],17)
    assert len(C.strata(b))==48
    r=C.test_batch(b,19)
    assert r['reasons']==['no_nonidentity_exchangeable_blocks']
    assert r['p'] is None


def test_random_mcar_masks_are_not_silently_pooled():
    s=C.Scenario('random',missing=.3,missing_mode='independent')
    r=C.test_batch(lab(s),19)
    assert r['status']=='refused'
    assert 'no_nonidentity_exchangeable_blocks' in r['reasons']


@pytest.mark.parametrize('name',['iid','market_shocks','cross_symbol','within_block_ar',
    'persistent_regimes','overlapping_lookbacks','overlapping_outcomes','missing_30',
    'uneven_symbols','episode_sequences'])
def test_permitted_lab_scenarios_preserve_48_units(name):
    s=next(s for s in C.SCENARIOS if s.name==name)
    b=lab(s); before=b.y.copy()
    r=C.test_batch(b,123,draws=31)
    assert r['status']=='tested' and r['dependence_groups']==48
    assert r['successful']==31 and 1/32 <= r['p'] <=1
    assert np.array_equal(before,b.y,equal_nan=True)


def test_one_mapping_moves_all_symbols_missingness_and_chain_targets():
    b=lab(C.Scenario('chain',sequences=True,missing=.3,uneven=True))
    reasons,groups=C.validate(b); assert not reasons
    mappings=C.permutations(groups,48,5,np.random.default_rng(7))
    for mapping in mappings:
        target=C.transformed_targets(b,mapping)
        for dst,src in enumerate(mapping):
            assert np.array_equal(target[dst],b.y[src],equal_nan=True)
            assert np.array_equal(b.observed[dst],b.observed[src])
        for p,pt,c,ct in b.links:
            # Both ends of every within-block chain use the same source block.
            assert mapping[p]==mapping[c]
            assert np.array_equal(target[p,:,pt],b.y[mapping[p],:,pt],equal_nan=True)


def test_cross_block_links_and_footprints_merge_never_split():
    b=lab(C.Scenario('cross',sequences=True,cross_link=True))
    assert C.dependency_components(b)==47
    b=lab()
    b.source_start[1,0]=b.available[0,-1]-1
    assert C.dependency_components(b)==47
    assert C.test_batch(b,9)['p'] is None


def test_future_outcome_and_unknowns_cannot_silently_become_valid():
    b=lab(); b.available[0,-1]+=20*C.DAY
    assert C.test_batch(b,2)['status']=='refused'
    b=lab(C.Scenario('missing',missing=.3))
    b.y[~b.observed]=0
    assert 'missing_outcome_not_unknown' in C.test_batch(b,2)['reasons']


def test_statistic_ties_and_plus_one_tail():
    b=lab(blocks=3,symbols=1)
    b.x[:]=[1,2,4]
    b.y[:]=np.array([2,5,1])[:,None,None]
    b.score_sd[:]=1
    x=b.x-b.x.mean(); y=np.array([2,5,1])-8/3
    exact=sum(abs(x@y[list(p)]) >= abs(x@y)-1e-12 for p in itertools.permutations(range(3)))/6
    r=C.test_batch(b,19)
    assert abs(r['p']-exact)<.04
    b.y[:]=1
    assert C.test_batch(b,19)['p']==1


def test_missingness_uses_fixed_complete_data_effect_scale():
    zero=lab(C.Scenario('same',missing=.5),effect=0)
    effect=lab(C.Scenario('same',missing=.5),effect=.5)
    full=lab(C.Scenario('same'),effect=.5)
    full_zero=lab(C.Scenario('same'),effect=0)
    for b in range(48):
        # Injected raw shift is unchanged when observations are missing.
        assert np.allclose((effect.y-zero.y)[b][zero.observed[b]],
                           (full.y-full_zero.y)[b,0,0])
    assert np.all(zero.score_sd>=full.score_sd)


def test_reproducibility_and_refusal_rates_not_zero_fpr():
    kwargs=dict(worlds=12,draws=31,blocks=12)
    assert C.experiment(C.SCENARIOS[0],**kwargs)==C.experiment(C.SCENARIOS[0],**kwargs)
    r=C.experiment(C.SCENARIOS[0],calendar_mode='strict',**kwargs)
    assert r['rejection_rate'] is None and r['tested']==0 and r['refused']==12


def test_synthetic_iid_size_and_large_effect_smoke():
    null=C.experiment(C.SCENARIOS[0],worlds=100,draws=199,blocks=24)
    effect=C.experiment(C.SCENARIOS[0],worlds=100,draws=199,blocks=24,effect=1)
    assert null['rejection_rate']<.15
    assert effect['rejection_rate']>.8


def test_384_family_reports_adjusted_power_and_resolution():
    r=C.test_batch(lab(effect=.5),123,draws=31,family=384)
    assert r['family_size']==384
    assert r['family_rejections']==0
    assert not r['rejected']


def test_harness_has_no_empirical_source_or_search_import():
    source=Path(C.__file__).read_text()
    tree=ast.parse(source)
    imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
    imports += [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
    assert not any(any(s in module for s in ('sqlite','trader','requests','ccxt')) for module in imports)
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='open'
                   for n in ast.walk(tree))


def test_cli_has_no_data_input_and_refuses_overwrite(tmp_path):
    out=tmp_path/'existing.json'; out.write_text('{}')
    p=subprocess.run([sys.executable,'-m','scripts.calibrate_m32_blocks','--output',str(out)],
                     capture_output=True,text=True)
    assert p.returncode!=0 and 'already exists' in p.stderr
    assert out.read_text()=='{}'
