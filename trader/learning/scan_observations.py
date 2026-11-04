"""Non-trade directional scan observations with counterfactual price-path outcomes.

Storage isolation: observations live in their OWN tables (`scan_observations`,
`scan_observation_outcomes`). Nothing here writes `decisions`, `votes`, `cycles`,
`trades` or `outcomes`, so the 45-minute same-direction cooldown
(`orchestrator.decide`: SELECT ... FROM decisions ...), every executable-decision
reader and every trade-outcome consumer are structurally unable to see a skipped
observation. Capture is observational: it never mutates the snapshot/decision and
every failure is swallowed (logged) so it cannot change a trading decision.

Identity
  occurrence  = strategy-signal-occurrence.v1 key (spec_id, spec_fingerprint, symbol,
                action, signal_timeframe, signal_bar_close_ms), i.e. the
                (symbol, strategy_id, signal_bar_close_ms) statistical sample.
  revision    = the same occurrence re-evaluated with different exact inputs
                (market-provenance revision ids / content hashes, or a different
                non-trade reason). Recorded with revision_seq and
                supersedes_observation_id, never as another sample. Identical inputs
                on the same bar close create no row.
Outcomes are `outcome_kind='COUNTERFACTUAL_PRICE_PATH'`: forward price return and
MFE/MAE in R from a hypothetical entry at the observation price. They are not fills,
PnL, or portfolio capacity.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time

import pandas as pd

from . import foundation as L

log = logging.getLogger(__name__)

SCHEMA = 'scan-observation.v1'
OUTCOME_KIND = 'COUNTERFACTUAL_PRICE_PATH'
SOURCE_KIND = 'SCAN_OBSERVATION'

DDL = (
    """CREATE TABLE IF NOT EXISTS scan_observations (
        observation_id TEXT PRIMARY KEY,
        occurrence_id TEXT NOT NULL,
        revision_seq INTEGER NOT NULL,
        supersedes_observation_id TEXT,
        source_kind TEXT NOT NULL DEFAULT 'SCAN_OBSERVATION',
        strategy_id TEXT NOT NULL,
        spec_sha256 TEXT,
        spec_fingerprint TEXT NOT NULL,
        compile_identity TEXT,
        version_id TEXT,
        symbol TEXT NOT NULL,
        action TEXT NOT NULL,
        signal_timeframe TEXT NOT NULL,
        signal_bar_close_ms INTEGER NOT NULL,
        skip_reason TEXT NOT NULL,
        control_state TEXT,
        observed_ms INTEGER NOT NULL,
        entry_price REAL,
        initial_risk REAL,
        risk_basis TEXT,
        horizon_bars INTEGER,
        horizon_ms INTEGER,
        input_digest TEXT NOT NULL,
        market_provenance_digest TEXT,
        source_manifest_id TEXT,
        registration_event_key TEXT,
        provenance_manifest_json TEXT,
        UNIQUE(occurrence_id, revision_seq))""",
    "CREATE INDEX IF NOT EXISTS idx_scan_obs_occ ON scan_observations(occurrence_id, revision_seq)",
    "CREATE INDEX IF NOT EXISTS idx_scan_obs_symbol ON scan_observations(symbol, observed_ms)",
    """CREATE TABLE IF NOT EXISTS scan_observation_outcomes (
        observation_id TEXT PRIMARY KEY REFERENCES scan_observations(observation_id),
        outcome_kind TEXT NOT NULL DEFAULT 'COUNTERFACTUAL_PRICE_PATH',
        horizon_ms INTEGER,
        entry_price REAL,
        exit_price REAL,
        fwd_return REAL,
        mfe_r REAL,
        mae_r REAL,
        excursion_status TEXT,
        status TEXT NOT NULL,
        graded_ms INTEGER NOT NULL)""",
    # one row per statistical sample: the latest revision of each occurrence
    """CREATE VIEW IF NOT EXISTS scan_observation_samples AS
        SELECT o.* FROM scan_observations o
        WHERE o.revision_seq=(SELECT MAX(revision_seq) FROM scan_observations x
                              WHERE x.occurrence_id=o.occurrence_id)""",
)

_ensured: set = set()


def ensure(db):
    for stmt in DDL:
        db.execute(stmt)


def _ensure_once(journal):
    key = id(journal)
    if key in _ensured:
        return
    with journal._tx() as c:
        ensure(c)
    _ensured.add(key)


_NUM = re.compile(r'[-+]?\d+(?:\.\d+)?')


def reason_class(decision, control_reason: str) -> str:
    """Stable non-trade reason: numbers in diagnostics (e.g. 's=+0.82') are masked so a
    drifting score is not mistaken for a changed input."""
    raw = (getattr(decision, 'skip_reason', '') or control_reason or '').strip()
    if not raw:
        raw = 'not_admitted:' + getattr(getattr(decision, 'action', None), 'value', 'HOLD').lower()
    return _NUM.sub('#', raw)[:240]


def _provenance_digest(params):
    prov = (params or {}).get('market_provenance') or {}
    sources = [dict(kind=s.get('kind'), key=s.get('key'),
                    revision_ids=s.get('revision_ids'), content_hashes=s.get('content_hashes'))
               for s in prov.get('sources', ())]
    return L.digest(sorted(sources, key=lambda s: (str(s['kind']), str(s['key'])))), sources


def _initial_risk(snap, tf, exit_cfg):
    """R unit from the spec's own declared stop; None (never invented) when unknowable."""
    price = getattr(snap, 'price', None)
    if exit_cfg is None or not price or not math.isfinite(price):
        return None, 'unavailable:no_declared_exit'
    try:
        if getattr(exit_cfg, 'stop_pct', 0):
            return float(price) * float(exit_cfg.stop_pct), 'spec_stop_pct'
        mult = float(getattr(exit_cfg, 'stop_atr_mult', 0) or 0)
        if mult > 0:
            from ..agents.indicators import atr
            a = atr(snap.df(tf))
            if math.isfinite(a) and a > 0:
                return a * mult, f'spec_stop_atr_x{mult}:{tf}'
    except Exception as exc:                                  # noqa: BLE001
        return None, 'unavailable:' + type(exc).__name__
    return None, 'unavailable:no_stop_geometry'


