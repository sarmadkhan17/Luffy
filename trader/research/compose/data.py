"""External-context loading for development (I1/I4): the same point-in-time
stores production research reads (`RefStore.load`, `DerivFeed.load`, which
apply receipt eligibility, availability stamps and staleness), read from
quiescent SNAPSHOT copies and cut at the development end.

A required series that cannot be served is reported (`gaps`), and the caller
turns that into BLOCKED_DATA for the dependent study; nothing is imputed and
no other provider is substituted. The identity of what was read (`data_id`)
joins the development cache key so changed context cannot hit a stale result.
"""
from __future__ import annotations

from ...strategy import spec_evidence
from ...strategy.spec_evidence import _SERIES_FOR
from .. import slices
from ..closure import ClosureRefused, digest
from ..closure.manifest import refuse_live


_ID_COLS = ('ts', 'open', 'high', 'low', 'close', 'volume', 'value', 'available_at_ms', 'observed_at_ms',
            'event_time_ms', 'revision_id', 'quality', 'bar_state', 'source', 'instrument_id', 'content_hash')


def _frame_id(df) -> str:
    """Identity over EVERY value and every publication/vintage/availability column present, so a causality or
    revision change can never reuse a cached development result."""
    import hashlib
    import pandas as pd
    cols = [c for c in _ID_COLS if c in df.columns]
    h = hashlib.sha256(canonical_cols(cols).encode())
    h.update(pd.util.hash_pandas_object(df[cols].astype(str) if 'ts' in cols else df[cols], index=False).values.tobytes())
    return h.hexdigest()


def canonical_cols(cols) -> str:
    return '|'.join(cols)


def coverage_gap(key, df, cut_ms, window_start_ms=None):
    """Why a served reference cannot support the development window, else None. A non-empty frame is not
    coverage: invalid rows are NaN, a dead feed stops short of the cut, and a late start leaves the window blind."""
    import numpy as np
    from ...data.references import REFS
    from ..closure.data_access import utc_date
    ref = REFS[key]
    ms = np.asarray(slices._ms(df))
    ok = np.isfinite(df['close'].to_numpy(dtype=float))
    if not ok.any():
        return 'no finite observations (invalid or stale rows only)'
    first, last = int(ms[ok].min()), int(ms[ok].max())
    if int(cut_ms) - (last + ref.close_after_ms) > ref.max_stale_ms:
        return (f'stale: latest valid observation {utc_date(last)} is more than {ref.max_stale_ms / 86_400_000:g} days '
                f'before development end {utc_date(cut_ms)}')
    if window_start_ms is not None and first > int(window_start_ms) + ref.max_stale_ms:
        return f'coverage: first valid observation {utc_date(first)} is after window start {utc_date(window_start_ms)}'
    return None


def _load_refs_retrospective(ref_keys, path, retro, gaps, market, first_obs, ids, provenance):
    """References under the existing retrospective store contract: observations
    closed by the development cut, served from ONE explicit store, tagged as
    retrospective observations. A key the store cannot serve is a gap; there is
    no fallback to another store or to an as-of read."""
    from ...data.published_csv import provenance as csv_provenance
    from ...data.references import REFS, RefStore
    from ...core.types import TF_MS
    from . import graph
    # Rolling features need history BEFORE the window: read back up to MAX_WINDOW bars of the source's own timeframe.
    # Everything stays bounded by the same cut, so chronology is unchanged; only the lower bound moves.
    warm = max(graph.MAX_WINDOW * TF_MS.get(REFS[r.split(':', 1)[1]].tf, 0) for r in ref_keys)
    ref_retro = dict(retro, start_ms=max(0, int(retro['start_ms']) - warm)) if retro.get('start_ms') is not None else dict(retro)
    store = RefStore(db_path=str(refuse_live(path)), retrospective=ref_retro)
    try:
        for r in ref_keys:
            k = r.split(':', 1)[1]
            try:
                df = store.load(k)
            except ValueError as exc:
                gaps.append(dict(requirement=r, reason=str(exc)[:160]))
                continue
            attrs = dict(df.attrs)
            df = slices.before(df, retro['cut_ms'])
            if df is not None:
                df.attrs.update(attrs)
            if df is None or not len(df):
                gaps.append(dict(requirement=r, reason='no retained observations closed by development end'))
                continue
            why = coverage_gap(k, df, retro['cut_ms'], retro.get('start_ms'))
            if why:
                gaps.append(dict(requirement=r, reason=why))
                continue
            market[k] = df
            first_obs[r] = int(slices._ms(df).min())
            ids[r] = _frame_id(df)
            entry = dict(read_mode='retrospective', evidence_scope='retrospective_observation', dataset_id=retro['dataset_id'],
                         input_identity=df.attrs.get('input_identity'), cut_ms=int(retro['cut_ms']), frame_id=ids[r])
            try:
                entry.update(csv_provenance(store, df))
            except (ValueError, KeyError) as exc:
                entry['source_provenance_unavailable'] = str(exc)[:120]
            provenance[r] = entry
    finally:
        store.db.close()


def _lineage(df) -> dict:
    """Candidate input -> source lineage for a served frame: identity, source and revision digest, never values."""
    col = lambda c: sorted({str(x) for x in df[c].dropna().unique()}) if c in df else []   # noqa: E731
    return dict(read_mode=df.attrs.get('read_mode'), rows=int(len(df)), frame_id=_frame_id(df),
                instrument_ids=col('instrument_id')[:4], sources=col('source')[:4],
                revision_digest=digest(df['revision_id'].tolist()) if 'revision_id' in df else None,
                first_ms=int(slices._ms(df).min()), last_ms=int(slices._ms(df).max()))


