import json
"""Non-trade scan observations: isolation from the cooldown, identity, revisions, outcomes."""
from types import SimpleNamespace as NS
import time

import numpy as np
import pandas as pd

from tests.test_lean_scan import offline_scan
from trader.core.types import ControlState, Action
from trader.engine.exits import SpecExit
from trader.learning import scan_observations as SO
from trader.scheduling.lean_scan import run

ACTIVE = lambda: NS(state=ControlState.ACTIVE, refresh=lambda: ControlState.ACTIVE, can_enter=lambda: True)


def scan(tmp_path, monkeypatch, *, enabled=True, state=None, admit=False, full=False):
    if full:   # opt-in heavy path: the offline fixture's frames cannot be frozen by the installed
        # pandas (the existing trade path fails identically), so stub ONLY frame serialisation.
        monkeypatch.setattr('trader.learning.capture_runtime.frame_chunks', lambda db, snap, cut: dict(chunks={}))
    k, _, _ = offline_scan(tmp_path, monkeypatch, actionable=True)
    k._spec_exits = {'lean': SpecExit(max_bars=4, timeframe='4h', trail_atr_mult=0, has_target=False, stop_atr_mult=2.0)}
    k.cfg = dict(k.cfg, scan_observations=dict(enabled=enabled, store_frame_blobs=full))
    if state:
        k.state_machine = state
    if admit:
        def enter(d, snap, *a):
            d._persist_trade_evidence(); d.executed = True; d.size_usdt = 100
            return True
        k._try_enter = enter
    return k


def decisions(k):
    return [(r['symbol'], r['action'], r['executed'], r['skip_reason'])
            for r in k.journal.query('SELECT symbol,action,executed,skip_reason FROM decisions ORDER BY symbol')]


def test_skipped_buy_does_not_block_executable_buy_and_matches_disabled(tmp_path, monkeypatch):
    results = {}
    for enabled in (True, False):
        (tmp_path / str(enabled)).mkdir()
        k = scan(tmp_path / str(enabled), monkeypatch, enabled=enabled)
        frozen = run(k)                                   # skipped BUY observations (state=FROZEN)
        n_obs = len(k.journal.query('SELECT 1 FROM scan_observations')) if enabled else 0
        assert n_obs == (30 if enabled else 0)
        assert not k.journal.query('SELECT 1 FROM decisions')       # decisions untouched
        # age every observation 10 minutes: cooldown (45 min) would block if it could see them
        if enabled:
            k.journal._conn().execute('UPDATE scan_observations SET observed_ms=observed_ms-600000')
        k.state_machine = ACTIVE()
        def enter(d, snap, *a, _k=k):
            d._persist_trade_evidence(); d.executed = True; d.size_usdt = 100
            return True
        k._try_enter = enter
        live = run(k)
        results[enabled] = (frozen['skips'], live['entries'], live['persisted_attempts'], decisions(k))
    assert results[True] == results[False]
    assert results[True][1] == 30 and all(row[1] == 'BUY' and row[2] == 1 for row in results[True][3])


def test_cooldown_control_is_sensitive_to_decisions_rows_only(tmp_path, monkeypatch):
    """Control: a prior row in `decisions` 10 min ago DOES block; an observation never can."""
    k = scan(tmp_path, monkeypatch, state=ACTIVE(), admit=True)
    ts = (pd.Timestamp.now(tz='UTC') - pd.Timedelta(minutes=10)).isoformat()
    with k.journal._tx() as c:
        c.execute('PRAGMA foreign_keys=OFF')
        c.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,executed) "
                  "VALUES ('d0','x',?,'C0/USDT','BUY',.6,.24,.6,1)", (ts,))
    live = run(k)
    assert live['entries'] == 29                          # C0/USDT blocked by a real decision row
    assert [r for r in decisions(k) if r[0] == 'C0/USDT'] == [('C0/USDT', 'BUY', 1, None)]   # only the seeded row


def test_opposing_skipped_observation_then_executable_signal(tmp_path, monkeypatch):
    k = scan(tmp_path, monkeypatch)
    run(k)                                                # FROZEN: BUY observations
    sig = k.journal.query('SELECT * FROM scan_observations ORDER BY symbol LIMIT 1')[0]
    # an opposing SELL observation for the same symbol, written through the same sink
    snap = NS(symbol=sig['symbol'], price=100.0, df=lambda tf: None, ts=None)
    d = NS(strategy_signals=[dict(strategy_id='lean', symbol=sig['symbol'], action='SELL', params=dict(
        spec_id='lean', spec_fingerprint=sig['spec_fingerprint'], signal_timeframe='4h',
        signal_bar_close_ms=sig['signal_bar_close_ms'], market_provenance=dict(sources=[])))],
        action=Action.SELL, skip_reason='state=FROZEN', ts=pd.Timestamp.now(tz='UTC').isoformat(), cycle_id='cx')
    ids = SO.record(k.journal, snap, d, cfg=k.cfg, control_state=ControlState.FROZEN)
    assert ids and k.journal.query('SELECT COUNT(*) n FROM scan_observations')[0]['n'] == 31
    k.state_machine = ACTIVE()
    def enter(dd, s, *a):
        dd._persist_trade_evidence(); dd.executed = True; dd.size_usdt = 100
        return True
    k._try_enter = enter
    assert run(k)['entries'] == 30


