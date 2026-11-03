"""P1 — snapshot-only manifest, controlled exposure registry, policy binding
and the immutable study envelope.

* Snapshot: ONE quiescent, self-contained file (no -wal/-shm/-journal, a
  single hard link, not any configured/live database by name, location or
  inode). Identity (dev, inode, size, mtime, sha256) is checked again before
  any sealing read; development re-verifies the identity of the exact rows it
  consumes, so it neither trusts a stale cache nor reads protected rows.
* Exposure: states are DERIVED from a controlled registry (`exposure.py`
  events), never from a caller's free-text claim. UNEXPOSED needs a
  FORWARD_SEAL recorded before the window's first retained availability (the
  data could not have been seen when the seal was made). SYNTHETIC_DECLARED is
  accepted only by TEST_ONLY policy and can never reach Factory authority.
  Anything else is UNKNOWN, and UNKNOWN is never protected evidence.
* Policy: `configured_policy(cfg)` reads the strictness in force (symbol
  floors, threshold samples, budget, draws). An envelope below it is TEST_ONLY.
* Data modes: protected evidence is always REPLAY (retained first-close
  receipts). Development may be REPLAY or RETROSPECTIVE (existing
  `DataFeed(retrospective=...)`, no receipt per row required); the mode is
  recorded and never upgraded.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import os
from pathlib import Path

from ...core.types import TF_MS
from ..predictive_experiment import evaluation_version, readonly
from ..slices import DISCOVERY_FRAC
from . import ClosureRefused, canonical, digest

MANIFEST_SCHEMA = 'closure-data-manifest.v2'
ENVELOPE_SCHEMA = 'closure-envelope.v2'
_LIVE_NAMES = frozenset(('candles.db', 'luffy.db', 'derivs.db'))
_LIVE_ROOTS = ()      # location rules come from config + inode identity, not one hardcoded mount
RANK = {s: i for i, s in enumerate(('UNEXPOSED', 'UNKNOWN', 'DEVELOPMENT_EXPOSED', 'PROTECTED_EXPOSED'))}


# ── policy ───────────────────────────────────────────────────────────────
def configured_policy(cfg: dict) -> dict:
    """The strictness currently configured (never a smaller number)."""
    from ...strategy import null_baseline
    from .. import evaluate, growth, referee, thresholds
    r = (cfg or {}).get('research') or {}
    return dict(
        klass='CONFIGURED', min_discovery_symbols=int(r.get('min_discovery_symbols', 16)),
        min_heldout_symbols=null_baseline.MIN_SYMBOLS,
        min_threshold_samples=int(r.get('min_threshold_samples', thresholds.MIN_SAMPLES)),
        min_finite_frac=float(r.get('min_finite_frac', thresholds.MIN_FINITE_FRAC)),
        draws=max(int(r.get('discovery_null_draws', 30)), null_baseline.MIN_DRAWS), seed=int(r.get('seed', 17)),
        min_symbol_trades=evaluate.MIN_SYMBOL_TRADES, min_referee_trades=referee.MIN_TRADES,
        fdr_target=float(r.get('fdr_target', 0.10)), lord_w0=float(r.get('lord_w0', 0.05)),
        referee_max_draws=int(r.get('referee_max_draws', 19999)), grow_max_p=float(r.get('grow_max_p', growth.GROW_MAX_P)),
        max_gap_bars=3, min_protected_bars=int(referee.ROTATION_FLOOR_BARS / (1 - DISCOVERY_FRAC)) + 1)


def numeric_id(policy: dict) -> str:
    """Identity of the NUMBERS only (floors, budget, draws), independent of the
    authority class: a TEST_ONLY simulation may run the exact configured numbers."""
    return digest({k: v for k, v in policy.items() if k != 'klass'})


def exact_numeric_policy(cfg: dict) -> dict:
    """The configured numbers in a TEST_ONLY authority context: needs no
    approved calibration, open gate or real forward seal, and grants nothing."""
    return dict(configured_policy(cfg), klass='TEST_ONLY')


def test_policy(**over) -> dict:
    """Explicitly weaker policy for synthetic fixtures. TEST_ONLY envelopes
    are refused by the Factory handoff; they exist to test the machinery."""
    base = dict(klass='TEST_ONLY', min_discovery_symbols=6, min_heldout_symbols=4, min_threshold_samples=2000,
                min_finite_frac=0.5, draws=30, seed=17, min_symbol_trades=8, min_referee_trades=60,
                fdr_target=0.5, lord_w0=0.4, referee_max_draws=19999, grow_max_p=0.25, max_gap_bars=3,
                min_protected_bars=1500)
    base.update(over)
    return base


def not_weaker(policy: dict, current: dict) -> list:
    """Reasons `policy` is weaker than `current` (empty = at least as strict)."""
    if policy.get('klass') != 'CONFIGURED':
        return ['policy_not_configured_class']
    out = []
    for k in ('min_discovery_symbols', 'min_heldout_symbols', 'min_threshold_samples', 'min_finite_frac', 'draws',
              'min_symbol_trades', 'min_referee_trades', 'min_protected_bars'):
        if policy.get(k, 0) < current[k]:
            out.append(f'{k}_weaker')
    for k in ('fdr_target', 'lord_w0', 'grow_max_p'):
        if policy.get(k, 9) > current[k]:
            out.append(f'{k}_weaker')
    return out


def evaluator_id() -> str:
    """Everything that can change evidence: the existing evaluation version
    plus the closure modules, the fill/provenance/compile layers."""
    here = Path(__file__).parent
    root = Path(__file__).parents[2]
    files = {f'closure.{p.stem}': hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(here.glob('*.py'))}
    # Fingerprint inputs only (file CONTENT is hashed; nothing is imported or called here).
    for rel in ('strategy/fill_timing.py', 'strategy/spec_evidence.py', 'strategy/compile.py', 'strategy/dsl.py',
                'strategy/geometries.py', 'data/market_provenance.py', 'data/feed.py', 'research/factory_transfer.py',
                'strategy/factory_handoff.py'):
        files[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    for folder in ('research/compose', 'world'):
        for p in sorted((root / folder).glob('*.py')):
            files[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    for rel in ('strategy/fitted.py', 'strategy/features.py', 'strategy/vector_backtest.py'):
        files[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    return digest(dict(base=evaluation_version(), extra=files))


# ── snapshot ─────────────────────────────────────────────────────────────
def _stat_id(p: Path) -> dict:
    st = p.stat()
    return dict(dev=st.st_dev, ino=st.st_ino, size=st.st_size, mtime_ns=st.st_mtime_ns, nlink=st.st_nlink)


def _configured_live_files():
    from ...core.config import ROOT, load_config
    out = [ROOT / 'data' / n for n in _LIVE_NAMES]
    try:
        paths = ((load_config().get('research') or {}).get('paths') or {})
        out += [Path(v) for v in paths.values() if isinstance(v, str)]
    except Exception:                                   # noqa: BLE001 - guard must not fail open on config errors
        pass
    return [p for p in out if p.exists()]


def refuse_live(path) -> Path:
    p = Path(path)
    if not p.exists() or p.is_symlink():
        raise ClosureRefused('snapshot_missing_or_symlink')
    p = p.resolve()
    if not p.is_file():
        raise ClosureRefused('snapshot_not_a_file')
    from ...core.config import ROOT
    roots = tuple(Path(r).resolve() for r in (ROOT / 'data', *_LIVE_ROOTS, *(f.parent for f in _configured_live_files())))
    if p.name in _LIVE_NAMES or any(root == p or root in p.parents for root in roots):
        raise ClosureRefused('live_database_refused', p.name)
    if any(os.path.samefile(p, live) for live in _configured_live_files()):
        raise ClosureRefused('live_database_refused', 'same inode as a configured database')
    if p.name not in _LIVE_NAMES and any((p.parent / n).exists() for n in ('luffy.db', 'candles.db')):
        raise ClosureRefused('live_data_directory_refused', str(p.parent.name))
    for suffix in ('-wal', '-shm', '-journal'):
        if Path(str(p) + suffix).exists():
            raise ClosureRefused('snapshot_not_quiescent', suffix)
    if p.stat().st_nlink != 1:
        raise ClosureRefused('snapshot_hardlinked')
    return p


def snapshot_identity(path) -> dict:
    p = refuse_live(path)
    # Publications are settled standalone files. A sidecar can change SQLite's
    # logical contents without changing the main-file digest.
    for suffix in ('-wal', '-journal'):
        if Path(str(p) + suffix).exists():
            raise ClosureRefused('snapshot_sidecar_present', suffix)
    before = _stat_id(p)
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    if _stat_id(p) != before:
        raise ClosureRefused('snapshot_changed_while_hashing')
    for suffix in ('-wal', '-journal'):
        if Path(str(p) + suffix).exists():
            raise ClosureRefused('snapshot_sidecar_present', suffix)
    return dict(before, sha256=h.hexdigest(), name=p.name)


def require_unchanged(path, ident: dict) -> None:
    if snapshot_identity(path) != ident:
        raise ClosureRefused('snapshot_changed_since_manifest')


# ── exposure ─────────────────────────────────────────────────────────────
def exposure_state(events, symbol, start_ms, end_ms, first_available_ms, *, allow_synthetic=False):
    """(state, proof). UNEXPOSED only with a covering SEAL whose clock is
    strictly before the window's first retained availability."""
    state, proof, covered = 'UNKNOWN', None, []
    for e in events:
        if e['symbol'] not in ('*', symbol) or e['end_ms'] < start_ms or e['start_ms'] > end_ms:
            continue
        if e['type'] == 'SEAL' and e['start_ms'] <= start_ms and e['end_ms'] >= end_ms:
            if e['kind'] == 'FORWARD_SEAL' and first_available_ms is not None and first_available_ms > e['at_ms']:
                covered.append('FORWARD_SEAL')
            elif e['kind'] == 'SYNTHETIC_DECLARED' and allow_synthetic:
                covered.append('SYNTHETIC_DECLARED')
    if covered:
        state, proof = 'UNEXPOSED', 'FORWARD_SEAL' if 'FORWARD_SEAL' in covered else 'SYNTHETIC_DECLARED'
    for e in events:
        if (e['type'] == 'MARK' and e['symbol'] in ('*', symbol) and e['end_ms'] >= start_ms
                and e['start_ms'] <= end_ms and RANK[e['state']] > RANK[state]):
            state, proof = e['state'], None
    return state, proof


