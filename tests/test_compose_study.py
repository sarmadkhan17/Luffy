"""Study/qualification integration for graph candidates through the REAL closure units, stores and sealing.
FIXTURE-labelled pieces (a scripted referee reading, a hand-made DEVELOPMENT_POSITIVE record) drive bookkeeping only;
the graph development in the real-flow case runs the actual evaluator."""
import json

import pytest

from tests import compose_fixtures as C
from tests.closure_fixtures import scripted_referee
from trader.core.config import load_config
from trader.research.closure import digest, manifest, qualification, study as S, synthetic as SYN
from trader.research.compose import graph
from trader.research.compose.hedge import RUNTIME_SUPPORT


def spx_spec():
    f = {'op': 'ref', 'key': 'spx', 'a': {'f': 'ret24'}}
    return dict(question='Does equity momentum direct drift?', rationale='risk appetite', expected='e', failure_conditions='f',
                structure='long_short', tf='4h', geo='fixed', long={'cmp': 'gt', 'a': f, 'b': {'lit': 0}},
                short={'cmp': 'lt', 'a': f, 'b': {'lit': 0}})


def fixture_graph_freeze(b, spec=None):
    """FIXTURE: hand-made DEVELOPMENT_POSITIVE for a graph (supplies a freeze to the real qualifier)."""
    cand = graph.make_candidate(spec or spx_spec())
    b.store.put_once('candidates', cand['candidate_id'], cand)
    c = graph.realize(cand, b.env['envelope_id'])
    res = dict(schema='closure-development.v1', stage='DEVELOPMENT', candidate_id=cand['candidate_id'],
               envelope_id=b.env['envelope_id'], outcome='DEVELOPMENT_POSITIVE', combo=c.as_dict(), combo_hash=c.hash,
               threshold_fit={}, metrics=dict(total_pct=12.0, max_dd_pct=6.0, consistency_p=0.01, median_pf=1.4, trades=120,
                                              scored_symbols=6, testable=True))
    res['result_id'] = digest(res)
    return qualification.freeze_finalist(b.store, b.env, [res])


@pytest.fixture(scope='module')
def ctx(tmp_path_factory):
    return C.build_ctx(tmp_path_factory.mktemp('q'), mode='spx')


def test_context_is_sealed_and_referee_payload_reads_it_and_restart_does_not_respend(ctx, tmp_path):
    fz = fixture_graph_freeze(ctx)
    seen = []
    run = scripted_referee(True, seen)
    cfg = load_config()
    q = qualification.qualify(ctx.store, fz, ctx.env, ctx.snap, tmp_path / 'wk', now_ms=5, run=run, cfg=cfg)
    assert q['status'] == 'QUALIFIED' and q['context_fingerprint'] and 'ref:spx' in q['required_inputs']
    assert (tmp_path / 'wk' / 'protected.db').exists()
    import sqlite3
    db = sqlite3.connect(tmp_path / 'wk' / 'protected.db')
    assert db.execute("SELECT COUNT(*) FROM refs WHERE key='spx'").fetchone()[0] > 100
    assert db.execute("SELECT MAX(available_ms) FROM market_revisions WHERE series_key='reference:spx'").fetchone()[0] \
        <= ctx.env['split']['end_ms'] + SYN.TF_MS                     # nothing published after the window end was sealed
    again = qualification.qualify(ctx.store, fz, ctx.env, ctx.snap, tmp_path / 'wk', now_ms=6, run=run, cfg=cfg)
    assert again == q and len(seen) == 1                               # restart: recorded packet, no second look


def test_missing_context_in_protected_window_blocks_before_any_look(tmp_path):
    b = C.build_ctx(tmp_path, mode='spx', name='gap', ref_until_bar=1050)    # refs stop before the protected window (bar 1100) opens
    fz = fixture_graph_freeze(b)
    calls = []
    q = qualification.qualify(b.store, fz, b.env, b.snap, tmp_path / 'wk', now_ms=5, run=scripted_referee(True, calls),
                              cfg=load_config())
    assert q['status'] == 'BLOCKED' and 'context_missing_in_protected_window' in q['reason'] and 'no look spent' in q['reason']
    assert calls == [] and b.store.get_once('looks', b.env['envelope_id']) is None     # nothing spent, nothing recorded


def test_real_flow_graph_study_develops_freezes_and_reports_visible_state(tmp_path):
    b = C.build_ctx(tmp_path, mode='spx')
    st = SYN.EPOCH_MS
    spec = dict(snapshot_path=str(b.snap), tf='4h', discovery_symbols=b.names[:6], heldout_symbols=b.names[6:],
                fit_start_ms=st, dev_end_ms=st + C.DEV_BARS * SYN.TF_MS, embargo_bars=10,
                protected_start_ms=st + 1100 * SYN.TF_MS, protected_end_ms=st + (C.N_BARS - 1) * SYN.TF_MS,
                policy=b.env['policy'], risk=C.RISK, seed_graphs=[spx_spec()], workdir=str(tmp_path / 'wk'))
    sid = S.create_study(b.store, spec, now_ms=1)
    out = S.step(b.store, sid, budget_s=900, now_ms=2, child=False, cfg=load_config(), allow_heavy=False)
    units = {u['unit_id'].split(':')[0]: json.loads(u['result']) for u in b.store.units(sid) if u['result']}
    dev = units['develop']
    assert dev['outcome'] == 'DEVELOPMENT_POSITIVE' and dev['diagnosis'] == 'DEVELOPMENT_SUPPORTED'
    assert 'ref:spx' in dev['requires']
    assert units['freeze']['freeze_id'] and out['research_outcome'] != 'QUALIFIED'      # development support is not qualification
