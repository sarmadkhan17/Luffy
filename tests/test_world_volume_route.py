"""Independent bounded World producer -> existing WorldContext -> world_volume_anomaly factor -> compiled entries."""
import sqlite3
from contextlib import closing

import numpy as np
import pandas as pd
import pytest

from trader.cognition.attention import CognitionConfig
from trader.cognition.contracts import volume_z
from trader.core.types import TF_MS
from trader.research.compose import data, graph, world_producer as WPR
from trader.strategy import spec_evidence
from trader.strategy.compile import compile_spec
from trader.world.context import load_context

TF = TF_MS['4h']
T0 = 1_700_000_000_000 // TF * TF
N = 420
SYMS = ['A/USDT', 'B/USDT', 'C/USDT', 'D/USDT', 'E/USDT', 'F/USDT']


def make_frames(seed=4):
    rng = np.random.default_rng(seed)
    out = {}
    for k, s in enumerate(SYMS):
        px = 100 * np.exp(np.cumsum(rng.normal(0, .004, N)))
        vol = (k + 1) * 1000 * np.exp(rng.normal(0, .35, N))
        ts = pd.to_datetime(T0 + np.arange(N) * TF, unit='ms', utc=True)
        out[s] = pd.DataFrame(dict(ts=ts, open=px, high=px * 1.002, low=px * .998, close=px, volume=vol,
                                  available_at_ms=T0 + (np.arange(N) + 1) * TF))
    return out


def rule():
    return graph.make_candidate(dict(question='does volume anomaly select entries', rationale='r', expected='e',
                                     failure_conditions='f', tf='4h', geo='fixed', structure='long_only',
                                     long={'cmp': 'gt', 'a': {'f': 'world_volume_anomaly'}, 'b': {'lit': 1}}, short=None))


@pytest.fixture(scope='module')
def produced(tmp_path_factory):
    frames = make_frames()
    path = tmp_path_factory.mktemp('w') / 'world.db'
    end = T0 + N * TF
    out = WPR.produce_store(frames, '4h', path, start_ms=T0 + 60 * TF, end_ms=end, retrospective=False, shortlist=3)
    return frames, path, out, end


def test_store_is_bounded_to_the_shortlist_and_read_by_the_existing_context_reader(produced):
    frames, path, out, end = produced
    assert out['shortlist'] == 3 and out['cuts'] > 300
    ctx = load_context(path, as_of_ms=end)
    assert ctx.source_status == 'RETAINED' and len(ctx.history.cuts) == out['cuts']
    model = ctx.history.get_exact(ctx.history.cuts[100])
    members = [s.scope if hasattr(s, 'scope') else s for s in model.states]
    assert len(model.states) <= 3                                              # never the whole universe
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT COUNT(*) FROM scans').fetchone()[0] == out['cuts']


def test_world_observation_equals_the_authoritative_volume_z_and_only_shortlisted_members_have_one(produced):
    frames, path, out, end = produced
    ctx = load_context(path, as_of_ms=end)
    w = CognitionConfig().window
    from trader.world import Horizon, Scope, ScopeLevel
    checked = hits = 0
    for t in range(150, 170):
        cut = T0 + (t + 1) * TF
        scored = []
        for s in SYMS:
            v, c = frames[s]['volume'].to_numpy(), frames[s]['close'].to_numpy()
            scored.append((-float(np.sum(v[t - 5:t + 1] * c[t - 5:t + 1])), s))
        top = sorted(s for _, s in sorted(scored)[:3])
        for s in SYMS:
            q = ctx.observations(cut, Scope(ScopeLevel.INSTRUMENT, s), Horizon.INTRADAY, kind='volume_anomaly')
            if s in top:
                expected = volume_z(frames[s]['volume'].to_numpy()[t - w:t + 1])
                assert q.observations and q.observations[0].value == pytest.approx(expected)
                hits += 1
            else:
                assert not q.observations or q.quality.value != 'VALID'                    # outside the shortlist: unknown, not zero
            checked += 1
    assert hits == 60 and checked == 120