def record(journal, snap, decision, *, cfg=None, control_state=None, control_reason='',
           spec_exits=None, now_ms=None) -> list:
    """Record each directional strategy signal of an un-admitted decision.

    Returns the observation ids written (revisions included). Never raises."""
    written = []
    try:
        if (cfg or {}).get('scan_observations', {}).get('enabled', True) is False:
            return written
        from ..strategy import signal_occurrence as so
        from ..engine.protective import venue_key
        now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        reason = reason_class(decision, control_reason)
        for sig in decision.strategy_signals or ():
            key, unavailable = so.signal_occurrence(sig)
            if key is None:
                continue                      # no exact bar identity: never fabricated
            _ensure_once(journal)
            written += _record_one(journal, snap, decision, sig, key, reason, cfg,
                                   control_state, spec_exits or {}, now_ms, venue_key)
    except Exception as exc:                                  # noqa: BLE001
        log.warning('scan observation capture failed: %s', type(exc).__name__)
    return written


def _record_one(journal, snap, decision, sig, key, reason, cfg, control_state, spec_exits, now_ms, venue_key):
    params = sig.get('params') or {}
    spec_id, fingerprint, symbol, action, tf, close_ms = key
    prov_digest, _sources = _provenance_digest(params)
    input_digest = L.digest(dict(action=action, reason=reason, provenance=prov_digest))
    occurrence_id = L.digest(list(key))
    prior = journal.query(
        "SELECT observation_id, revision_seq, input_digest FROM scan_observations "
        "WHERE occurrence_id=? ORDER BY revision_seq DESC LIMIT 1", (occurrence_id,))
    if prior and prior[0]['input_digest'] == input_digest:
        return []                                             # same bar, same inputs: no new row
    seq = (prior[0]['revision_seq'] + 1) if prior else 0
    supersedes = prior[0]['observation_id'] if prior else None
    observation_id = 'obs_' + L.digest([occurrence_id, seq])[:24]
    exit_cfg = spec_exits.get(spec_id)
    risk, risk_basis = _initial_risk(snap, tf, exit_cfg)
    from ..core.types import TF_MS
    bars = int(getattr(exit_cfg, 'max_bars', 0) or 0) if exit_cfg is not None else 0
    horizon_ms = bars * TF_MS.get(getattr(exit_cfg, 'timeframe', None) or tf, 0) or None
    entry = float(snap.price) if getattr(snap, 'price', None) and math.isfinite(snap.price) else None
    event_key = 'decision:' + observation_id
    manifest_id = None
    # Default: a tiny provenance manifest (immutable revision ids / content hashes that
    # already identify the bars in candles.db / derivs.db) on the row itself. The heavy
    # DataFrame-blob manifest is opt-in via scan_observations.store_frame_blobs.
    full = bool((cfg or {}).get('scan_observations', {}).get('store_frame_blobs', False))
    prov_json = json.dumps(dict(schema='scan-observation-provenance.v1', spec_id=spec_id,
                                spec_sha256=params.get('spec_sha256'),
                                compile_identity=params.get('compile_identity'),
                                sources=_sources), sort_keys=True, default=str)
    with journal._tx() as c:
        if full:
            from . import capture as lc, capture_runtime as lr
            from ..cognition.outcomes import timestamp
            # the observation is made at now_ms; its source cut must not follow its own timestamp
            ts_iso = pd.Timestamp(now_ms, unit='ms', tz='UTC').isoformat()
            row = dict(id=observation_id, cycle_id=decision.cycle_id, ts=ts_iso, symbol=snap.symbol,
                       action=action, executed=0, skip_reason=reason,
                       signals_json=json.dumps([sig], default=str), strategy_ids=spec_id)
            inputs = lr.runtime_inputs(journal, snap, cfg or {}, cut_ms=now_ms, control_state=control_state)
            lc.safely(c, event_key, lr.decision, row, None, inputs)
            lc.safely(c, event_key, lc.record_action, event_key,
                      dict(prediction=dict(decision_id=observation_id, cycle_id=decision.cycle_id,
                                           symbol=snap.symbol, ts=ts_iso, action=action, entry_price=entry,
                                           source_kind=SOURCE_KIND)), timestamp(ts_iso))
            try:
                manifest_id = lc.registration(c, event_key)[1].get('decision_source_manifest_id')
            except Exception as exc:                          # noqa: BLE001
                log.warning('scan observation manifest lookup failed: %s', type(exc).__name__)
        c.execute(
            "INSERT INTO scan_observations(observation_id,occurrence_id,revision_seq,"
            "supersedes_observation_id,strategy_id,spec_sha256,spec_fingerprint,compile_identity,"
            "version_id,symbol,action,signal_timeframe,signal_bar_close_ms,skip_reason,control_state,"
            "observed_ms,entry_price,initial_risk,risk_basis,horizon_bars,horizon_ms,input_digest,"
            "market_provenance_digest,source_manifest_id,registration_event_key,provenance_manifest_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (observation_id, occurrence_id, seq, supersedes, spec_id, params.get('spec_sha256'),
             fingerprint, params.get('compile_identity'), params.get('version_id'), symbol, action, tf,
             close_ms, reason, getattr(control_state, 'value', control_state), now_ms, entry, risk,
             risk_basis, bars or None, horizon_ms, input_digest, prov_digest, manifest_id,
             event_key if manifest_id else None, prov_json))
    return [observation_id]