def load_external(requires, snapshot_path, derivs_path, symbols, dev_end_ms: int, *, world_path=None,
                  references_path=None, retrospective=None, window_start_ms=None, warmup_ms=0) -> dict:
    """{market, derivs, gaps, data_id, first_obs, provenance}. `requires` are
    DSL requirement names ('ref:spx', 'funding', ...).

    `retrospective` (development_mode RETROSPECTIVE) is the dataset contract
    {dataset_id, cut_ms}; references are then read with the retrospective
    RefStore from `references_path`, or from the snapshot when none is given.
    A detached `references_path` is refused outside RETROSPECTIVE: its
    observations were captured after the fact and are not replay evidence."""
    from ...data.derivatives import DerivFeed
    from ...data.references import RefStore
    market, derivs, gaps, first_obs, ids, provenance = {}, {s: {} for s in symbols}, [], {}, {}, {}
    if references_path is not None and retrospective is None:
        raise ClosureRefused('references_path_requires_retrospective_development')
    world = None
    if 'world' in requires:
        from ...world.context import load_context
        if world_path is None:
            gaps.append(dict(requirement='world', reason='no retained World snapshot supplied'))
        else:
            world = load_context(refuse_live(world_path), as_of_ms=int(dev_end_ms), immutable=True,
                                 allow_reconstruction=retrospective is not None,
                                 dataset_id=(retrospective or {}).get('dataset_id'))
            ids['world'] = world.context_id
            allowed = ('RETAINED', 'RECONSTRUCTED_RETROSPECTIVE') if retrospective is not None else ('RETAINED',)
            if world.source_status not in allowed or not world.history.cuts:
                gaps.append(dict(requirement='world', reason='retained World history unavailable at development cut'))
            provenance['world'] = dict(source_status=world.source_status,
                                      read_mode='retrospective' if retrospective is not None else 'replay',
                                      evidence_scope='retrospective_observation' if world.allow_reconstruction else 'point_in_time',
                                      context_id=world.context_id)
    ref_keys = [r for r in requires if isinstance(r, str) and r.startswith('ref:')]
    if ref_keys and retrospective is not None:
        _load_refs_retrospective(ref_keys, references_path or snapshot_path, retrospective, gaps, market, first_obs, ids,
                                 provenance)
    elif ref_keys:
        store = RefStore(refuse_live(snapshot_path), read_only=True)
        try:
            got = spec_evidence.load_refs(ref_keys, store, as_of_ms=int(dev_end_ms))
        finally:
            store.db.close()
        for r in ref_keys:
            k = r.split(':', 1)[1]
            df = slices.before(got.get(k), dev_end_ms) if got.get(k) is not None else None
            if df is None or not len(df):
                gaps.append(dict(requirement=r, reason='no eligible rows as of development end'))
                continue
            why = coverage_gap(k, df, dev_end_ms, window_start_ms)
            if why:
                gaps.append(dict(requirement=r, reason=why))
                continue
            market[k] = df
            first_obs[r] = int(slices._ms(df).min())
            ids[r] = _frame_id(df)
    series_reqs = [r for r in requires if r in _SERIES_FOR]
    if series_reqs:
        if derivs_path is None:
            gaps += [dict(requirement=r, reason='no derivatives snapshot supplied') for r in series_reqs]
        else:
            if retrospective is not None:
                # SAME dataset/cut contract the candle frames and references use: history may reach back by `warmup_ms`
                # before the window (rolling derivative features), never past the cut, and a historical as-of read is
                # refused by the store itself. Retained market_revisions are still required; nothing is fabricated.
                d = dict(retrospective)
                if d.get('start_ms') is not None:
                    d['start_ms'] = max(0, int(d['start_ms']) - int(warmup_ms))
                feed = DerivFeed(db_path=str(refuse_live(derivs_path)), retrospective=d)
            else:
                feed = DerivFeed(db_path=str(refuse_live(derivs_path)), read_only=True)
            try:
                for s in symbols:
                    try:
                        got = spec_evidence.load_derivs(s, series_reqs, feed, as_of_ms=None if retrospective is not None else int(dev_end_ms))
                    except ValueError as exc:
                        gaps += [dict(requirement=r, reason=f'{s}: {exc}'[:160]) for r in series_reqs]
                        continue
                    derivs[s] = {}
                    for k, v in got.items():
                        attrs = dict(v.attrs)
                        v = slices.before(v, dev_end_ms)
                        if v is not None:
                            v.attrs.update(attrs)
                        derivs[s][k] = v
                    for r in series_reqs:
                        v = derivs[s].get(_SERIES_FOR[r])
                        if v is not None and len(v):
                            ids[f'{r}:{s}'] = _frame_id(v)
                            provenance[f'{r}:{s}'] = _lineage(v)
                            first_obs[r] = min(first_obs.get(r, 1 << 62), int(slices._ms(v).min()))
            finally:
                feed.db.close()
            for r in series_reqs:
                have = sum(1 for s in symbols if len(derivs[s].get(_SERIES_FOR[r], ())))
                if have < len(symbols):
                    gaps.append(dict(requirement=r, reason=f'{have}/{len(symbols)} symbols have eligible rows'))
    return dict(market=market or None, derivs=derivs, world=world, gaps=gaps, first_obs=first_obs,
                provenance=provenance, data_id=digest(sorted(ids.items())))