def test_required_input_reaches_actual_compiled_entries_and_intervention_changes_them(produced, tmp_path):
    frames, path, out, end = produced
    w = CognitionConfig().window
    comp = compile_spec(graph.realize(rule(), 'world-route').to_spec())
    ctx = load_context(path, as_of_ms=end)
    sym = None
    for s in SYMS:                                                                 # the big-volume names are the shortlist
        sf = spec_evidence.frames_for(frames[s], '4h')
        lo, _ = comp.entries(sf, symbol=s, world=ctx)
        if np.asarray(lo, bool).any():
            sym, entries = s, np.asarray(lo, bool)
            break
    assert sym is not None and entries.sum() > 3
    # independent expectation from the raw volumes: z > 1 AND in the shortlist at that cut
    expected = np.zeros(N, bool)
    v = frames[sym]['volume'].to_numpy()
    for t in range(WPR.RANK_BARS, N):
        cut = T0 + (t + 1) * TF
        if cut < T0 + 60 * TF or t < w:
            continue
        scored = sorted((-float(np.sum(frames[s]['volume'].to_numpy()[t - 5:t + 1] * frames[s]['close'].to_numpy()[t - 5:t + 1])), s) for s in SYMS)
        if sym in [s for _, s in scored[:3]]:
            z = volume_z(v[t - w:t + 1])
            expected[t] = z is not None and z > 1
    assert (entries[w + 10:] == expected[w + 10:]).all()
    # intervention: scale ONE symbol's volume history in the source and re-produce -> its entries change
    changed = {k: f.copy() for k, f in frames.items()}
    changed[sym]['volume'] = changed[sym]['volume'] * np.where(np.arange(N) % 7 == 0, 8.0, 1.0)
    p2 = tmp_path / 'w2.db'
    WPR.produce_store(changed, '4h', p2, start_ms=T0 + 60 * TF, end_ms=end, retrospective=False, shortlist=3)
    lo2, _ = comp.entries(spec_evidence.frames_for(changed[sym], '4h'), symbol=sym, world=load_context(p2, as_of_ms=end))
    assert (np.asarray(lo2, bool) != entries).any()
    assert 'world' in graph.requires_of(rule())


def test_late_capture_is_not_an_earlier_observation_and_retrospective_is_labelled(produced):
    import json
    import trader.data.references as R
    frames, _, _, _ = produced
    late = {k: f.assign(available_at_ms=R.to_ms(f['ts']) + TF + 10 * TF) for k, f in frames.items()}   # every bar received 10 bars late
    cut = T0 + 150 * TF
    live = [json.loads(p) for _, p in WPR.produce_rows(late, '4h', [cut], retrospective=False, shortlist=3)]
    assert all(r['world_model']['observation_counts'].get('VALID', 0) == 0 for r in live)      # not available at the cut: no observation
    assert all(r['evidence_scope'] == 'point_in_time' for r in live)
    retro = [json.loads(p) for _, p in WPR.produce_rows(late, '4h', [cut], retrospective=True, shortlist=3)]
    assert retro and retro[0]['evidence_scope'] == 'retrospective_observation'                  # labelled; availability = event close
    assert retro[0]['world_model']['observation_counts'].get('VALID', 0) > 0


def test_reconstruction_mode_survives_outer_label_removal_and_cannot_be_qualified(tmp_path):
    import json
    from trader.world import Scope, ScopeLevel, Horizon
    from trader.research.compose.world_snapshot import seal_world
    from trader.research.closure import ClosureRefused
    frames = make_frames()
    cut = T0 + 150 * TF
    rows = WPR.produce_rows(frames, '4h', [cut], retrospective=True, shortlist=3)
    altered = []
    for t, raw in rows:
        body = json.loads(raw)
        body.pop('evidence_scope')
        body['world_model']['producer_id'] = 'attention-scan-world-producer.v1'
        altered.append((t, json.dumps(body)))
    path = tmp_path / 'reconstructed.db'
    WPR.write_store(path, altered)
    ctx = load_context(path, as_of_ms=cut)
    assert ctx.source_status == 'RECONSTRUCTED_RETROSPECTIVE'
    member = ctx.history.get_exact(cut).states[0].instrument
    q = lambda c: c.observations(cut, Scope(ScopeLevel.INSTRUMENT, member), Horizon.INTRADAY, kind='volume_anomaly')
    assert q(ctx).quality.value == 'UNKNOWN'
    allowed = load_context(path, as_of_ms=cut, allow_reconstruction=True)
    assert q(allowed).quality.value == 'VALID' and allowed.context_id != ctx.context_id
    with pytest.raises(ClosureRefused, match='world_protected_history_invalid'):
        seal_world(path, tmp_path / 'sealed.db', dict(tf='4h', split=dict(start_ms=cut, end_ms=cut)))
    assert not (tmp_path / 'sealed.db').exists()