def resolve_pending(journal, feed, now_ms=None, limit=200) -> int:
    """Grade observations whose declared horizon has elapsed. Never raises."""
    done = 0
    try:
        _ensure_once(journal)
        now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        rows = journal.query(
            "SELECT o.* FROM scan_observations o LEFT JOIN scan_observation_outcomes r "
            "ON r.observation_id=o.observation_id WHERE r.observation_id IS NULL "
            "AND o.horizon_ms IS NOT NULL AND o.observed_ms+o.horizon_ms<=? "
            "ORDER BY o.observed_ms LIMIT ?", (now_ms, limit))
        for o in rows:
            outcome = _grade(o, feed, now_ms)
            if outcome is None:
                continue
            with journal._tx() as c:
                c.execute("INSERT OR IGNORE INTO scan_observation_outcomes(observation_id,outcome_kind,"
                          "horizon_ms,entry_price,exit_price,fwd_return,mfe_r,mae_r,excursion_status,"
                          "status,graded_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (o['observation_id'], OUTCOME_KIND, o['horizon_ms'], o['entry_price'],
                           outcome['exit_price'], outcome['fwd_return'], outcome['mfe_r'],
                           outcome['mae_r'], outcome['excursion_status'], outcome['status'], now_ms))
            done += 1
    except Exception as exc:                                  # noqa: BLE001
        log.warning('scan observation grading failed: %s', type(exc).__name__)
    return done


