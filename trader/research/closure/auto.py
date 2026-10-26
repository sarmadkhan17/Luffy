"""Autonomous scheduled research: safe snapshot -> derived study -> bounded resumable steps.

What `scheduled()` does when `research.closure.auto` is configured:

1. RESUME the newest auto study that still has runnable bounded units or is waiting for data; else
2. when due (default every 12 h), publish a bounded exact-cut snapshot through the normal data reader (a full
   filesystem copy is an explicit optional mode), derive a study from its ACTUAL coverage, and create the study;
3. run intake / search / develop / revise / freeze in killable, niced, resource-limited children.

Qualification and handoff are never run here (they spend a protected look and need the operator). A study can end
as insufficient evidence or blocked data; nothing here forces a result, invents data or opens a gate. All outcomes
are written to the closure Store so they survive the kernel callback that discards its return value.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from ...core.types import TF_MS
from . import ClosureRefused, canonical, digest

COPY_SUFFIXES = ('-wal', '-journal')       # -shm is derived state: never copied
DEFAULTS = dict(interval_hours=12.0, tf='4h', protected_extra_bars=0, embargo_bars=10, fit_bars=1620, heldout_frac=0.4,
                development_mode='RETROSPECTIVE', prep_timeout_s=900, prep_memory_mb=3072, step_memory_mb=3072,
                max_candidates=8, unit_timeout_s=900, search={}, retry_hours=1.0, min_free_gb=2.0)
STEP_KINDS = ('intake', 'search', 'develop', 'revise', 'freeze')      # qualify / handoff are operator-only


def settings(cfg) -> dict | None:
    c = (((cfg or {}).get('research') or {}).get('closure') or {})
    a = c.get('auto')
    if not c.get('enabled', False) or not isinstance(a, dict) or not a.get('enabled', False):
        return None
    out = dict(DEFAULTS, **{k: v for k, v in a.items() if k != 'enabled'})
    if not out.get('snapshot_dir') or not out.get('market_db'):
        raise ClosureRefused('auto_requires_snapshot_dir_and_market_db')
    return out


def recent_development(auto):
    """Explicit retrospective paper-research policy; never an independent look."""
    enabled=auto.get('recent_retrospective_development',False)
    if type(enabled) is not bool or (enabled and auto.get('development_mode')!='RETROSPECTIVE'):
        raise ClosureRefused('recent_development_requires_retrospective_policy')
    return enabled


def horizon_for(auto, index):
    """Predeclared horizon rotation; never choose from measured profitability.

    A horizon may override fit_bars (the history required by its mechanism).
    Publication reads cached rows only; missing coverage is a data task.
    """
    options = auto.get('horizons') or [dict(tf=auto['tf'], fit_bars=auto['fit_bars'])]
    if not isinstance(options, list) or not options or len(options) > 8:
        raise ClosureRefused('research_horizons_invalid')
    for option in options:
        if (not isinstance(option, dict) or option.get('tf') not in ('15m', '1h', '4h')
                or type(option.get('fit_bars')) is not int or option['fit_bars'] < 400):
            raise ClosureRefused('research_horizon_contract_invalid')
    return dict(auto, **options[index % len(options)])


# ── snapshot: filesystem copy only ─────────────────────────────────────────
GIB = 1024 ** 3
CHUNK = 8 * 1024 * 1024


def _ident(p: Path):
    st = p.stat()
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def _wal_header(p: Path) -> bytes:
    with open(p, 'rb') as f:
        return f.read(32)               # magic, version, page size, checkpoint sequence, the two generation salts


def _capture(src: Path) -> dict:
    wal, jr = Path(str(src) + '-wal'), Path(str(src) + '-journal')
    return dict(db=_ident(src), wal=(_ident(wal), _wal_header(wal)) if wal.is_file() else None,
                journal=_ident(jr) if jr.is_file() else None)


def _copy_bytes(src: Path, dst: Path, limit=None, deadline=None):
    done = 0
    with open(src, 'rb') as r, open(dst, 'wb') as w:
        while limit is None or done < limit:
            if deadline is not None and time.monotonic() > deadline:
                raise ClosureRefused('auto_prep_deadline')
            chunk = r.read(CHUNK if limit is None else min(CHUNK, limit - done))
            if not chunk:
                break
            w.write(chunk)
            done += len(chunk)
    return done


def copy_database(src, dst, *, deadline=None, min_free_bytes=2 * GIB, _between=None) -> dict:
    """Copy `src` (+ -wal/-journal) to `dst` as plain bytes (the source is never opened by SQLite, not even read-only),
    then settle the COPY into one quiescent, integrity-checked file.

    Consistency is proven, not assumed: the database file identity (size, mtime, inode) and the WAL generation header
    are captured before and after, the WAL is copied only up to the length seen at the start (a bounded prefix of one
    generation), and ANY change in the database file, a WAL restart/checkpoint, a shrunk WAL or a moved rollback journal
    refuses the snapshot as unstable. integrity_check alone cannot detect a logically mixed copy, so it is not relied on.
    A refusal is retried at the next scheduled attempt; a mixed snapshot is never presented as coordinated.
    `deadline` is a monotonic instant: the copy stops at it instead of overrunning the caller's slot."""
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise ClosureRefused('auto_source_missing', src.name)
    before = _capture(src)
    wal_len = before['wal'][0][0] if before['wal'] else 0
    jr_len = before['journal'][0] if before['journal'] else 0
    need = before['db'][0] + wal_len + jr_len
    dst.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(dst.parent).free < need + max(int(min_free_bytes), need // 10):
        raise ClosureRefused('auto_disk_space_insufficient', f'need {need} bytes plus reserve')
    tmp = dst.parent / ('.partial-' + dst.name)
    for suffix in ('', '-wal', '-shm', '-journal'):
        Path(str(tmp) + suffix).unlink(missing_ok=True)
    try:
        _copy_bytes(src, tmp, deadline=deadline)
        if before['wal']:
            _copy_bytes(Path(str(src) + '-wal'), Path(str(tmp) + '-wal'), limit=wal_len, deadline=deadline)
        if before['journal']:
            _copy_bytes(Path(str(src) + '-journal'), Path(str(tmp) + '-journal'), deadline=deadline)
        if _between is not None:
            _between()                                            # test seam: a writer acting during the copy
        after = _capture(src)
        stable = (after['db'] == before['db'] and after['journal'] == before['journal']
                  and (before['wal'] is None) == (after['wal'] is None)
                  and (before['wal'] is None or (after['wal'][1] == before['wal'][1] and after['wal'][0][0] >= wal_len)))
        if not stable:
            raise ClosureRefused('auto_source_unstable', 'database/WAL generation changed during the copy')
        with closing(sqlite3.connect(tmp)) as db:                    # the COPY: recovery replays its own wal/journal
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('PRAGMA journal_mode=DELETE')
            ok = db.execute('PRAGMA integrity_check').fetchone()[0]
        if ok != 'ok':
            raise ClosureRefused('auto_snapshot_integrity', str(ok)[:80])
        for suffix in ('-wal', '-shm', '-journal'):
            Path(str(tmp) + suffix).unlink(missing_ok=True)
        os.replace(tmp, dst)
    finally:
        for suffix in ('', '-wal', '-shm', '-journal'):
            Path(str(tmp) + suffix).unlink(missing_ok=True)
    return dict(path=str(dst), bytes=dst.stat().st_size, source_identity=dict(db=list(before['db']), wal_bytes=wal_len),
                consistency='STABLE_SOURCE_VERIFIED_BEFORE_AND_AFTER')


def snapshot_job(payload: dict) -> dict:
    """Child entry: copy the market (and optional derivatives) database into snapshot_dir, within one deadline."""
    stamp = int(payload['now_ms'])
    d = Path(payload['snapshot_dir'])
    deadline = time.monotonic() + float(payload['deadline_s'])
    reserve = int(payload.get('min_free_gb', 2) * GIB)
    out = dict(candles=copy_database(payload['market_db'], d / f'candles-{stamp}.db', deadline=deadline, min_free_bytes=reserve),
               derivs=None)
    if payload.get('derivs_db'):
        try:
            out['derivs'] = copy_database(payload['derivs_db'], d / f'derivs-{stamp}.db', deadline=deadline, min_free_bytes=reserve)
        except ClosureRefused:
            Path(out['candles']['path']).unlink(missing_ok=True)      # no half snapshot set is kept
            raise
    return out


def derivative_series_for(auto,cfg):
    """Only series the declared search, proposer vocabulary or costs use."""
    from ..compose import catalog
    from ...strategy.spec_evidence import _SERIES_FOR
    factors=(auto.get('search') or {}).get('factors')
    if factors is None or auto.get('bank_path'):
        factors=[f.key for f in catalog.FACTORS if auto['tf'] in f.horizons and not f.provider_gap]
    required={need for key in factors if key in catalog.BY_KEY for need in catalog.BY_KEY[key].requires}
    if (cfg.get('risk') or {}).get('real_funding',False) or auto.get('bank_path'):
        required.add('funding')
    return tuple(sorted({_SERIES_FOR[need] for need in required if need in _SERIES_FOR}))


def publish_job(payload: dict) -> dict:
    """Child entry (default): publish the bounded exact-cut study snapshot through the reader contract instead of
    physically copying every timeframe of the cache. Same result shape as snapshot_job."""
    from . import manifest, publish
    from ...research.compose import graph
    stamp = int(payload['now_ms'])
    d = Path(payload['snapshot_dir'])
    a = payload['auto']
    policy = manifest.configured_policy(payload['cfg'])
    warm = graph.MAX_WINDOW                       # rolling features need history before the fit window
    reserve=0 if recent_development(a) else int(policy['min_protected_bars'])+int(a['protected_extra_bars'])+int(a['embargo_bars'])
    bars=reserve+int(a['fit_bars'])+warm+16
    deadline = time.monotonic() + float(payload['deadline_s'])
    out = publish.publish_exact_cut(
        payload['market_db'], d / f'candles-{stamp}.db', tf=a['tf'], window_bars=bars, deadline=deadline,
        max_rows=int(a.get('max_rows', 5_000_000)),
        max_bytes=int(a.get('max_snapshot_payload_bytes', 512 * 1024 * 1024)),
        coverage_filter=dict(fit_bars=int(a['fit_bars']),
                             reserve_bars=reserve,
                             max_gap_bars=int(policy['max_gap_bars'])),
        symbols=a.get('symbols'),deriv_series=derivative_series_for(a,payload['cfg']),now_ms=stamp,retrospective=a['development_mode'] == 'RETROSPECTIVE',
        development_derivatives_only=bool(a.get('development_derivatives_only', False)),
        deriv_src=payload.get('derivs_db'), dst_derivs=(d / f'derivs-{stamp}.db') if payload.get('derivs_db') else None)
    universe=None
    if a.get('source_journal_path'):
        universe=publish.publish_universe(a['source_journal_path'],d/f'universe-{stamp}.db',
            start_ms=int(out['as_of_ms'])-bars*TF_MS[a['tf']],end_ms=stamp,deadline=deadline,
            max_bytes=min(512*1024*1024,max(0,int(a.get('max_snapshot_payload_bytes',512*1024*1024))-int(out['bytes'])-int((out.get('derivs') or {}).get('bytes',0)))))
    return dict(candles=dict(path=out['path'], bytes=out['bytes'], consistency=out['consistency'], rows=out['rows'],
                             revisions=out['revisions'], window_bars=bars, as_of_ms=out['as_of_ms'], coverage=out.get('coverage_filter'),
                             revision_scope=out['revision_scope']),
                derivs=out['derivs'],universe=universe)


def prune_snapshots(snapshot_dir, store, keep_unreferenced=0) -> list:
    """Remove snapshot copies no study references and no pending SNAPSHOT_READY names. Bounded disk use: failed or
    blocked preparations never accumulate copies."""
    d = Path(snapshot_dir)
    if not d.is_dir():
        return []
    held = set()
    for row in store.studies():
        pl = json.loads(row['payload'])
        held.update(str(pl.get(k)) for k in ('snapshot_path', 'derivs_path', 'world_path','universe_path') if pl.get(k))
    for ev in _ready(store):
        held.update(v for v in (ev['candles'], ev['derivs'],ev.get('universe')) if v)
    gone = []
    stale = sorted((p for pat in ('candles-*.db', 'derivs-*.db', 'world-*.db','universe-*.db') for p in d.glob(pat) if str(p) not in held),
                   key=lambda p: p.stat().st_mtime)
    for p in stale[:max(len(stale) - int(keep_unreferenced), 0)]:
        p.unlink(missing_ok=True)
        gone.append(p.name)
    return gone


# ── spec derived from measured coverage ────────────────────────────────────
def derive_spec(auto: dict, cfg: dict, snapshot_path, events, derivs_snapshot=None,universe_snapshot=None) -> dict:
    """{spec} or {blocked: reason}. Only symbols whose retained coverage satisfies the policy enter; a window
    with too few of them is a data blockage, reported, never padded."""
    from . import manifest
    tf = auto['tf']
    step = TF_MS[tf]
    with closing(manifest.readonly(manifest.refuse_live(snapshot_path))) as db:
        syms = [r[0] for r in db.execute('SELECT DISTINCT symbol FROM candles WHERE tf=? ORDER BY symbol', (tf,))]
    if not syms:
        return dict(blocked='no_symbols_in_snapshot')
    policy = manifest.configured_policy(cfg)
    m = manifest.build_manifest(snapshot_path, tf, syms, events)
    as_of = m['as_of_ms']
    protected_bars = int(policy['min_protected_bars']) + int(auto['protected_extra_bars'])
    protected_end = as_of - step
    protected_start = protected_end - (protected_bars - 1) * step
    embargo = int(auto['embargo_bars'])
    dev_end = protected_start - embargo * step
    if recent_development(auto):
        # Use the newest retained closed bars for retrospective development.
        # The envelope format still requires ordered independent boundaries;
        # these future placeholders are explicitly UNASSIGNED, not a promise
        # to wait for this period or a claim it has been sealed.
        dev_end=as_of
        protected_start=dev_end+embargo*step
        protected_end=protected_start+(protected_bars-1)*step
    fit_start = dev_end - int(auto['fit_bars']) * step
    inv, gap, mode = m['symbols'], policy['max_gap_bars'], auto['development_mode']
    ok = [s for s in syms if manifest._coverage(inv[s], fit_start, dev_end - step, step, gap, 1, mode) is None]
    ok.sort(key=lambda s: digest(s))                                   # deterministic, not performance-ranked
    held_n = max(int(policy['min_heldout_symbols']), int(round(len(ok) * float(auto['heldout_frac']))))
    if recent_development(auto):
        # This NEW study explicitly has no independent test window. Reserve
        # unused symbols where possible without blocking permitted development.
        # make_envelope still enforces the original protected-evidence floors;
        # no existing study, split or evidence reservation is changed here.
        held_n = min(held_n, max(0, len(ok) - int(policy['min_discovery_symbols'])))
    disc_n = len(ok) - held_n
    if disc_n < int(policy['min_discovery_symbols']):
        required = (f'{policy["min_discovery_symbols"]} discovery; independent test remains unassigned'
                    if recent_development(auto) else
                    f'{policy["min_discovery_symbols"]} discovery + {policy["min_heldout_symbols"]} held-out')
        return dict(blocked=f'insufficient_covered_symbols: {len(ok)} cover the fit window; need '
                            f'{required}')
    risk = {k: v for k, v in ((cfg or {}).get('risk') or {}).items()
            if k in ('taker_fee_pct', 'slippage_atr_frac', 'funding_rate_8h', 'real_funding', 'risk_per_trade_pct', 'max_open_trades', 'max_open_positions')}
    spec = dict(snapshot_path=str(snapshot_path), tf=tf, discovery_symbols=ok[held_n:], heldout_symbols=ok[:held_n],
                fit_start_ms=int(fit_start), dev_end_ms=int(dev_end), embargo_bars=embargo,
                protected_start_ms=int(protected_start), protected_end_ms=int(protected_end), policy=policy, risk=risk,
                development_mode=mode, search=dict(auto['search']), max_candidates=int(auto['max_candidates']),
                max_revisions=int(auto.get('max_revisions', 4)),
                unit_timeout_s=int(auto['unit_timeout_s']), workdir=str(Path(auto['snapshot_dir']) / 'workdir'),
                origin='auto')
    if recent_development(auto):
        spec['independent_window_assigned']=False
    for key in ('bank_path', 'references_path', 'world_path', 'source_journal_path', 'protected_derivs_path'):
        if auto.get(key):
            spec[key] = str(auto[key])
    if universe_snapshot:
        spec.update(universe_path=str(universe_snapshot),universe_environment=auto.get('universe_environment','demo'))
    if derivs_snapshot:                       # the derivatives COPY taken with the candles, never the live path
        spec['derivs_path'] = str(derivs_snapshot)
    elif auto.get('derivs_path'):
        spec['derivs_path'] = str(auto['derivs_path'])
    return dict(spec=spec, symbols_considered=len(syms), symbols_covered=len(ok), prepared_manifest=m)


def world_frames(snapshot_path, spec) -> dict:
    """Closed-bar frames of the study's DISCOVERY universe over its fit window, read through the same data-feed contract
    development uses (replay receipts for REPLAY, retained observations for RETROSPECTIVE)."""
    from . import manifest
    from ...data.feed import DataFeed
    from ..universe import NoExchange
    path = manifest.refuse_live(snapshot_path)
    tf, lo, hi = spec['tf'], int(spec['fit_start_ms']), int(spec['dev_end_ms'])
    step = TF_MS[tf]
    if spec['development_mode'] == 'RETROSPECTIVE':
        feed = DataFeed(exchange=NoExchange(), db_path=str(path), retrospective=dict(dataset_id='auto-world', cut_ms=hi, start_ms=lo))
        read = lambda sym: feed.cached_ohlcv(sym, tf, limit=200000)         # noqa: E731
    else:
        feed = manifest._feed(path, hi)
        read = lambda sym: feed.replay_ohlcv(sym, tf, 200000)               # noqa: E731
    frames = {}
    try:
        for sym in spec['discovery_symbols']:
            df = read(sym)
            if df is None or not len(df):
                continue
            ms = manifest._ms(df['ts'])
            df = df[(ms >= lo) & (ms + step <= hi)].reset_index(drop=True)
            if len(df):
                frames[sym] = df
    finally:
        if getattr(feed._local, 'conn', None) is not None:
            feed.db.close()
    return frames


def produce_world(auto, spec, snapshot_path, stamp) -> dict | None:
    """The bounded independent World store for this study (volume shortlist), or None when it cannot be produced. A missing
    store is reported by the study as a data gap for the factor that needs it; nothing is substituted."""
    if not auto.get('world', True) or spec.get('world_path'):
        return None
    from ..compose import catalog, graph, world_producer
    factors = (spec.get('search') or {}).get('factors')
    declared = spec.get('seed_graphs') or []
    # A bounded, explicitly specified technical study needs no historical World construction.
    # Open-ended search or retained questions can request World, so retain preparation there.
    if factors is not None and not spec.get('bank_path'):
        needs_world = any('world' in catalog.BY_KEY[k].requires for k in factors if k in catalog.BY_KEY)
        needs_world |= any('world' in graph.requires_of(g) for g in declared)
        if not needs_world:
            return None
    frames = world_frames(snapshot_path, spec)
    if not frames:
        return None
    path = Path(auto['snapshot_dir']) / f'world-{stamp}.db'
    return world_producer.produce_store(frames, spec['tf'], path, start_ms=int(spec['fit_start_ms']), end_ms=int(spec['dev_end_ms']),
                                        retrospective=spec['development_mode'] == 'RETROSPECTIVE',
                                        shortlist=int(auto.get('world_shortlist', 30)))


def create_job(payload: dict) -> dict:
    """Child entry: derive from the snapshot copy and create the study in the closure store."""
    from . import exposure, study
    store = Store(payload['store_path'])
    try:
        out = derive_spec(payload['auto'], payload['cfg'], payload['snapshot_path'], exposure.events(store),
                          derivs_snapshot=payload.get('derivs_snapshot'),universe_snapshot=payload.get('universe_snapshot'))
        if 'spec' not in out:
            return out
        try:
            world = produce_world(payload['auto'], out['spec'], payload['snapshot_path'], int(payload['now_ms']))
        except Exception as exc:                  # noqa: BLE001 - independent studies may continue
            store.log_schedule(int(payload['now_ms']),'WORLD_PREPARATION_FAILED',
                               detail=dict(error=type(exc).__name__+': '+str(exc)[:200]))
            world = None
        if world is not None:
            out['spec']['world_path'] = world['path']
        sid = study.create_study(store, out['spec'], now_ms=int(payload['now_ms']),
                                 prepared_manifest=out['prepared_manifest'])
        return dict(study_id=sid, world=None if world is None else dict(cuts=world['cuts'], shortlist=world['shortlist']), symbols_considered=out['symbols_considered'], symbols_covered=out['symbols_covered'])
    finally:
        store.close()


from .store import Store      # noqa: E402  (after the module-level jobs so spawn imports stay light)


# ── scheduling ─────────────────────────────────────────────────────────────
def auto_studies(store) -> list:
    out = []
    for row in store.studies():
        payload = json.loads(row['payload'])
        if payload.get('origin') == 'auto':
            out.append((row, payload))
    return out


def waiting_eligible(store, study_id, unit) -> bool:
    """A WAITING_DATA unit is worth a step only when its awaited series is NOW served (the same read-only loader and
    coverage preflight development uses). A permanently missing dataset therefore never keeps selecting its study."""
    from . import data_access
    row = store.study(study_id)
    study = json.loads(row['payload'])
    env = store.get_once('envelopes', study['envelope_id'])
    cand = store.get_once('candidates', unit['unit_id'].split(':', 1)[1])
    need = (json.loads(unit['result']) if unit['result'] else {}).get('waiting_requirements') or []
    return bool(cand is not None and env is not None and data_access.eligible_now(store, study, env, cand, need))


def runnable(store, study_id) -> str | None:
    """'DATA' when a waiting candidate's data has arrived, 'WORK' when a bounded unit may run now, else None.
    Waiting-only studies are NOT runnable (they stay in Needs You) so the next due question/domain can start."""
    units = store.units(study_id)
    done = {u['unit_id'] for u in units if u['status'] in ('DONE', 'FAILED')}
    for u in units:
        if u['status'] == 'WAITING_DATA' and waiting_eligible(store, study_id, u):
            return 'DATA'
    for u in units:
        if (u['status'] in ('PENDING', 'RUNNING') and u['kind'] in STEP_KINDS
                and all(d in done for d in json.loads(u['deps']))):
            return 'WORK'
    return 'WORK' if not units else None      # a created study with no units yet is planned on its first step


ATTEMPTS = ('STUDY_CREATED', 'DATA_BLOCKED', 'PREP_FAILED')         # every preparation attempt, not only successes
MIN_PREP_S = 30.0                                                     # below this the slot cannot make progress on a copy


def _events(store, status=None, limit=500):
    out = []
    for e in store.schedule_events(limit):
        if status is None or e['status'] in (status if isinstance(status, tuple) else (status,)):
            out.append(dict(e, detail=json.loads(e['detail']) if e.get('detail') else None))
    return out


def due(store, auto, now_ms) -> tuple:
    """(due, reason). A success waits `interval_hours`; a failed or blocked preparation backs off exponentially from
    `retry_hours` up to the interval, so a persistent fault cannot copy a multi-GB database on every scheduled slot."""
    attempts = _events(store, ATTEMPTS)
    if not attempts:
        return True, 'no previous attempt'
    last = attempts[0]
    fails = 0
    for e in attempts:
        if e['status'] == 'STUDY_CREATED':
            break
        fails += 1
    interval = float(auto['interval_hours'])
    wait_h = interval if last['status'] == 'STUDY_CREATED' else min(interval, float(auto.get('retry_hours', 1.0)) * 2 ** max(fails - 1, 0))
    if now_ms - last['at_ms'] >= wait_h * 3_600_000:
        return True, f'{wait_h:g}h since last {last["status"]}'
    return False, f'next attempt {wait_h:g}h after last {last["status"]} ({fails} consecutive unsuccessful)'


def _ready(store) -> list:
    """Snapshot copies taken but not yet consumed by a created/blocked outcome (resume instead of copying again)."""
    consumed = {(e['detail'] or {}).get('snapshot') for e in _events(store, ('STUDY_CREATED', 'DATA_BLOCKED'))}
    out = []
    for e in _events(store, 'SNAPSHOT_READY'):
        d = e['detail'] or {}
        if d.get('candles') not in consumed and d.get('candles') and Path(d['candles']).is_file():
            out.append(d)
    return out


def tick(cfg, store, auto, *, now_ms, budget_s, cycle_seconds=None, should_stop=lambda: False, child=True) -> dict:
    from . import study
    with study.single_flight(store) as free:
        if not free:
            return dict(status='ALREADY_RUNNING')
        return _tick(cfg, store, auto, now_ms=now_ms, budget_s=budget_s,
                     cycle_seconds=cycle_seconds, should_stop=should_stop, child=child)


def _tick(cfg, store, auto, *, now_ms, budget_s, cycle_seconds=None, should_stop=lambda: False, child=True) -> dict:
    """One scheduler invocation under ONE monotonic deadline (`budget_s`): every child gets the time remaining, never a
    fresh full timeout. Always records its outcome in the store."""
    from . import study
    t0 = time.monotonic()
    deadline = t0 + float(budget_s)

    def left():
        return deadline - time.monotonic()

    def finish(status, **kw):
        rec = dict(status=status, **kw)
        latest = store.schedule_events(1)
        quiet = status in ('IDLE', 'DEFERRED_BUDGET') and latest and latest[0]['status'] == status   # no log flooding
        if not quiet:
            store.log_schedule(now_ms, status, study_id=kw.get('study_id'), stop_reason=kw.get('stop_reason'),
                               units_run=kw.get('units_run'), elapsed_s=round(time.monotonic() - t0, 2), detail=kw.get('detail'))
        return rec

    from .manifest import evaluator_id
    current_evaluator = evaluator_id()
    ranked = []
    for row, definition in auto_studies(store):
        env = store.get_once('envelopes', definition['envelope_id'])
        if env is not None and env['evaluator_id'] != current_evaluator:
            if row['status'] not in ('COMPLETED', 'SUPERSEDED'):
                with store._tx() as db:
                    db.execute("UPDATE studies SET status='SUPERSEDED',stop_reason='evaluator_changed',next_work=NULL WHERE study_id=?",
                               (row['study_id'],))
                store.log_schedule(now_ms,'STUDY_SUPERSEDED',study_id=row['study_id'],
                    detail=dict(reason='evaluator_changed; retained evidence is historical, never rewritten',
                                previous=env['evaluator_id'],current=current_evaluator))
            continue
        kind = runnable(store,row['study_id'])
        if kind:
            ranked.append((row,kind))
    data_first = [r for r, kind in ranked if kind == 'DATA']                # arrived data resumes its candidate first
    sid = (data_first or [r for r, _ in ranked] or [None])[-1]
    sid = sid['study_id'] if sid else None
    if sid is None:
        ready = _ready(store)
        if not ready:
            ok, why = due(store, auto, now_ms)
            if not ok:
                return finish('IDLE', detail=why)
        if left() < MIN_PREP_S:
            return finish('DEFERRED_BUDGET', detail=f'{left():.0f}s left in the slot; preparation needs at least {MIN_PREP_S:.0f}s')
        # Rotate attempted preparations too: missing one horizon must not
        # permanently starve the others. Resume always uses the saved choice.
        prep_count = len(_events(store, 'PREPARING'))
        legacy_horizon = dict(tf=auto['tf'],fit_bars=auto['fit_bars'])
        auto = horizon_for(auto, prep_count)
        base = dict(now_ms=now_ms, snapshot_dir=auto['snapshot_dir'], market_db=auto['market_db'], derivs_db=auto.get('derivs_db'),
                    min_free_gb=float(auto.get('min_free_gb', 2.0)))
        if ready:
            snap = ready[-1]
            auto = dict(auto, **snap.get('horizon', legacy_horizon))
        else:
            job = snapshot_job if auto.get('snapshot_mode') == 'copy' else publish_job
            store.log_schedule(now_ms, 'PREPARING', detail=dict(
                stage='copy' if auto.get('snapshot_mode') == 'copy' else 'bounded_publication',
                tf=auto['tf'], fit_bars=auto['fit_bars'],
                timeout_s=min(float(auto['prep_timeout_s']), left()), memory_mb=auto['prep_memory_mb']))
            res = _prepare(job, base, auto, cfg, timeout_s=min(float(auto['prep_timeout_s']), left()), child=child,
                           should_stop=should_stop)
            if not res.ok:
                if not getattr(res, 'terminated', True):
                    study.fence_child(store, res, stage='preparation', attempt_ms=now_ms)
                    return finish('CHILD_OWNERSHIP_UNKNOWN', detail=dict(stage='preparation'))
                return finish('PREP_FAILED', detail=dict(stage='snapshot', error=str(res.error)[-200:], timed_out=res.timed_out))
            snap = dict(candles=res.value['candles']['path'], derivs=(res.value['derivs'] or {}).get('path'),
                        universe=(res.value.get('universe') or {}).get('path'),
                        consistency=res.value['candles']['consistency'], bytes=res.value['candles']['bytes'],
                        coverage=res.value['candles'].get('coverage'),
                        horizon=dict(tf=auto['tf'], fit_bars=auto['fit_bars']))
            store.log_schedule(now_ms, 'SNAPSHOT_READY', detail=snap)
        if left() < MIN_PREP_S:                      # copy done but no time to derive: resume from the copy next slot
            return finish('DEFERRED_BUDGET', detail=dict(reason='snapshot ready; study creation deferred', snapshot=snap['candles']))
        made = _run(create_job, dict(store_path=str(store.path), auto=auto, cfg=_slim(cfg), now_ms=now_ms,
                                     snapshot_path=snap['candles'], derivs_snapshot=snap.get('derivs'),universe_snapshot=snap.get('universe')),
                    dict(timeout_s=min(float(auto['prep_timeout_s']), left()), memory_mb=auto['prep_memory_mb']), child,
                    should_stop=should_stop)
        if not made.ok:
            if not getattr(made, 'terminated', True):
                study.fence_child(store, made, stage='create', attempt_ms=now_ms, snapshot_path=snap['candles'],
                    pending_paths=[str(Path(auto['snapshot_dir'], f'world-{int(now_ms)}.pending'))])
                return finish('CHILD_OWNERSHIP_UNKNOWN', detail=dict(stage='create'))
            # create_job can commit its exact study before its result is lost.
            # Reconcile by owned input identity, never start a replacement study.
            from ...core.child import ChildResult
            row = store._conn().execute("SELECT study_id,payload FROM studies WHERE "
                "json_extract(payload,'$.snapshot_path')=? AND json_extract(payload,'$.origin')='auto' "
                "ORDER BY rowid DESC LIMIT 1", (snap['candles'],)).fetchone()
            if row is not None and getattr(made, 'terminated', True):
                committed = json.loads(row['payload'])
                made = ChildResult(ok=True, value=dict(study_id=row['study_id'],
                    symbols_covered=len(committed['discovery_symbols']) + len(committed['heldout_symbols']),
                    symbols_considered=len(committed['discovery_symbols']) + len(committed['heldout_symbols']),
                    recovered_committed_attempt=True))
            if getattr(made, 'terminated', True):
                Path(auto['snapshot_dir'], f'world-{int(now_ms)}.pending').unlink(missing_ok=True)
        if not made.ok:
            return finish('PREP_FAILED', detail=dict(stage='study', error=str(made.error)[-200:], timed_out=made.timed_out,
                                                     snapshot=snap['candles']))
        if 'study_id' not in made.value:
            out = finish('DATA_BLOCKED', detail=dict(reason=made.value.get('blocked'), snapshot=snap['candles']))
            prune_snapshots(auto['snapshot_dir'], store)               # a blocked copy backs nothing: free the disk now
            return out
        store.log_schedule(now_ms, 'STUDY_CREATED', study_id=made.value['study_id'], detail=dict(
            snapshot=snap['candles'], covered=made.value['symbols_covered'], considered=made.value['symbols_considered']))
        sid = made.value['study_id']
        prune_snapshots(auto['snapshot_dir'], store)
    remaining = max(left(), 0.0)
    out = study._step(store, sid, remaining, now_ms, time.monotonic, cycle_seconds, 45.0, None, child,
                      should_stop, cfg, allow_heavy=False, extra_kinds=('intake', 'search'))
    return finish('STEPPED', study_id=sid, stop_reason=out.get('step'), units_run=out.get('units_run'),
                  detail=dict(units=out.get('units'), research_outcome=out.get('research_outcome'),
                              waiting_for_data=out.get('waiting_for_data')))


#: the ONLY configuration a spawned preparation child receives: policy numbers and cost fields, by name
RESEARCH_KEYS = ('min_discovery_symbols', 'min_threshold_samples', 'min_finite_frac', 'discovery_null_draws', 'seed',
                 'fdr_target', 'lord_w0', 'referee_max_draws', 'grow_max_p')
RISK_KEYS = ('taker_fee_pct', 'slippage_atr_frac', 'funding_rate_8h', 'real_funding', 'risk_per_trade_pct', 'max_open_trades', 'max_open_positions')


def _slim(cfg) -> dict:
    """Explicit allowlist: never the whole config, never a section assumed harmless."""
    r, k = (cfg or {}).get('research') or {}, (cfg or {}).get('risk') or {}
    return dict(research={x: r[x] for x in RESEARCH_KEYS if x in r}, risk={x: k[x] for x in RISK_KEYS if x in k})


def _run(fn, payload, prep, child, should_stop=None):
    from ...core.child import ChildResult, run_child
    if not child:
        try:
            return ChildResult(ok=True, value=fn(payload))
        except ClosureRefused as exc:
            return ChildResult(ok=False, error='refused:' + exc.code)
    return run_child(fn, payload, timeout_s=prep['timeout_s'], nice=19, memory_mb=prep['memory_mb'],
                     stop_requested=should_stop)


def _prepare(job, base, auto, cfg, *, timeout_s, child, should_stop=None):
    """Own the child's staging directory so cancellation cannot strand large partial copies.

    Only completed files are linked into the study-input directory; an existing
    file is never replaced. The source databases are never in this directory.
    """
    from ...core.child import ChildResult
    root = Path(base['snapshot_dir'])
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.preparing-', dir=root))
    installed = []
    complete = False
    res = None
    try:
        res = _run(job, dict(base, snapshot_dir=str(staging), auto=auto, cfg=_slim(cfg),
                             deadline_s=max(timeout_s - 2.0, 1.0)),
                   dict(timeout_s=timeout_s, memory_mb=auto['prep_memory_mb']), child, should_stop=should_stop)
        if not res.ok:
            if not getattr(res, 'terminated', True):
                res.owned_pending = (str(staging),)
            return res
        value = dict(res.value)
        for key in ('candles', 'derivs','universe'):
            if value.get(key) is None:
                continue
            item = dict(value[key])
            path = Path(item['path'])
            if path.is_symlink() or path.resolve().parent != staging.resolve() or not path.is_file():
                raise ClosureRefused('auto_prepared_path_not_owned')
            target = root / path.name
            os.link(path, target)
            installed.append((target, path.stat().st_ino))
            value[key] = dict(item, path=str(target))
        res.value = value
        complete = True
        return res
    except Exception as exc:
        return ChildResult(ok=False, error='preparation:' + (exc.code if isinstance(exc, ClosureRefused) else type(exc).__name__))
    finally:
        reaped = res is None or getattr(res, 'terminated', True)
        if not complete and reaped:
            for target, inode in installed:
                if target.exists() and target.stat().st_ino == inode:
                    target.unlink()
        if reaped:
            shutil.rmtree(staging)


# ── owner evidence (read-only) ─────────────────────────────────────────────
def _clip(v, n=1500):
    text = canonical(v)
    return v if len(text) <= n else dict(truncated=True, bytes=len(text), sha256=digest(v))


def _candidates(db, envelope_id, have, limit=8) -> list:
    """Per attempted candidate: the question, the graph, factor roles, fitted models, alternatives (component removals),
    regime / marked-drawdown diagnostics, input lineage, outcome and the exact reason. Development evidence only."""
    if not envelope_id or 'runs' not in have:
        return []
    out = []
    for (raw,) in db.execute('SELECT payload FROM runs ORDER BY rowid DESC'):
        r = json.loads(raw)
        if r.get('envelope_id') != envelope_id or r.get('stage') != 'DEVELOPMENT':
            continue
        cand = None
        if 'candidates' in have:
            row = db.execute('SELECT payload FROM candidates WHERE id=?', (r.get('candidate_id'),)).fetchone()
            cand = json.loads(row[0]) if row else None
        gf = r.get('graph_fit') or {}
        d = r.get('diagnostics') or {}
        out.append(dict(
            candidate_id=r.get('candidate_id'), outcome=r.get('outcome'), reason=(r.get('reason') or '')[:240],
            question=(cand or {}).get('question'), structure=(cand or {}).get('structure'),
            graph=_clip({k: (cand or {}).get(k) for k in ('long', 'short', 'exit', 'roles', 'target')}) if cand else None,
            requires=r.get('requires'), metrics=r.get('metrics'), alternatives=r.get('baselines'),
            validation_readiness=r.get('validation_readiness'),
            models=_clip(gf.get('models')), walk_forward=_clip(gf.get('walk_forward')),
            regimes=_clip(d.get('regime')), mtm=_clip(d.get('mtm')), contribution=_clip(d.get('contribution')),
            factor_roles=_clip(d.get('roles'), 4000),
            context_gaps=_clip(r.get('context_gaps')), input_lineage=_clip(r.get('context_provenance'), 4000),
            deployment=_clip(((r.get('hedge') or {}).get('deployment'))),
            evaluated=_clip(dict(combo=r.get('combo'), resolved_graph_id=r.get('resolved_graph_id'), fit_window=r.get('fit_window'),
                                 threshold_fit=r.get('threshold_fit')), 3000),
            combo_hash=r.get('combo_hash'), lineage=_lineage(db, r.get('candidate_id'), have),
            hedge=_clip(r.get('hedge'), 3000),
            detail_ref=dict(query='research', identity=f"closure-candidate:{r.get('candidate_id')}",
                            sections='closure-run:<run_id>:<section>:<part> after selecting an immutable run from the candidate catalogue'),
            data_id=r.get('data_id'), result_id=r.get('result_id'),
            authority='RESEARCH_ONLY: development evidence is not qualification'))
        if len(out) >= limit:
            break
    return out


def _lineage(db, candidate_id, have, limit=12, tick=None) -> list:
    """Revision/intake lineage of a candidate from the work-item table (who it derives from, what changed and why). The limit and
    order are applied IN SQL (indexed on candidate_id); only `limit` payloads are ever decoded."""
    if 'items' not in have:
        return []
    out = []
    for kind, status, reason, payload in db.execute('SELECT kind,status,reason,CASE WHEN length(CAST(payload AS BLOB))<=? THEN payload END FROM items WHERE candidate_id=? '
                                                    'ORDER BY updated_ms DESC LIMIT ?', (MAX_RECORD_BYTES, candidate_id, limit)):
        if tick is not None:
            tick()
        if payload is None:
            out.append(dict(kind=kind, status='TOO_LARGE', reason='lineage record exceeds read limit'))
            continue
        p = json.loads(payload)
        prov = p.get('provenance') or {}
        out.append(dict(kind=kind, status=status, reason=(reason or '')[:160], parent=prov.get('parent_candidate_id'),
                        change=_clip(prov.get('change'), 400), root=prov.get('root'), intent=prov.get('intent'),
                        question_id=p.get('question_id')))
    return out


WIRE_CHUNK_BYTES = 8000            # per chunk, measured as the JSON-ENCODED text the owner envelope actually carries (quotes/escapes included)
RESULT_PAGE = 20
MAX_RECORD_BYTES = 8 * 1024 * 1024  # a stored record larger than this is reported TOO_LARGE, never decoded
DETAIL_SECONDS = 3.0                # statement progress limit for the whole owner GET
LEGACY_SCAN_RUNS = 400


def _sections(cand, last, lineage, results_page) -> dict:
    """The exact stored content of each section, from the SELECTED development result (default: the latest)."""
    last = last or {}
    gf = last.get('graph_fit') or {}
    return {
        'graph': cand,
        'evaluated': dict(combo=last.get('combo'), combo_hash=last.get('combo_hash'), resolved_graph_id=last.get('resolved_graph_id'),
                          fit_window=last.get('fit_window'), threshold_fit=last.get('threshold_fit'), requires=last.get('requires')),
        'models': dict(models=gf.get('models'), walk_forward=gf.get('walk_forward'), thresholds=gf.get('thresholds')),
        'diagnostics': dict(metrics=last.get('metrics'), baselines=last.get('baselines'), diagnostics=last.get('diagnostics')),
        'hedge': last.get('hedge'),
        'lineage': dict(items=lineage, context_provenance=last.get('context_provenance'), context_gaps=last.get('context_gaps')),
        'results': results_page,
    }


def wire_chunks(text: str, budget: int = WIRE_CHUNK_BYTES, *, _tick=None) -> list:
    """Split `text` into valid-Unicode chunks whose JSON-ENCODED size (what the owner envelope carries, with every quote, backslash,
    control and non-ASCII escape counted) never exceeds `budget`. Never splits a character; concatenation is exactly `text`."""
    chunks, cur, used, widths = [], [], 0, {}
    for i, ch in enumerate(text):
        if _tick is not None and i % 2048 == 0:
            _tick()
        w = widths.get(ch)
        if w is None:
            w = widths[ch] = len(json.dumps(ch)) - 2     # escaped width of this character inside a JSON string (cached per character)
        if used + w > budget and cur:
            chunks.append(''.join(cur))
            cur, used = [], 0
        cur.append(ch)
        used += w
    chunks.append(''.join(cur))
    return chunks


class _DetailDeadline(Exception):
    pass


class _DetailTooLarge(Exception):
    def __init__(self, what, size):
        self.what, self.size = what, size


def _detail_tick(deadline):
    import time
    if time.monotonic() >= deadline:
        raise _DetailDeadline()


def _detail_json(db, table, key, deadline):
    # Table names are internal literals, never caller input. Check bytes BEFORE fetching/decoding the payload.
    _detail_tick(deadline)
    size = db.execute(f'SELECT length(CAST(payload AS BLOB)) FROM {table} WHERE id=?', (key,)).fetchone()
    if size is None:
        return None
    if size[0] > MAX_RECORD_BYTES:
        raise _DetailTooLarge(table, size[0])
    _detail_tick(deadline)
    raw = db.execute(f'SELECT payload FROM {table} WHERE id=?', (key,)).fetchone()[0]
    _detail_tick(deadline)
    value = json.loads(raw)
    _detail_tick(deadline)
    return value


def candidate_detail(store_path, candidate_id, section=None, part=0, result=None, *, _deadline=None):
    """Read one immutable result, or page its catalogue. `result` is the EXACT run id, not a prefix or page position.
    Only the selected full result is decoded; catalogue summaries are extracted by bounded SQL. Every phase shares one
    deadline, including JSON and Unicode chunking. This is a direct HTTP read, creates no Store and grants no authority."""
    import time
    p = Path(store_path)
    if not p.is_file():
        raise ValueError('closure_store_not_found')
    if not isinstance(candidate_id, str) or not 1 <= len(candidate_id) <= 80:
        raise ValueError('candidate_id_invalid')
    if type(part) is not int or part < 0:
        raise ValueError('part_out_of_range')
    if result is not None and (not isinstance(result, str) or not 1 <= len(result) <= 80):
        raise ValueError('run_id_invalid')
    deadline = time.monotonic() + DETAIL_SECONDS if _deadline is None else _deadline
    tick = lambda: _detail_tick(deadline)
    try:
        tick()
        with closing(sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'candidates' not in have:
                return dict(status='ABSENT', candidate_id=candidate_id)
            cand = _detail_json(db, 'candidates', candidate_id, deadline)
            if cand is None:
                return dict(status='ABSENT', candidate_id=candidate_id)
            indexed = 'run_index' in have and db.execute(
                "SELECT 1 FROM run_index WHERE candidate_id=? LIMIT 1", (candidate_id,)).fetchone() is not None
            offset = part * RESULT_PAGE if section == 'results' else 0
            tick()
            if result is not None and section != 'results':
                total, ids = 0, []        # exact immutable read needs no catalogue scan
            elif indexed:
                total = db.execute("SELECT count(*) FROM run_index WHERE candidate_id=? AND stage='DEVELOPMENT'", (candidate_id,)).fetchone()[0]
                ids = [r[0] for r in db.execute("SELECT run_id FROM run_index WHERE candidate_id=? AND stage='DEVELOPMENT' "
                                                'ORDER BY rowid DESC LIMIT ? OFFSET ?', (candidate_id, RESULT_PAGE, offset))]
            else:
                # A pre-index store has an explicitly incomplete, bounded catalogue. Never scan all its payloads.
                ids_all = []
                if 'runs' in have:
                    for rid, n, cid, stage in db.execute(
                        "SELECT id,length(CAST(payload AS BLOB)),CASE WHEN length(CAST(payload AS BLOB))<=? THEN json_extract(payload,'$.candidate_id') END,"
                        "CASE WHEN length(CAST(payload AS BLOB))<=? THEN json_extract(payload,'$.stage') END "
                        'FROM runs ORDER BY rowid DESC LIMIT ?', (MAX_RECORD_BYTES, MAX_RECORD_BYTES, LEGACY_SCAN_RUNS)):
                        tick()
                        if cid == candidate_id and stage == 'DEVELOPMENT':
                            ids_all.append(rid)
                total, ids = len(ids_all), ids_all[offset:offset + RESULT_PAGE]
            summaries = []
            for rid in ids:
                tick()
                if indexed:
                    row = db.execute('SELECT result_id FROM run_index WHERE run_id=?', (rid,)).fetchone()
                    meta = db.execute('SELECT outcome,reason,data_id FROM run_summaries WHERE run_id=?', (rid,)).fetchone() if 'run_summaries' in have else None
                    summaries.append(dict(run_id=rid, result_id=row[0] if row else None,
                                          outcome=meta[0] if meta else None, reason=(meta[1] or '')[:48] if meta else None,
                                          data_id=meta[2] if meta else None, summary_status='RECORDED_PREVIEW' if meta else 'NOT_RECORDED'))
                else:
                    # Exact legacy details remain readable by id. The catalogue does not decode their full result bodies.
                    summaries.append(dict(run_id=rid, summary_status='NOT_INDEXED'))
            chosen, chosen_id = None, None
            if section != 'results':
                chosen_id = result if result is not None else (ids[0] if ids else None)
                if chosen_id is not None:
                    if indexed:
                        bound = db.execute('SELECT candidate_id,stage FROM run_index WHERE run_id=?', (chosen_id,)).fetchone()
                        if bound is not None and bound != (candidate_id, 'DEVELOPMENT'):
                            return dict(status='RESULT_ABSENT', candidate_id=candidate_id)
                    chosen = _detail_json(db, 'runs', chosen_id, deadline)
                    if chosen is None or chosen.get('candidate_id') != candidate_id or chosen.get('stage') != 'DEVELOPMENT':
                        return dict(status='RESULT_ABSENT', candidate_id=candidate_id)
            tick()
            lineage = _lineage(db, candidate_id, have, tick=tick)
            tick()
        page = dict(total=total, offset=offset, page=RESULT_PAGE, items=summaries,
                    more_results=total > offset + len(summaries), results_may_be_incomplete=not indexed,
                    catalog_scope='INDEXED_DEVELOPMENT_RESULTS' if indexed else 'BOUNDED_LEGACY_WINDOW',
                    detail_route='closure-run:<run_id>:<section>:<part>')
        secs = _sections(cand, chosen, lineage, page)
        keys = list(secs) if section is None else [section]
        if any(k not in secs for k in keys):
            raise ValueError('section_unknown')
        texts, pieces = {}, {}
        for key in keys:
            tick()
            text = json.dumps(secs[key], sort_keys=True, separators=(',', ':'), allow_nan=False, ensure_ascii=False)
            tick()
            if len(text.encode()) > MAX_RECORD_BYTES:
                raise _DetailTooLarge(key, len(text.encode()))
            texts[key] = text
            pieces[key] = wire_chunks(text, _tick=tick) if key != 'results' else [text]
            tick()
        if section is None:
            return dict(status='AVAILABLE', candidate_id=candidate_id, run_id=chosen_id,
                        results=page, results_may_be_incomplete=not indexed,
                        sections={k: dict(chars=len(texts[k]), parts=len(pieces[k])) for k in keys}, wire_chunk_bytes=WIRE_CHUNK_BYTES)
        idx = 0 if section == 'results' else part
        if idx >= len(pieces[section]):
            raise ValueError('part_out_of_range')
        checksum = digest(secs[section])
        tick()
        return dict(status='AVAILABLE', candidate_id=candidate_id, run_id=chosen_id, result_id=(chosen or {}).get('result_id'),
                    section=section, part=idx, parts=len(pieces[section]), chars=len(texts[section]),
                    sha256=checksum, text=pieces[section][idx], results_may_be_incomplete=not indexed)
    except _DetailDeadline:
        return dict(status='TIME_LIMIT', candidate_id=candidate_id, seconds=DETAIL_SECONDS)
    except _DetailTooLarge as exc:
        return dict(status='TOO_LARGE', candidate_id=candidate_id, what='candidate' if exc.what == 'candidates' else exc.what, bytes=exc.size)
    except sqlite3.OperationalError as exc:
        if 'interrupted' in str(exc).lower():
            return dict(status='TIME_LIMIT', candidate_id=candidate_id, seconds=DETAIL_SECONDS)
        raise


def run_detail(store_path, run_id, section, part=0):
    """Resolve one exact immutable run id without duplicating 64-character ids in the owner's 128-character query field."""
    import time
    if not isinstance(run_id, str) or not 1 <= len(run_id) <= 80:
        raise ValueError('run_id_invalid')
    p = Path(store_path)
    if not p.is_file():
        raise ValueError('closure_store_not_found')
    deadline = time.monotonic() + DETAIL_SECONDS
    try:
        _detail_tick(deadline)
        with closing(sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            row = db.execute('SELECT candidate_id,stage FROM run_index WHERE run_id=?', (run_id,)).fetchone() if 'run_index' in have else None
            if row is None:
                item = _detail_json(db, 'runs', run_id, deadline) if 'runs' in have else None
                row = (item.get('candidate_id'), item.get('stage')) if item else None
            if row is None or row[1] != 'DEVELOPMENT':
                return dict(status='ABSENT', run_id=run_id)
            _detail_tick(deadline)
        return candidate_detail(store_path, row[0], section, part, result=run_id, _deadline=deadline)
    except _DetailDeadline:
        return dict(status='TIME_LIMIT', run_id=run_id, seconds=DETAIL_SECONDS)
    except _DetailTooLarge as exc:
        return dict(status='TOO_LARGE', run_id=run_id, what=exc.what, bytes=exc.size)
    except sqlite3.OperationalError as exc:
        if 'interrupted' in str(exc).lower():
            return dict(status='TIME_LIMIT', run_id=run_id, seconds=DETAIL_SECONDS)
        raise


def _qualifications(db, envelope_id, have) -> list:
    """Qualification packets of this study's envelope (alpha or protective hedge): kind, status, reason, admission state. A
    protective-hedge ASSESSED_* status is independent risk assessment, never statistical qualification or trading authority."""
    if not envelope_id or 'qualifications' not in have:
        return []
    out = []
    for (raw,) in db.execute('SELECT payload FROM qualifications ORDER BY rowid DESC LIMIT 50'):
        q = json.loads(raw)
        if (q.get('envelope') or {}).get('envelope_id') != envelope_id:
            continue
        out.append(dict(kind='hedge_protective' if str(q.get('schema', '')).startswith('closure-hedge') else 'alpha',
                        status=q.get('status'), reason=(q.get('reason') or '')[:240], qualification_id=q.get('qualification_id'),
                        authority_class=q.get('authority_class'), rule_id=q.get('rule_id'),
                        statistical_admission=(q.get('statistical_admission') or {}).get('status'), gaps=q.get('gaps')))
    return out[:5]


def _resources(db, study_id, have) -> dict:
    if 'resource_ledger' not in have:
        return dict(attempts=0, seconds=0.0)
    n, sec, bad = db.execute("SELECT COUNT(*), COALESCE(SUM(elapsed_s),0), COALESCE(SUM(outcome NOT IN ('DONE','WAITING_DATA')),0) "
                             'FROM resource_ledger WHERE study_id=?', (study_id,)).fetchone()
    return dict(attempts=int(n), seconds=round(float(sec), 2), interrupted_or_failed_attempts=int(bad),
                note='wall seconds of every attempt including timeouts and interrupted runs')


def evidence(store_path, limit_studies=5) -> dict:
    """Studies, units, stage timings, outcomes and recent scheduler events from the configured store, read-only."""
    p = Path(store_path)
    if not p.is_file():
        raise ValueError('closure_store_not_found')
    with closing(sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        studies = []
        if 'studies' in have:
            for s in db.execute('SELECT * FROM studies ORDER BY rowid DESC LIMIT ?', (limit_studies,)):
                payload = json.loads(s['payload'])
                units = []
                for u in db.execute('SELECT * FROM units WHERE study_id=? ORDER BY seq', (s['study_id'],)):
                    r = json.loads(u['result']) if u['result'] else {}
                    units.append(dict(unit=u['unit_id'], kind=u['kind'], status=u['status'], attempts=u['attempts'],
                                      elapsed_s=r.get('elapsed_s'), started_ms=r.get('started_ms'), ended_ms=r.get('ended_ms'),
                                      outcome=r.get('outcome') or r.get('status') or r.get('refused'),
                                      reason=(r.get('reason') or u['note'] or '')[:200]))
                studies.append(dict(study_id=s['study_id'], origin=payload.get('origin'), status=s['status'],
                                    stop_reason=s['stop_reason'], steps=s['steps'], last_progress_ms=s['last_progress_ms'],
                                    next_work=s['next_work'], development_mode=payload.get('development_mode'), units=units,
                                    cost_assumptions=payload.get('risk'), envelope_id=payload.get('envelope_id'),
                                    candidates=_candidates(db, payload.get('envelope_id'), have),
                                    resources=_resources(db, s['study_id'], have),
                                    qualifications=_qualifications(db, payload.get('envelope_id'), have)))
        events = [dict(r) for r in db.execute('SELECT at_ms,status,study_id,stop_reason,units_run,elapsed_s FROM schedule_events '
                                              'ORDER BY seq DESC LIMIT 20')] if 'schedule_events' in have else []
    return dict(studies=studies, schedule_events=events, catalog_limits=dict(studies=limit_studies, candidates_per_study=8),
                authority='RESEARCH_ONLY: development evidence is not qualification',
                note='qualification and handoff are operator steps and are never run by the scheduler')