def test_missing_acquisition_times_are_not_original_time_evidence():
    import json
    frames = {s: f.drop(columns=['available_at_ms']) for s, f in make_frames().items()}
    rows = WPR.produce_rows(frames, '4h', [T0 + 150 * TF], retrospective=False)
    assert all(not json.loads(raw)['world_model']['observation_counts'] for _, raw in rows)


def test_admitted_snapshot_inference_matches_research_and_keeps_timeframes_separate(tmp_path):
    from datetime import datetime, timezone
    from trader.core.types import Snapshot
    from trader.world import Scope, ScopeLevel, Horizon
    from trader.research.compose import catalog
    frames = make_frames()
    sym = SYMS[0]
    end = T0 + N * TF
    frame = frames[sym]
    snap = Snapshot(sym, datetime.fromtimestamp(end / 1000, timezone.utc).isoformat(),
                    float(frame['close'].iloc[-1]), {'4h': frame})
    live = WPR.snapshot_context(snap)
    path = tmp_path / 'research-world.db'
    WPR.write_store(path, WPR.produce_rows({sym: frame}, '4h', [end], retrospective=False))
    historical = load_context(path, as_of_ms=end)
    assert live.history.history_id == historical.history.history_id
    scope = Scope(ScopeLevel.INSTRUMENT, sym)
    q = live.observations(end, scope, Horizon.INTRADAY, kind='price_return_24h', timeframe='4h')
    assert q.quality.value == 'VALID'
    assert q.observations[0].value == pytest.approx(frame['close'].iloc[-1] / frame['close'].iloc[-7] - 1)
    one = frame.copy()
    step = TF_MS['1h']
    one['ts'] = pd.to_datetime(end - np.arange(N, 0, -1) * step, unit='ms', utc=True)
    one['available_at_ms'] = end - np.arange(N - 1, -1, -1) * step
    snap.dfs['1h'] = one
    both = WPR.snapshot_context(snap)
    for tf in ('4h', '1h'):
        assert both.observations(end, scope, Horizon.INTRADAY, kind='volume_anomaly', timeframe=tf).quality.value == 'VALID'
    assert both.observations(end, scope, Horizon.INTRADAY, kind='volume_anomaly').quality.value == 'UNKNOWN'
    measured = catalog.measure(path, world_path=path)
    assert catalog.status_of(catalog.BY_KEY['world_return24h'], measured)[0] == 'EXECUTABLE'


def test_a_new_world_cut_with_identical_inputs_does_not_retrigger_economic_decisions():
    from datetime import datetime, timezone
    from trader.core.types import Snapshot
    from trader.scheduling.coordination import input_fingerprint
    frame = make_frames()[SYMS[0]]
    end = T0 + N * TF
    snap = Snapshot(SYMS[0], datetime.fromtimestamp(end / 1000, timezone.utc).isoformat(), 100., {'4h': frame})
    snap.world = WPR.snapshot_context(snap)
    args = dict(generation=1, control='FROZEN', funding={}, oi={}, book=[], learning={})
    before = input_fingerprint(snap, **args)
    identity = snap.world.context_id
    snap.ts = datetime.fromtimestamp((end + 100) / 1000, timezone.utc).isoformat()
    snap.world = WPR.snapshot_context(snap)
    assert snap.world.context_id != identity
    assert input_fingerprint(snap, **args) == before
    snap.dfs['4h'] = frame.copy()
    snap.dfs['4h'].loc[N - 1, 'volume'] *= 10
    snap.world = WPR.snapshot_context(snap)
    assert input_fingerprint(snap, **args) != before
