"""Independent, bounded World producer for research, on the EXISTING shared WorldModel / WorldContext.

The retained World history normally comes from Attention scans. Producing it for a study must not revive full-universe
historical Attention/lifecycle scans in the market cycle, so this builds the SAME typed `WorldModel` (via
`observability.world_producer.build`, the same perception, the same `volume_z` arithmetic) from a bounded set of closed
bars: only a VOLUME SHORTLIST (default 30, ranked at each cut by trailing quote volume known at that cut) is a member, so
the cost is cuts x 30 symbols, never the whole universe. Output is a store `load_context` reads unchanged
(`scans(as_of_ms, payload)` with a `world-model-live-receipt.v1` receipt), so development, qualification and inference all
use one reader and one record identity.

Chronology: an observation is available at its bar close; a bar whose receipt arrived later than the cut is not used (the
Dataset's own availability rule), so a late capture cannot appear as an earlier observation. A RETROSPECTIVE study reads
retained bars that were captured after the fact: the store is labelled `retrospective_observation` and its availability is the
event close, never claimed as the original availability.
"""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from dataclasses import replace

import numpy as np
import pandas as pd

from ...cognition.attention import CognitionConfig
from ...cognition.contracts import Candle, Dataset, Membership
from ...core.types import TF_MS
from ...observability import world_producer as WP
from ...world import WorldModelRecord, Observation, Quality, WorldHistory

SHORTLIST = 30
RANK_BARS = 6            # trailing 24h of 4h bars for the quote-volume ranking
MAX_CUTS = 4000          # hard bound on one production run


def _ms(df):
    from ...data.references import to_ms
    return to_ms(df['ts'])


def dataset_from_frames(frames: dict, tf: str, *, retrospective: bool, source: str = 'research.world') -> Dataset:
    """A detached `Dataset` of CLOSED bars only (the type Attention's producer consumes). `frames[symbol]` carries ts/open/high/
    low/close/volume and, when present, `available_at_ms`."""
    tf_ms = TF_MS[tf]
    candles, members = {}, []
    for sym, df in frames.items():
        opens = _ms(df)
        avail = df['available_at_ms'].to_numpy(dtype='float64') if 'available_at_ms' in df else np.full(len(df), np.nan)
        by_open = {}
        for k in range(len(df)):
            o = int(opens[k])
            close_ms = o + tf_ms
            if not retrospective and not np.isfinite(avail[k]):
                continue                    # absent acquisition evidence is not original-time availability
            if not retrospective and 'quality' in df and df['quality'].iloc[k] != 'VALID':
                continue
            a = close_ms if retrospective else int(avail[k])
            by_open[o] = [Candle(sym, o, float(df['open'].iloc[k]), float(df['high'].iloc[k]), float(df['low'].iloc[k]),
                                 float(df['close'].iloc[k]), float(df['volume'].iloc[k]), max(a, close_ms), source, close_ms)]
        if by_open:
            candles[sym] = by_open
            members.append(Membership(sym, min(by_open), None,
                                      min(c[0].available_ms for c in by_open.values()), source))
    return Dataset(timeframe=tf, tf_ms=tf_ms, candles=candles, membership=members, participation={}, decision_times=[])


