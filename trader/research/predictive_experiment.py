"""Exact translation into existing planner/runner jobs and registered policy.

The metadata scan reads timestamps/counts only. The numerical worker sees a
frozen local copy and uses the same controls, nulls, ablations and referee.
"""
from dataclasses import asdict
from contextlib import closing
from pathlib import Path
import sqlite3
import json

from trader.cognition import predictive as P
from . import slices, evaluate, referee, thresholds
from .universe import DISCOVERY, HELDOUT
from ..strategy import null_baseline


def evidence_policy(cfg):
    r=cfg.get('research') or {}
    return dict(min_slice_bars=slices.MIN_SLICE_BARS,
        min_discovery_symbols=int(r.get('min_discovery_symbols',16)),
        min_symbol_trades=evaluate.MIN_SYMBOL_TRADES,
        min_projected_trades=evaluate.MIN_PROJECTED_TRADES,
        min_projected_markets=evaluate.MIN_PROJECTED_MARKETS,
        min_threshold_samples=int(r.get('min_threshold_samples',thresholds.MIN_SAMPLES)),
        min_referee_trades=referee.MIN_TRADES,registered_policy_hash=P.digest(r))


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
    db.execute('PRAGMA query_only=ON')
    return db


def translate(h, candles_path, cfg, as_of_ms):
    P.validate(h)
    if P.TRANSFORMS[h.transformation]['shape'] != 'combination':
        return dict(status='UNSUPPORTED_EXPERIMENT_SHAPE', source_hypothesis_id=h.hypothesis_id)
    if as_of_ms < h.created_at:
        raise ValueError('leakage_boundary')
    # Newly closed bars only; even warmup comes AFTER generation evidence.
    start = (h.generation_end_ms // 14_400_000 + 1) * 14_400_000
    if start - h.generation_end_ms < 30_000:
        start += 14_400_000
    end = as_of_ms - 14_400_000
    try:
        with closing(readonly(candles_path)) as db:
            db.execute('BEGIN')
            rows = db.execute('SELECT symbol,MIN(ts),MAX(ts),COUNT(*) FROM candles '
                "WHERE tf='4h' AND ts>=? AND ts<=? GROUP BY symbol", (start, end)).fetchall()
            valid = [r for r in rows if r[0] in DISCOVERY]
            if not valid:
                return dict(status='DATA_INSUFFICIENT', source_hypothesis_id=h.hypothesis_id,
                            reason='no_closed_post_generation_tuning_window')
            lo, hi = min(r[1] for r in valid), max(r[2] for r in valid)
            cut = lo + int((hi-lo) * slices.DISCOVERY_FRAC)
            counts = {part: dict(db.execute('SELECT symbol,COUNT(*) FROM candles '
                "WHERE tf='4h' AND ts>=? AND ts<? GROUP BY symbol", bounds))
                for part, bounds in (('discovery', (start, cut)), ('protected_b', (cut, end+1)))}
    except (sqlite3.Error, OSError):
        return dict(status='DATA_INSUFFICIENT', source_hypothesis_id=h.hypothesis_id,
                    reason='candle_metadata_unavailable')
    r = cfg.get('research') or {}
    if '4h' not in r.get('horizons',['4h']) or 'fixed' not in r.get('geometries',['trail','fixed']):
        return dict(status='UNSUPPORTED_EXPERIMENT_SHAPE',source_hypothesis_id=h.hypothesis_id)
    minimum = int(r.get('min_discovery_symbols', 16))
    disc = [s for s in DISCOVERY if counts['discovery'].get(s, 0) >= slices.MIN_SLICE_BARS]
    held = [s for s in HELDOUT if counts['discovery'].get(s, 0) >= slices.MIN_SLICE_BARS]
    later = [s for s in DISCOVERY + HELDOUT if counts['protected_b'].get(s, 0) >= slices.MIN_SLICE_BARS]
    if len(disc) < minimum or len(held) < null_baseline.MIN_SYMBOLS or len(later) < null_baseline.MIN_SYMBOLS:
        return dict(status='DATA_INSUFFICIENT', source_hypothesis_id=h.hypothesis_id,
                    reason='existing_slice_or_universe_floor', counts=counts)
    body = dict(schema='predictive-experiment.v1', source_hypothesis_id=h.hypothesis_id,
        hypothesis=asdict(h), predictor=list(h.predictor_definition), target=h.target_definition,
        horizon=h.horizon, direction=h.direction, timeframe='4h', geometry='fixed',
        split=dict(schema='post-generation-calendar-split.v1', start_ms=start, cut_ms=cut,
                   end_ms=end, generation_end_ms=h.generation_end_ms,
                   policy='research.slices.DISCOVERY_FRAC', fraction=slices.DISCOVERY_FRAC,
                   discovery_symbols=list(DISCOVERY), heldout_symbols=list(HELDOUT)),
        leakage_guard=h.forbidden_leakage_boundary,
        baseline_null='existing window control + one-part ablations + entry rotation + protected common rotation',
        metrics=['consistency_p', 'consistency_p_dep', 'portfolio.total_pct', 'portfolio.max_dd_pct'],
        minimum_evidence=evidence_policy(cfg), registered_policy_json=P.canonical(r),
        evaluation_config_json=P.canonical(dict(research=r,risk=cfg.get('risk') or {})),
        falsification_outcome='UNSUPPORTED only for powered measured failure; missing power remains INCONCLUSIVE/DATA_INSUFFICIENT',
        status='READY', authority='EXPERIMENT_ONLY')
    return dict(body, experiment_id=P.digest(body))


def validate(experiment):
    if experiment.get('experiment_id') != P.digest({k:v for k,v in experiment.items() if k != 'experiment_id'}):
        raise ValueError('experiment_integrity')
    h = P.hypothesis_from_dict(experiment['hypothesis'])
    s = experiment['split']
    policy=json.loads(experiment['registered_policy_json'])
    if (experiment['minimum_evidence']!=evidence_policy(dict(research=policy))
            or json.loads(experiment['evaluation_config_json'])['research']!=policy):
        raise ValueError('unregistered_evidence_policy')
    if (experiment['source_hypothesis_id'] != h.hypothesis_id or experiment['status'] != 'READY'
            or s['schema'] != 'post-generation-calendar-split.v1'
            or not h.created_at == s['generation_end_ms'] < s['start_ms'] < s['cut_ms'] < s['end_ms']
            or s['fraction'] != slices.DISCOVERY_FRAC
            or s['discovery_symbols'] != DISCOVERY or s['heldout_symbols'] != HELDOUT
            or experiment['predictor'] != list(h.predictor_definition)
            or experiment['target'] != h.target_definition or experiment['horizon'] != h.horizon
            or P.TRANSFORMS[h.transformation]['shape'] != 'combination'
            or experiment['schema'] != 'predictive-experiment.v1'
            or experiment['direction'] != h.direction or experiment['timeframe'] != '4h'
            or experiment['leakage_guard'] != h.forbidden_leakage_boundary
            or s['policy'] != 'research.slices.DISCOVERY_FRAC'
            or s['start_ms'] - s['generation_end_ms'] < 30_000
            or experiment['baseline_null'] != 'existing window control + one-part ablations + entry rotation + protected common rotation'
            or experiment['metrics'] != ['consistency_p', 'consistency_p_dep', 'portfolio.total_pct', 'portfolio.max_dd_pct']
            or experiment['falsification_outcome'] != 'UNSUPPORTED only for powered measured failure; missing power remains INCONCLUSIVE/DATA_INSUFFICIENT'
            or experiment['geometry'] != 'fixed' or experiment['authority'] != 'EXPERIMENT_ONLY'):
        raise ValueError('leakage_or_experiment_contract')
    return h


def freeze_candles(source, dest, experiment):
    """Copy only post-generation closed bars; never open production writable."""
    validate(experiment)
    s = experiment['split']
    if Path(dest).exists():
        with closing(readonly(dest)) as old:
            required={'candles','candle_floor','market_revisions','market_raw_sources','market_revision_cut'}
            if not required <= {r[0] for r in old.execute('SELECT name FROM sqlite_master')}:
                raise ValueError('frozen_candle_provenance_unavailable')
        return
    temporary = Path(str(dest) + '.tmp')
    if temporary.exists():
        temporary.unlink()
    with closing(readonly(source)) as src, sqlite3.connect(temporary) as dst:
        src.execute('BEGIN')
        columns = src.execute('PRAGMA table_info(candles)').fetchall()
        if [c[1] for c in columns] != ['symbol','tf','ts','open','high','low','close','volume','taker_buy']:
            raise ValueError('unsupported_candle_schema')
        dst.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,taker_buy REAL,PRIMARY KEY(symbol,tf,ts))')
        dst.execute('CREATE TABLE candle_floor(symbol TEXT,tf TEXT,first_ts INTEGER,PRIMARY KEY(symbol,tf))')
        from ..data import market_provenance as provenance
        from ..data.feed import DataFeed
        provenance.init(dst)
        retained = bool(src.execute("SELECT 1 FROM sqlite_master WHERE name='market_revisions'").fetchone())
        for sym in DISCOVERY + HELDOUT:
            rows = src.execute("SELECT * FROM candles WHERE symbol=? AND tf='4h' AND ts>=? AND ts<=? ORDER BY ts LIMIT 200000",
                               (sym,s['start_ms'],s['end_ms'])).fetchall()
            dst.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)', rows)
            if retained:
                revisions = src.execute("SELECT * FROM market_revisions WHERE series_key=? AND event_ms>=? AND event_ms<=? AND available_ms<=? AND observed_ms<=? ORDER BY rowid",
                    (DataFeed._series_key(sym,'4h'),s['start_ms'],s['end_ms'],
                     s['end_ms']+14_400_000,s['end_ms']+14_400_000)).fetchall()
                dst.executemany('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)', revisions)
    temporary.replace(dest)


def planner_batch(led, cfg, experiment):
    """The existing control gate is the ONLY path to an evaluate batch."""
    from . import planner
    from .combo import Combination, subsets
    h = validate(experiment)
    if not led.gauges('4h'):
        return planner.Batch('measure', '4h', reason='predictive thresholds on isolated discovery')
    try:
        c = Combination(P.parts(h, led.gauges('4h')), '4h', 'fixed',
                        trigger=h.hypothesis_id, round='predictive', evaluation_scope=experiment['experiment_id'])
    except ValueError:
        return planner.Batch('idle', '4h', reason='DATA_INSUFFICIENT')
    ablations = subsets(c)
    pending = [a for a in ablations if not led.has(a.hash)]
    offered = pending or [c]
    take, controls = planner._gate(offered, led, '4h', 'fixed', int((cfg.get('research') or {}).get('batch_combos', 40)))
    if controls:
        return planner.Batch('evaluate', '4h', 'fixed', 'control', controls, 'existing window control')
    if take:
        return planner.Batch('evaluate', '4h', 'fixed', 'predictive', take, 'registered hypothesis + mandatory ablations')
    return planner.Batch('idle', '4h', reason='experiment_scored')