def test_repeated_scans_on_one_bar_close_are_one_occurrence_and_revisions_are_not_samples(tmp_path, monkeypatch):
    k = scan(tmp_path, monkeypatch)
    for _ in range(3):
        run(k)                                            # 3 x 60s ticks, same 4h close, same inputs
    rows = k.journal.query('SELECT * FROM scan_observations')
    assert len(rows) == 30 and {r['revision_seq'] for r in rows} == {0}
    assert len({(r['symbol'], r['strategy_id'], r['signal_bar_close_ms']) for r in rows}) == 30
    assert all(r['spec_sha256'] and r['spec_fingerprint'] for r in rows)
    # default lightweight path: provenance manifest on the row, no blobs/manifests written
    assert all(r['source_manifest_id'] is None and json.loads(r['provenance_manifest_json'])['sources'] for r in rows)
    for t in ('learning_decision_source_manifests', 'learning_source_blobs', 'learning_registrations'):
        assert not k.journal.query("SELECT 1 FROM sqlite_master WHERE name=?", (t,)) \
            or not k.journal.query(f'SELECT 1 FROM {t} LIMIT 1')
    # a restated secondary input on the SAME bar close: revision, not a new sample
    import trader.learning.scan_observations as mod
    real = mod._provenance_digest
    mod._provenance_digest = lambda p: (real(p)[0] + 'x', real(p)[1])
    try:
        run(k); run(k)
    finally:
        mod._provenance_digest = real
    assert len(k.journal.query('SELECT 1 FROM scan_observations')) == 60
    samples = k.journal.query('SELECT * FROM scan_observation_samples')
    assert len(samples) == 30 and {r['revision_seq'] for r in samples} == {1}
    assert all(r['supersedes_observation_id'] for r in samples)


def test_counterfactual_grading_is_labelled_and_never_touches_trade_tables(tmp_path, monkeypatch):
    k = scan(tmp_path, monkeypatch)
    run(k)
    o = k.journal.query('SELECT * FROM scan_observations ORDER BY symbol LIMIT 1')[0]
    assert o['horizon_ms'] == 4 * 4 * 3600_000 and o['initial_risk'] and o['risk_basis'].startswith('spec_stop_atr')
    start = o['observed_ms']; step = 300_000; n = int(o['horizon_ms'] / step) + 40
    ts = np.arange(start - step, start + n * step, step, dtype=np.int64)
    px = np.linspace(100, 103, len(ts)); frame = pd.DataFrame(dict(
        ts=pd.to_datetime(ts, unit='ms', utc=True), open=px, high=px + .5, low=px - .5, close=px))
    feed = NS(fetch_ohlcv=lambda sym, tf, limit=0, as_of_ms=None: frame)
    assert SO.resolve_pending(k.journal, feed, start + o['horizon_ms'] - 1) == 0      # horizon not elapsed
    assert SO.resolve_pending(k.journal, feed, start + o['horizon_ms'] + 600_000) == 30
    r = k.journal.query('SELECT * FROM scan_observation_outcomes WHERE observation_id=?', (o['observation_id'],))[0]
    assert r['outcome_kind'] == 'COUNTERFACTUAL_PRICE_PATH' and r['status'] == 'GRADED' and r['fwd_return'] > 0
    assert r['mfe_r'] is not None and r['mae_r'] is not None
    for table in ('decisions', 'outcomes', 'trades', 'cycles'):
        assert not k.journal.query(f'SELECT 1 FROM {table}'), table
    print('\n' + SO.format_summary(SO.summary(k.journal, 3)))


def test_reason_class_masks_drifting_numbers_so_a_score_change_is_not_a_revision():
    a = NS(skip_reason='4h trend UP s=+0.82 — hard veto', action=Action.SELL)
    b = NS(skip_reason='4h trend UP s=+0.91 — hard veto', action=Action.SELL)
    assert SO.reason_class(a, '') == SO.reason_class(b, '')
    assert SO.reason_class(NS(skip_reason='', action=Action.HOLD), '') == 'not_admitted:hold'
    assert SO.reason_class(NS(skip_reason='', action=Action.BUY), 'state=FROZEN') == 'state=#FROZEN'.replace('#','') 


def test_full_manifest_capture_is_opt_in(tmp_path, monkeypatch):
    k = scan(tmp_path, monkeypatch, full=True)
    run(k)
    rows = k.journal.query('SELECT * FROM scan_observations')
    assert len(rows) == 30 and all(r['source_manifest_id'] for r in rows)
    assert k.journal.query('SELECT 1 FROM learning_decision_source_manifests LIMIT 1')