def _grade(o, feed, now_ms):
    from ..engine import excursion
    entry, start, end = o['entry_price'], o['observed_ms'], o['observed_ms'] + o['horizon_ms']
    empty = dict(exit_price=None, fwd_return=None, mfe_r=None, mae_r=None, excursion_status=None)
    if not entry or entry <= 0:
        return dict(empty, status='UNMEASURABLE:no_entry_price')
    need = int(o['horizon_ms'] / 300_000) + 300
    if need > 5000:
        return dict(empty, status='UNMEASURABLE:horizon_beyond_path_window')
    df = feed.fetch_ohlcv(o['symbol'], '5m', limit=need, as_of_ms=now_ms)
    if df is None or df.empty:
        return None                                           # retry later
    side = 'long' if o['action'] == 'BUY' else 'short'
    sign = 1 if side == 'long' else -1
    ts_ms = [int(t.value // 1_000_000) if hasattr(t, 'value') else int(t) for t in df['ts']]
    target = [(t, c) for t, c in zip(ts_ms, df['close']) if t >= end]
    if not target:
        return None
    exit_px = float(target[0][1])
    ret = round((exit_px - entry) / entry * sign, 6)
    exc = excursion.compute(side, entry, o['initial_risk'] or 0,
                            list(zip(ts_ms, df['high'], df['low'])), '5m', start, end)
    return dict(exit_price=exit_px, fwd_return=ret, mfe_r=exc.mfe_r, mae_r=exc.mae_r,
                excursion_status=(exc.provenance or {}).get('reason') or (exc.provenance or {}).get('status'),
                status='GRADED')


def summary(journal, limit=20) -> list:
    """strategy_id | version/sha | symbol | signal_bar_close_ms | action | skip_reason |
    horizon | resolved_counterfactual_return, one row per statistical sample."""
    return journal.query(
        "SELECT s.strategy_id, COALESCE(s.version_id, substr(s.spec_sha256,1,12)) AS version_sha, "
        "s.symbol, s.signal_bar_close_ms, s.action, s.skip_reason, s.horizon_ms, s.revision_seq, "
        "r.fwd_return AS resolved_counterfactual_return, r.mfe_r, r.mae_r, r.status "
        "FROM scan_observation_samples s LEFT JOIN scan_observation_outcomes r "
        "ON r.observation_id=s.observation_id ORDER BY s.observed_ms DESC LIMIT ?", (limit,))


def format_summary(rows) -> str:
    head = 'strategy_id | version/sha | symbol | signal_bar_close_ms | action | skip_reason | horizon | resolved_counterfactual_return'
    lines = [head]
    for r in rows:
        hz = f"{r['horizon_ms'] // 3_600_000}h" if r['horizon_ms'] else 'n/a'
        ret = 'pending' if r['status'] is None else (f"{r['resolved_counterfactual_return']:+.4f}"
              if r['resolved_counterfactual_return'] is not None else r['status'])
        lines.append(f"{r['strategy_id']} | {r['version_sha']} | {r['symbol']} | {r['signal_bar_close_ms']} | "
                     f"{r['action']} | {r['skip_reason']} | {hz} | {ret}")
    return '\n'.join(lines)


if __name__ == '__main__':          # read-only CLI: python -m trader.learning.scan_observations [db]
    import sqlite3
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else 'data/luffy.db'
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    class _J:
        def query(self, sql, params=()):
            return [dict(r) for r in con.execute(sql, params)]
    try:
        print(format_summary(summary(_J())))
    except sqlite3.OperationalError as exc:
        print(f'no scan observations table yet ({exc})')