class ShortlistDataset:
    """Per-cut view of a Dataset restricted to the volume shortlist; the producer sees exactly these members."""

    def __init__(self, ds: Dataset, n: int = SHORTLIST):
        self._ds, self.n = ds, int(n)
        self.timeframe, self.tf_ms = ds.timeframe, ds.tf_ms
        self._volume_measurements = {}
        self.rank_bars = max(1, 86_400_000 // self.tf_ms)

    def bar_asof(self, symbol, open_ms, as_of):
        return self._ds.bar_asof(symbol, open_ms, as_of)

    def members_asof(self, as_of):
        """Top-N by trailing quote volume of bars CLOSED and AVAILABLE at `as_of`; ties by symbol (deterministic)."""
        anchor = (as_of // self.tf_ms) * self.tf_ms
        scored = []
        for sym in self._ds.members_asof(as_of):
            total, seen = 0.0, 0
            for j in range(1, self.rank_bars + 1):
                b = self._ds.bar_asof(sym, anchor - j * self.tf_ms, as_of)
                if b is not None and math.isfinite(b.volume) and math.isfinite(b.close):
                    total += b.volume * b.close
                    seen += 1
            if seen == self.rank_bars:
                scored.append((-total, sym))
        return sorted(s for _, s in sorted(scored)[:self.n])


def iter_rows(frames: dict, tf: str, cuts, *, retrospective: bool, shortlist: int = SHORTLIST):
    """[(as_of_ms, payload_json)] for each cut that yields a model. A cut whose model cannot be built is simply absent
    (retention gaps stay gaps; nothing is filled)."""
    ds = ShortlistDataset(dataset_from_frames(frames, tf, retrospective=retrospective), shortlist)
    from itertools import islice
    for as_of in islice(cuts, MAX_CUTS):
        try:
            model = WP.build(ds, int(as_of), f'research-world:{int(as_of)}')
            scope = 'retrospective_observation' if retrospective else 'point_in_time'
            # Bind the data mode INSIDE the immutable model. Removing a scan's outer label cannot
            # turn a reconstruction into original-time evidence or enable protected qualification.
            states = []
            for state in model.states:
                # Observable descriptions, not learned winning regimes. Every input is closed and
                # available at this cut; missing windows remain absent rather than invented zero.
                anchor = int(as_of) // ds.tf_ms * ds.tf_ms
                day = 86_400_000 // ds.tf_ms
                week_points = 604_800_000 // ds.tf_ms + 1
                bars = [ds.bar_asof(state.instrument, anchor - j * ds.tf_ms, int(as_of))
                        for j in range(1, min(2048, max(week_points, day + 1)) + 1)]
                extra = []
                if day and len(bars) >= day + 1 and all(b is not None for b in bars[:day + 1]):
                    recent = bars[:day]
                    if all(np.isfinite(b.close) and b.close > 0 and np.isfinite(b.volume) and b.volume >= 0 for b in bars[:day + 1]):
                        measurements = [('price_return_24h', recent[0].close / bars[day].close - 1, 'fraction'),
                                        ('quote_volume_24h', sum(b.volume * b.close for b in recent), 'quote_notional')]
                        if len(bars) == week_points and all(b is not None and np.isfinite(b.close) and b.close > 0 for b in bars):
                            closes = np.array([b.close for b in reversed(bars)])
                            measurements.append(('realized_volatility_7d', float(np.std(np.diff(np.log(closes)))), 'fraction'))
                        for kind, val, unit in measurements:
                            extra.append(Observation(instrument=state.instrument, timestamp_ms=anchor,
                                observed_at_ms=int(as_of), available_at_ms=int(as_of), timeframe=tf,
                                horizon='intraday', kind=kind, value=float(val), unit=unit, quality=Quality.VALID,
                                source='research.world', source_ref='source-model:' + model.model_id,
                                max_age_ms=ds.tf_ms, transform_version='observable-descriptions.v1'))
                marker = Observation(instrument=state.instrument, timestamp_ms=int(as_of), observed_at_ms=int(as_of),
                    available_at_ms=int(as_of), timeframe=tf, kind='research_evidence_scope', value=scope,
                    quality=Quality.VALID, source='research.world', source_ref='source-model:' + model.model_id,
                    uncertainty={'evidence_scope': scope})
                states.append(replace(state, observations=state.observations + tuple(extra) + (marker,)))
            model = replace(model, states=tuple(states))
            rec = WorldModelRecord.from_model(model)
        except Exception:                                          # noqa: BLE001 - a failed cut is an absent cut
            continue
        receipt = WP.receipt(WP.OK, None, int(as_of), rec, WP._counts(model))
        receipt['producer_id'] = 'research.compose.world_producer.v1'
        payload = dict(as_of_ms=int(as_of), world_model=receipt, producer='research.compose.world_producer.v1',
                       shortlist=int(shortlist), evidence_scope='retrospective_observation' if retrospective else 'point_in_time')
        yield int(as_of), json.dumps(payload, sort_keys=True, allow_nan=False)


def produce_rows(frames: dict, tf: str, cuts, *, retrospective: bool, shortlist: int = SHORTLIST) -> list:
    """Compatibility list API; store production streams through iter_rows."""
    return list(iter_rows(frames, tf, cuts, retrospective=retrospective, shortlist=shortlist))


def snapshot_context(snapshot):
    """Point-in-time inference from the ALREADY admitted symbol's bounded cached inputs.

    No reads, network, controls or whole-universe scan. Timeframes stay distinct. An absent
    acquisition receipt stays missing. This uses the same producer/formulas as development.
    """
    from datetime import datetime
    from ...world.context import WorldContext
    cut = int(datetime.fromisoformat(snapshot.ts).timestamp() * 1000)
    models = []
    for tf, df in sorted(snapshot.dfs.items()):
        if tf not in TF_MS or df is None or not len(df):
            continue
        width = min(2048, max(CognitionConfig().window + 1, 604_800_000 // TF_MS[tf] + 1))
        rows = produce_rows({snapshot.symbol: df.tail(width)}, tf, [cut], retrospective=False, shortlist=1)
        for _, raw in rows:
            model = WorldModelRecord.from_json(json.loads(raw)['world_model']['record_json']).reconstruct()
            if model.states:
                models.append(model)
    if not models:
        return WorldContext(WorldHistory(()), 'SOURCE_UNAVAILABLE', cut)
    observations = tuple(o for m in models for s in m.states for o in s.observations)
    from ...world import WorldState
    model = replace(models[0], states=(WorldState(snapshot.symbol, cut, observations),))
    return WorldContext(WorldHistory((WorldModelRecord.from_model(model),)), 'RETAINED', cut)


def write_store(path, rows) -> dict:
    """The research World store: the table `observability.store.read_world_history` reads. One row per cut."""
    path = Path(path)
    tmp = path.with_suffix('.pending')
    tmp.unlink(missing_ok=True)
    count = 0
    def counted():
        nonlocal count
        for row in rows:
            count += 1
            yield row
    try:
        with closing(sqlite3.connect(tmp)) as db:
            db.execute('CREATE TABLE scans(as_of_ms INTEGER NOT NULL, payload TEXT NOT NULL)')
            db.executemany('INSERT INTO scans VALUES (?,?)', counted())
            db.commit()
        tmp.replace(path)
        return dict(path=str(path), cuts=count)
    finally:
        tmp.unlink(missing_ok=True)


def produce_store(frames: dict, tf: str, path, *, start_ms: int, end_ms: int, retrospective: bool, shortlist: int = SHORTLIST) -> dict:
    """Every closed-bar cut in [start_ms, end_ms] -> a retained World store. Bounded by MAX_CUTS and the shortlist."""
    step = TF_MS[tf]
    first = ((int(start_ms) + step - 1) // step) * step
    cuts = range(first, int(end_ms) + 1, step)
    if len(cuts) > MAX_CUTS:
        raise ValueError(f'world_cuts_exceed_bound:{len(cuts)}>{MAX_CUTS}')
    rows = iter_rows(frames, tf, cuts, retrospective=retrospective, shortlist=shortlist)
    out = write_store(path, rows)
    out.update(cuts_requested=len(cuts), shortlist=int(shortlist), retrospective=bool(retrospective),
               window=CognitionConfig().window)
    return out