# ── inventory / manifest ─────────────────────────────────────────────────
def _ms(ts):
    from ...data import market_provenance as mp
    return mp.ms(ts)


def _has(db, name):
    return bool(db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone())


def _feed(path, as_of_ms):
    from ...data.feed import DataFeed
    from ..universe import NoExchange
    return DataFeed(exchange=NoExchange(), db_path=str(path), clock_ms=lambda: int(as_of_ms), read_only=True)


def eligible_ts(path, tf, symbols, as_of_ms) -> dict:
    """{symbol: [event ms]} that the Feed's own replay eligibility accepts."""
    feed = _feed(path, as_of_ms)
    out = {}
    try:
        for sym in symbols:
            meta = feed.replay_metadata(sym, tf)
            out[sym] = [] if meta is None or not len(meta) else [int(x) for x in _ms(meta['ts'])]
    finally:
        if getattr(feed._local, 'conn', None) is not None:
            feed.db.close()
    return out


def inventory(path, tf, symbols, as_of_ms) -> dict:
    step = TF_MS[tf]
    elig = eligible_ts(path, tf, symbols, as_of_ms)
    out = {}
    with closing(readonly(path)) as db:
        for sym in symbols:
            key = f'candle:{sym}:{tf}'
            stored = [r[0] for r in db.execute('SELECT ts FROM candles WHERE symbol=? AND tf=? ORDER BY ts', (sym, tf))]
            ts = elig[sym]
            rev = _has(db, 'market_revisions')
            lag = [r[0] for r in db.execute('SELECT available_ms-event_ms-? FROM market_revisions WHERE series_key=? '
                   'AND available_ms IS NOT NULL ORDER BY 1', (step, key))] if rev else []
            obs = db.execute('SELECT MIN(observed_ms),MAX(observed_ms),MIN(available_ms) FROM market_revisions '
                             'WHERE series_key=?', (key,)).fetchone() if rev else (None, None, None)
            out[sym] = dict(
                stored_bars=len(stored), stored_first_ms=stored[0] if stored else None,
                stored_last_ms=stored[-1] if stored else None,
                stored_gap_count=sum(1 for a, b in zip(stored, stored[1:]) if b - a != step),
                eligible_bars=len(ts), eligible_first_ms=ts[0] if ts else None,
                eligible_last_ms=ts[-1] if ts else None,
                gap_count=sum(1 for a, b in zip(ts, ts[1:]) if b - a != step),
                availability_lag_ms=dict(min=lag[0], median=lag[len(lag) // 2], max=lag[-1]) if lag else None,
                observed_range_ms=[obs[0], obs[1]], first_available_ms=obs[2],
                eligible_sha256=hashlib.sha256(canonical(ts).encode()).hexdigest())
    return out


def window_first_available(path, tf, sym, start_ms, end_ms):
    with closing(readonly(path)) as db:
        if not _has(db, 'market_revisions'):
            return None
        return db.execute('SELECT MIN(available_ms) FROM market_revisions WHERE series_key=? AND event_ms>=? '
                          'AND event_ms<=?', (f'candle:{sym}:{tf}', start_ms, end_ms)).fetchone()[0]


def build_manifest(snapshot_path, tf, symbols, exposure_events=(), *, as_of_ms=None) -> dict:
    path = refuse_live(snapshot_path)
    if tf not in TF_MS:
        raise ClosureRefused('unsupported_timeframe')
    ident = snapshot_identity(path)
    with closing(readonly(path)) as db:
        if not _has(db, 'candles'):
            raise ClosureRefused('snapshot_schema')
        last = db.execute('SELECT MAX(ts) FROM candles WHERE tf=?', (tf,)).fetchone()[0]
    if last is None:
        raise ClosureRefused('snapshot_empty')
    as_of = int(as_of_ms if as_of_ms is not None else last + TF_MS[tf])
    body = dict(schema=MANIFEST_SCHEMA, tf=tf, as_of_ms=as_of, snapshot=ident,
                symbols=inventory(path, tf, sorted(set(symbols)), as_of),
                exposure_events=sorted(exposure_events, key=canonical), authority='DESCRIPTIVE_ONLY')
    require_unchanged(path, ident)
    return dict(body, manifest_id=digest(body))


def verify_manifest(m):
    if m.get('schema') != MANIFEST_SCHEMA or m.get('manifest_id') != digest(
            {k: v for k, v in m.items() if k != 'manifest_id'}):
        raise ClosureRefused('manifest_integrity')
    return m


def _coverage(inv, start_ms, end_ms, step, max_gap, min_bars, mode):
    """Reasons a symbol's retained bars do not cover [start,end]."""
    p = 'eligible' if mode == 'REPLAY' else 'stored'
    lo, hi = inv[f'{p}_first_ms'], inv[f'{p}_last_ms']
    if lo is None or lo > start_ms + max_gap * step or hi < end_ms - max_gap * step:
        return 'coverage_short'
    if inv[f'{p}_bars'] < min_bars:
        return 'too_few_bars'
    if inv['gap_count' if mode == 'REPLAY' else 'stored_gap_count'] > max_gap:
        return 'gaps'
    return None


RESEARCH_COST_KEYS = ('taker_fee_pct', 'slippage_atr_frac', 'funding_rate_8h')


def research_risk(risk) -> dict:
    """The one frozen research risk/cost block, normalised BEFORE hashing so development,
    qualification and the handoff comparison see identical explicit values.

    real_funding defaults to False (the closure contract: flat funding_rate_8h unless signed
    funding is bound). max_open_trades keeps an explicit value; else the configured live
    breadth limit max_open_positions (same meaning, read only); else 8. Cost fields are
    kept only when present. Null/invalid values are a typed refusal, never silently defaulted."""
    risk = risk or {}

    def num(k, default=None):
        v = risk.get(k, default)
        if v is None and k in risk:
            raise ClosureRefused('risk_value_invalid', f'{k} is null')
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float('inf'), float('-inf')):
            raise ClosureRefused('risk_value_invalid', f'{k}={v!r}')
        return v

    out = {}
    for k in RESEARCH_COST_KEYS:
        v = num(k)
        if v is not None:
            if v < 0:
                raise ClosureRefused('risk_value_invalid', f'{k}={v!r}')
            out[k] = v
    rf = risk.get('real_funding', False)
    if not isinstance(rf, bool):
        raise ClosureRefused('risk_value_invalid', f'real_funding={rf!r}')
    out['real_funding'] = rf
    pct = num('risk_per_trade_pct', 0.5)
    if not 0 < pct <= 100:
        raise ClosureRefused('risk_value_invalid', f'risk_per_trade_pct={pct!r}')
    out['risk_per_trade_pct'] = pct
    key = 'max_open_trades' if risk.get('max_open_trades') is not None or 'max_open_trades' in risk else 'max_open_positions'
    mo = num(key, 8)
    if isinstance(mo, float) and mo != int(mo) or mo < 1:
        raise ClosureRefused('risk_value_invalid', f'{key}={mo!r}')
    out['max_open_trades'] = int(mo)
    return out


def make_envelope(manifest, snapshot_path, *, policy, discovery_symbols, heldout_symbols, fit_start_ms,
                  dev_end_ms, embargo_bars, protected_start_ms, protected_end_ms, risk,
                  development_mode='REPLAY') -> dict:
    """Freeze a study. Statuses are COMPUTED: development ELIGIBLE needs the
    policy's discovery floor; protected ELIGIBLE additionally needs REPLAY
    provenance over the whole window, a proven UNEXPOSED state, the held-out
    floor and the referee's bar floors."""
    verify_manifest(manifest)
    path = refuse_live(snapshot_path)
    require_unchanged(path, manifest['snapshot'])
    if development_mode not in ('REPLAY', 'RETROSPECTIVE') or policy.get('klass') not in ('CONFIGURED', 'TEST_ONLY'):
        raise ClosureRefused('mode_or_policy_invalid')
    tf, step = manifest['tf'], TF_MS[manifest['tf']]
    disc, held = sorted(set(discovery_symbols)), sorted(set(heldout_symbols))
    if not disc or set(disc) & set(held):
        raise ClosureRefused('discovery_heldout_overlap_or_empty')
    if (fit_start_ms >= dev_end_ms or protected_start_ms < dev_end_ms + embargo_bars * step
            or protected_end_ms <= protected_start_ms or embargo_bars < 0):
        raise ClosureRefused('window_order_or_embargo')
    inv = manifest['symbols']
    gap = policy['max_gap_bars']
    dev_reasons = [f'{s}:{r}' for s in disc if (r := _coverage(inv[s], fit_start_ms, dev_end_ms - step, step, gap, 1,
                   development_mode) if s in inv else 'not_in_manifest')]
    if len(disc) < policy['min_discovery_symbols']:
        dev_reasons.append(f'discovery_symbols_{len(disc)}_below_{policy["min_discovery_symbols"]}')
    covered = [s for s in disc if not any(r.startswith(s + ':') for r in dev_reasons)]
    if len(covered) < policy['min_discovery_symbols']:
        dev_reasons.append(f'covered_discovery_symbols_{len(covered)}_below_{policy["min_discovery_symbols"]}')
    prot_reasons = []
    if len(held) < policy['min_heldout_symbols']:
        prot_reasons.append('heldout_symbols_below_floor')
    if len(disc) < policy['min_discovery_symbols']:
        prot_reasons.append('discovery_symbols_below_floor')
    want = (protected_end_ms - protected_start_ms) // step + 1
    if want < policy['min_protected_bars']:
        prot_reasons.append('protected_window_below_referee_bar_floor')
    states, proofs, firsts = {}, {}, {}
    for s in disc + held:
        r = _coverage(inv[s], protected_start_ms, protected_end_ms, step, gap, policy['min_protected_bars'],
                      'REPLAY') if s in inv else 'not_in_manifest'
        if r:
            prot_reasons.append(f'{s}:{r}')
        if s in inv and inv[s]['eligible_bars'] < inv[s]['stored_bars']:
            prot_reasons.append(f'{s}:unqualified_provenance')
        firsts[s] = window_first_available(path, tf, s, protected_start_ms, protected_end_ms)
        states[s], proofs[s] = exposure_state(manifest['exposure_events'], s, protected_start_ms, protected_end_ms,
                                              firsts[s], allow_synthetic=policy['klass'] == 'TEST_ONLY')
        if states[s] != 'UNEXPOSED':
            prot_reasons.append(f'{s}:exposure_{states[s].lower()}')
    cut = protected_start_ms + int((protected_end_ms - protected_start_ms) * DISCOVERY_FRAC)
    cut -= cut % step
    split = dict(schema='historical-protected-split.v1', start_ms=protected_start_ms, cut_ms=cut,
                 end_ms=protected_end_ms, discovery_symbols=disc, heldout_symbols=held)
    # exact eligible counts (metadata only) feed the testability projection
    elig = eligible_ts(path, tf, disc + held, manifest['as_of_ms'])
    counts = dict(a={s: sum(1 for t in elig[s] if protected_start_ms <= t < cut) for s in held},
                  b={s: sum(1 for t in elig[s] if cut <= t <= protected_end_ms) for s in disc + held})
    # Development's firing-rate projection is a metadata inventory, not a
    # qualification look. Backfilled candles can supply that inventory even
    # when their original-time receipts are absent. Keep the replay counts
    # above and every protected-evidence requirement unchanged.
    development_counts = counts
    projection_basis = 'retained_point_in_time'
    if development_mode == 'RETROSPECTIVE':
        from ...data.feed import DataFeed
        from ..universe import NoExchange
        feed = DataFeed(exchange=NoExchange(), db_path=str(path), retrospective=dict(
            dataset_id=manifest['manifest_id'], cut_ms=dev_end_ms))
        try:
            def count(sym, lo, hi):
                return feed.retrospective_coverage(sym, tf, start_ms=lo, end_ms=hi,
                    closed_before_ms=hi, metadata_only=True)['bars']
            development_counts = dict(a={s: count(s, protected_start_ms, cut) for s in held},
                                      b={s: count(s, cut, protected_end_ms + step) for s in disc + held})
        finally:
            if getattr(feed._local, 'conn', None) is not None:
                feed.db.close()
        projection_basis = 'retrospective_coverage_metadata'
    dev_ids = sorted((s, inv[s]['eligible_sha256'] if development_mode == 'REPLAY' else inv[s]['stored_gap_count'])
                     for s in disc)
    risk_id = research_risk(risk)
    body = dict(schema=ENVELOPE_SCHEMA, manifest_id=manifest['manifest_id'], tf=tf, development_mode=development_mode,
                fit_window=[fit_start_ms, dev_end_ms], dev_end_ms=dev_end_ms, embargo_bars=embargo_bars,
                protected_window=[protected_start_ms, protected_end_ms], discovery_symbols=disc, heldout_symbols=held,
                policy=policy, policy_id=digest(policy), numeric_id=numeric_id(policy), policy_class=policy['klass'],
                development_status='BLOCKED' if dev_reasons else 'ELIGIBLE', development_reasons=sorted(set(dev_reasons)),
                protected_status='UNAVAILABLE' if prot_reasons else 'ELIGIBLE',
                protected_reasons=sorted(set(prot_reasons)), exposure_proofs=proofs, protected_first_available=firsts,
                projection_counts=counts, development_projection_counts=development_counts,
                development_projection_basis=projection_basis,
                development_inventory_id=digest(dev_ids), split=split, split_id=digest(split),
                evaluator_id=evaluator_id(), cost_id=digest(risk_id), risk=risk_id, protected_fingerprint=None,
                development_data_id=None, snapshot=manifest['snapshot'], exposure_events=manifest['exposure_events'],
                authority='RESEARCH_ONLY')
    if not dev_reasons:
        body['development_data_id'] = development_data_id(path, body)
    if not prot_reasons:
        body['protected_fingerprint'] = protected_fingerprint(path, body)
    return dict(body, envelope_id=digest(body))


def rederive_proofs(env) -> dict:
    """Exposure proofs recomputed from the envelope's own recorded events; a
    packet whose stated proofs differ (or whose class was rehashed) is refused."""
    from . import exposure
    exposure.verify_chain(env['exposure_events'])
    s = env['split']
    out = {}
    for sym in env['discovery_symbols'] + env['heldout_symbols']:
        out[sym] = exposure_state(env['exposure_events'], sym, s['start_ms'], s['end_ms'],
                                  env['protected_first_available'].get(sym),
                                  allow_synthetic=env['policy']['klass'] == 'TEST_ONLY')[1]
    return out


def verify_envelope(env, manifest=None):
    if env.get('schema') != ENVELOPE_SCHEMA or env.get('envelope_id') != digest(
            {k: v for k, v in env.items() if k != 'envelope_id'}):
        raise ClosureRefused('envelope_integrity')
    if manifest is not None and env['manifest_id'] != verify_manifest(manifest)['manifest_id']:
        raise ClosureRefused('envelope_manifest_mismatch')
    return env


# ── data views ───────────────────────────────────────────────────────────
def _dev_frames(snapshot_path, env):
    import pandas as pd
    path = refuse_live(snapshot_path)
    step = TF_MS[env['tf']]
    lo, hi = env['fit_window'][0], env['dev_end_ms']
    frames, ids = {}, {}
    if env['development_mode'] == 'RETROSPECTIVE':
        from ...data.feed import DataFeed
        from ..universe import NoExchange
        contract = dict(dataset_id='closure:' + env['manifest_id'][:16], cut_ms=hi, start_ms=lo)
        feed = DataFeed(exchange=NoExchange(), db_path=str(path), retrospective=contract)
        read = lambda s: feed.cached_ohlcv(s, env['tf'], limit=200000)         # noqa: E731
    else:
        feed = _feed(path, hi)
        read = lambda s: feed.replay_ohlcv(s, env['tf'], 200000)               # noqa: E731
    try:
        for sym in env['discovery_symbols']:
            df = read(sym)
            if df is None or not len(df):
                continue
            ms = _ms(df['ts'])
            df = df[(ms >= lo) & (ms + step <= hi)].reset_index(drop=True)
            if len(df):
                frames[sym] = df
                ids[sym] = hashlib.sha256(canonical(
                    [df['ts'].astype('int64').tolist(), df['close'].round(10).tolist(), df['volume'].round(10).tolist(),
                     df['open'].round(10).tolist(), df['high'].round(10).tolist(), df['low'].round(10).tolist()]
                ).encode()).hexdigest()
    finally:
        if getattr(feed._local, 'conn', None) is not None:
            feed.db.close()
    return frames, digest(sorted(ids.items()))


def development_data_id(snapshot_path, env_body) -> str:
    return _dev_frames(snapshot_path, env_body)[1]


def development_frames(snapshot_path, env) -> tuple:
    """Frames as known at `dev_end_ms`; later rows are not selectable. The
    identity of the consumed rows must equal the envelope's, so changed
    development data can neither be developed on nor hit a stale cache."""
    verify_envelope(env)
    if env['development_status'] != 'ELIGIBLE':
        raise ClosureRefused('development_blocked', ';'.join(env['development_reasons'])[:200])
    try:
        frames, data_id = _dev_frames(snapshot_path, env)
    except ValueError as exc:            # the provenance layer's own content check fired
        raise ClosureRefused('development_data_changed', str(exc)[:120])
    if data_id != env['development_data_id']:
        raise ClosureRefused('development_data_changed')
    return frames, data_id


def freeze_window(snapshot_path, dest, env) -> str:
    """Seal ONLY the protected window (rows + retained receipts); returns an
    opaque fingerprint. The source identity is re-verified first."""
    from ...data import market_provenance as mp
    from ...data.feed import DataFeed
    import sqlite3
    src_path = refuse_live(snapshot_path)
    require_unchanged(src_path, env['snapshot'])
    s, step = env['split'], TF_MS[env['tf']]
    dest = Path(dest)
    if dest.exists():
        dest.unlink()
    fingerprint = hashlib.sha256()
    with closing(readonly(src_path)) as src, closing(sqlite3.connect(dest)) as dst:
        src.execute('BEGIN')
        dst.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,'
                    'volume REAL,taker_buy REAL,PRIMARY KEY(symbol,tf,ts))')
        dst.execute('CREATE TABLE candle_floor(symbol TEXT,tf TEXT,first_ts INTEGER,window_version TEXT,'
                    'PRIMARY KEY(symbol,tf))')
        mp.init(dst)
        for sym in env['discovery_symbols'] + env['heldout_symbols']:
            rows = src.execute('SELECT * FROM candles WHERE symbol=? AND tf=? AND ts>=? AND ts<=? ORDER BY ts',
                               (sym, env['tf'], s['start_ms'], s['end_ms'])).fetchall()
            dst.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)', rows)
            revs = src.execute('SELECT * FROM market_revisions WHERE series_key=? AND event_ms>=? AND event_ms<=? '
                               'AND available_ms<=? AND observed_ms<=? ORDER BY event_ms,revision_id',
                               (DataFeed._series_key(sym, env['tf']), s['start_ms'], s['end_ms'],
                                s['end_ms'] + step, s['end_ms'] + step)).fetchall()
            logical = [(*r[:-1], mp.logical_record_json(src, r[-1])) for r in revs]
            dst.executemany('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)', logical)
            for r in revs:
                raw = src.execute('SELECT * FROM market_raw_sources WHERE content_hash=?',
                                  (mp.read_record(src, r[-1])['content_hash'],)).fetchone()
                if raw:
                    dst.execute('INSERT OR IGNORE INTO market_raw_sources VALUES (?,?,?)', raw)
            fingerprint.update(canonical([sym, rows, logical]).encode())
        dst.commit()
    require_unchanged(src_path, env['snapshot'])
    return fingerprint.hexdigest()


def protected_fingerprint(snapshot_path, env) -> str:
    import tempfile
    with tempfile.TemporaryDirectory(prefix='closure-fp-') as tmp:
        return freeze_window(snapshot_path, Path(tmp) / 'p.db', env)


_DERIV_SERIES = {'funding': 'funding', 'open_interest': 'oi', 'taker_ratio': 'taker_ratio', 'ls_ratio': 'ls_ratio',
                 'ls_account_ratio': 'ls_account_ratio', 'basis': 'basis'}


def freeze_context(snapshot_path, derivs_path, dest_candles, dest_derivs, env, requires) -> dict:
    """Seal the EXTERNAL series a candidate reads (references into the sealed
    candle file's `refs`/`market_revisions`, derivatives into their own sealed
    file), limited to the protected window and to observations that were
    available by its end. Returns {fingerprint, counts, gaps}: a requirement with
    no sealed rows is a gap the caller must treat as BLOCKED before any look."""
    from ...data import market_provenance as mp
    import sqlite3
    src_path = refuse_live(snapshot_path)
    s, step = env['split'], TF_MS[env['tf']]
    fp, counts, gaps = hashlib.sha256(), {}, []
    ref_keys = [r.split(':', 1)[1] for r in requires if isinstance(r, str) and r.startswith('ref:')]
    series = [(r, _DERIV_SERIES[r]) for r in requires if r in _DERIV_SERIES]
    end = s['end_ms'] + step
    with closing(readonly(src_path)) as src, closing(sqlite3.connect(dest_candles)) as dst:
        src.execute('BEGIN')
        if ref_keys:
            dst.execute('CREATE TABLE IF NOT EXISTS refs(key TEXT NOT NULL,ts INTEGER NOT NULL,open REAL,high REAL,low REAL,'
                        'close REAL,volume REAL,PRIMARY KEY(key,ts))')
            mp.init(dst)
        for k in ref_keys:
            rows = src.execute('SELECT * FROM refs WHERE key=? AND ts<=? ORDER BY ts', (k, end)).fetchall()
            revs = src.execute('SELECT * FROM market_revisions WHERE series_key=? AND event_ms<=? AND available_ms<=? '
                               'AND observed_ms<=? ORDER BY event_ms,revision_id', ('reference:' + k, end, end, end)).fetchall()
            logical = [(*r[:-1], mp.logical_record_json(src, r[-1])) for r in revs]
            dst.executemany('INSERT OR IGNORE INTO refs VALUES (?,?,?,?,?,?,?)', rows)
            dst.executemany('INSERT OR IGNORE INTO market_revisions VALUES (?,?,?,?,?,?)', logical)
            for r in revs:
                raw = src.execute('SELECT * FROM market_raw_sources WHERE content_hash=?',
                                  (mp.read_record(src, r[-1])['content_hash'],)).fetchone()
                if raw:
                    dst.execute('INSERT OR IGNORE INTO market_raw_sources VALUES (?,?,?)', raw)
            in_win = sum(1 for r in logical if s['start_ms'] <= r[2] <= s['end_ms'])
            counts['ref:' + k] = dict(rows=len(rows), receipts=len(logical), in_window=in_win)
            if not len(logical) or not in_win:
                gaps.append(dict(requirement='ref:' + k, reason='no receipted observations in the protected window'))
            fp.update(canonical(['ref', k, rows, logical]).encode())
        dst.commit()
    if series:
        if derivs_path is None:
            gaps += [dict(requirement=r, reason='no derivatives snapshot supplied') for r, _ in series]
        else:
            with closing(readonly(refuse_live(derivs_path))) as src, closing(sqlite3.connect(dest_derivs)) as dst:
                src.execute('BEGIN')
                dst.execute('CREATE TABLE IF NOT EXISTS derivs(symbol TEXT NOT NULL,series TEXT NOT NULL,ts INTEGER NOT NULL,'
                            'value REAL,PRIMARY KEY(symbol,series,ts))')
                mp.init(dst)
                for r, name in series:
                    have = 0
                    for sym in env['discovery_symbols'] + env['heldout_symbols']:
                        # one prior settlement interval (24h) of history BEFORE the window: a protected bar needs the funding rate in
                        # force at its open, which was set before the window began. Development-era data, not protected values.
                        lo = s['start_ms'] - 24 * 3_600_000
                        rows = src.execute('SELECT * FROM derivs WHERE symbol=? AND series=? AND ts>=? AND ts<=? ORDER BY ts',
                                           (sym, name, lo, end)).fetchall()
                        revs = src.execute('SELECT * FROM market_revisions WHERE series_key=? AND event_ms>=? AND event_ms<=? '
                                           'AND available_ms<=? AND observed_ms<=? ORDER BY event_ms,revision_id',
                                           (f'derivative:{sym}:{name}', lo, end, end, end)).fetchall()
                        logical = [(*x[:-1], mp.logical_record_json(src, x[-1])) for x in revs]
                        dst.executemany('INSERT OR IGNORE INTO derivs VALUES (?,?,?,?)', rows)
                        dst.executemany('INSERT OR IGNORE INTO market_revisions VALUES (?,?,?,?,?,?)', logical)
                        for x in revs:
                            raw = src.execute('SELECT * FROM market_raw_sources WHERE content_hash=?',
                                              (mp.read_record(src, x[-1])['content_hash'],)).fetchone()
                            if raw:
                                dst.execute('INSERT OR IGNORE INTO market_raw_sources VALUES (?,?,?)', raw)
                        have += 1 if logical else 0
                        fp.update(canonical([r, sym, rows, logical]).encode())
                    counts[r] = dict(symbols_with_receipts=have)
                    if have < len(env['discovery_symbols'] + env['heldout_symbols']):
                        gaps.append(dict(requirement=r, reason=f'{have} symbols have receipted rows in the protected window'))
                dst.commit()
    return dict(fingerprint=fp.hexdigest(), counts=counts, gaps=gaps)
