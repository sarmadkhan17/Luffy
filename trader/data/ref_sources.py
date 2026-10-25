"""Fetch each reference market, and build the alt index.

Every fetcher takes an injectable `get` (or ccxt-style `fetch`) so tests
never touch the network, and every fetcher returns the same frame shape —
COLS, `ts` UTC, sorted, floats, NaN where nothing was measured — so the
store and the DSL never special-case a source.

Yahoo's chart API is unofficial and can change without notice. A failure
there must degrade to a stale reference (-> NaN -> UNTESTED), never to a
fabricated value; `refresh_all` isolates each source for exactly that.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

from ..core.types import TF_MS

log = logging.getLogger(__name__)

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
DEFILLAMA_STABLES = "https://stablecoins.llama.fi/stablecoincharts/all"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
UA = {"User-Agent": "Mozilla/5.0 (luffy reference recorder)"}
COLS = ["ts", "open", "high", "low", "close", "volume"]


def get_json(url, params=None, timeout: float = 25.0):
    r = requests.get(url, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLS)


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df = df.astype({c: float for c in COLS[1:]})
    return (df.dropna(subset=["close"]).drop_duplicates("ts", keep="last")
              .sort_values("ts").reset_index(drop=True))[COLS]


def yahoo_frame(ticker: str, interval: str, rng: str,
                get=get_json) -> pd.DataFrame:
    d = get(YAHOO + quote(ticker, safe=""),
            {"range": rng, "interval": interval})
    res = ((d or {}).get("chart") or {}).get("result") or []
    if not res or not res[0].get("timestamp"):
        return _empty()
    r = res[0]
    n = len(r["timestamp"])
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def col(k):
        v = q.get(k)
        return v if v is not None and len(v) == n else [None] * n

    frame = _finish(pd.DataFrame({
        "ts": pd.to_datetime(r["timestamp"], unit="s", utc=True),
        "open": col("open"), "high": col("high"), "low": col("low"),
        "close": col("close"), "volume": col("volume")}))
    frame.attrs.update(source=YAHOO+quote(ticker, safe=''), raw_source_record=d)
    return frame


def defillama_stables(get=get_json) -> pd.DataFrame:
    ts, close = [], []
    raw = get(DEFILLAMA_STABLES) or []
    for row in raw:
        try:
            v = float((row.get("totalCirculatingUSD") or {})["peggedUSD"])
            ts.append(int(row["date"]))
            close.append(v)
        except (TypeError, ValueError, KeyError):
            continue
    if not ts:
        return _empty()
    df = pd.DataFrame({"ts": pd.to_datetime(ts, unit="s", utc=True),
                       "close": close})
    for c in ("open", "high", "low", "volume"):
        df[c] = np.nan
    df = _finish(df)
    df.attrs.update(source=DEFILLAMA_STABLES, raw_source_record=raw)
    return df


def coingecko_global(get=get_json) -> dict:
    raw = get(COINGECKO_GLOBAL) or {}
    d = raw.get("data") or {}
    total = float((d.get("total_market_cap") or {})["usd"])
    pct = d.get("market_cap_percentage") or {}
    btc, usdt = float(pct["btc"]), float(pct["usdt"])
    class SourceSnapshot(dict):
        pass
    result = SourceSnapshot(cg_btc_d=btc, cg_usdt_d=usdt, cg_total=total,
                            cg_total2=total * (1.0 - btc / 100.0))
    result.raw_source_record = raw
    return result


def binance_klines(fetch, symbol: str, tf: str, since_ms: int, now_ms: int,
                   page: int = 1000) -> pd.DataFrame:
    """Page forward until the venue returns nothing new.

    A short page is NOT the end: asked for 1,500 bars the venue answered
    1,000, and a loop that stopped on the first short page stored BTCDOM
    from 2021-06-21 to 2021-12-04 and nothing after. Stop only on an empty
    page or one that makes no progress.
    """
    rows, since, step = [], int(since_ms), TF_MS[tf]
    while since < now_ms:
        batch = fetch(symbol, tf, since=since, limit=page) or []
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1][0]) + step
        if nxt <= since:
            break
        since = nxt
    if not rows:
        return _empty()
    df = pd.DataFrame([r[:6] for r in rows], columns=COLS)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = _finish(df)
    df.attrs.update(source='binance:public:klines', raw_source_record=rows)
    return df


# ── the alt index ─────────────────────────────────────────────────────────
def _member_closes(frames: dict) -> dict:
    """{member: sorted, de-duplicated close Series}; members under 2 bars drop out."""
    closes = {}
    for sym, df in frames.items():
        if df is None or len(df) < 2:
            continue
        s = pd.Series(df["close"].to_numpy(dtype=float),
                      index=pd.to_datetime(df["ts"], utc=True))
        closes[sym] = s[~s.index.duplicated(keep="last")].sort_index()
    return closes


def _grid_of(closes: dict) -> pd.DatetimeIndex:
    lo = min(s.index[0] for s in closes.values())
    hi = max(s.index[-1] for s in closes.values())
    return pd.date_range(lo, hi, freq="4h")


def _full_numeric(closes: dict, grid, min_members: int):
    """(cumulative product, level) over the whole grid -- the definition."""
    panel = pd.DataFrame(closes).sort_index()
    rets = panel.reindex(grid).pct_change(fill_method=None)
    measured = rets.notna().sum(axis=1)
    r = rets.mean(axis=1).where(measured >= min_members)
    cum = (1.0 + r.fillna(0.0)).cumprod()
    return cum.to_numpy(), (cum * 100.0).where(r.notna()).to_numpy()


def _suffix_numeric(closes: dict, grid, min_members: int, start: int, cum_prev: float):
    """The full definition's rows grid[start:], continuing from `cum_prev`, the
    cumulative product through row start-1. Row start-1 is read only as the
    predecessor close of the first return; the multiplication order is the
    full cumprod's, so the result is bit-identical."""
    sub = grid[start - 1:] if start > 0 else grid
    panel = pd.DataFrame({sym: s.reindex(sub) for sym, s in closes.items()})
    rets = panel.pct_change(fill_method=None)
    measured = rets.notna().sum(axis=1)
    r = rets.mean(axis=1).where(measured >= min_members)
    if start > 0:
        r = r.iloc[1:]
    x = (1.0 + r.fillna(0.0)).to_numpy(copy=True)
    if start > 0 and len(x):
        x[0] = cum_prev * x[0]
    cum = np.cumprod(x)
    return cum, np.where(r.notna().to_numpy(), cum * 100.0, np.nan)


def _alts_frame(grid, level, frames: dict, min_members: int) -> pd.DataFrame:
    df = pd.DataFrame({"ts": grid, "close": level})
    for c in ("open", "high", "low", "volume"):
        df[c] = np.nan
    result = _finish(df)
    from . import market_provenance as mp
    constituents = _constituents(frames)
    if constituents is not None:
        result.attrs.update(source='derived:equal_weight_alt_index.v1',
                            raw_source_record={'derivation':'equal_weight_alt_index.v1',
                              'min_members':min_members,
                              'constituents':constituents})
    return result


def _constituents(frames: dict):
    """Constituent revision IDs per member, or None unless every member is receipt-qualified."""
    from . import market_provenance as mp
    qualified = all(member.attrs.get('slim_qualified') or all(key in member for key in mp.META)
                    for member in frames.values() if member is not None and len(member))
    if not qualified:
        return None
    return {sym: member['revision_id'].tolist()
            for sym, member in frames.items() if member is not None and len(member)}


def alts_index(frames: dict, min_members: int = 5) -> pd.DataFrame:
    """Equal-weight index of the alt perps, on a 4h grid, close-only.

    Each bar's move is the mean of the members' own bar returns. A member
    with no bar at t (or t-1) has no return there — its gap is not filled —
    and a bar with fewer than `min_members` measured returns is NaN rather
    than a level. The caller's member list excludes BTC and stablecoins.
    """
    closes = _member_closes(frames)
    if not closes:
        return _empty()
    grid = _grid_of(closes)
    _, level = _full_numeric(closes, grid, min_members)
    return _alts_frame(grid, level, frames, min_members)


# ── one refresh of everything ─────────────────────────────────────────────
#: BTCDOM/USDT was listed on 2021-06-17; ask from the start of that month
BTCDOM_FROM_MS = 1_622_505_600_000


def _alts_witness(feed, store, names):
    """Committed change witnesses of every member series plus the stored output.

    None unless every member is proven: an unproven member can never be skipped.
    """
    from . import market_provenance as mp
    try:
        parts = {s: feed.series_witness(s, '4h') for s in names}
        out = mp.witness(store.db, 'reference:alts')
    except Exception:
        return None
    if any(w is None for w in parts.values()):
        return None
    return dict(members=parts, output=out)


def _slim(entry, sym):
    """A member frame rebuilt from the committed index state (no store read)."""
    ts, close = entry.series[sym]
    rid = (entry.ancestry or {}).get(sym)
    if rid is None or len(rid) != len(ts):
        return None
    return _slim_frame(pd.to_datetime(ts, utc=True), close, rid)


def _slim_frame(ts, close, rid):
    df = pd.DataFrame({'ts': ts, 'close': np.asarray(close, dtype=float), 'revision_id': list(rid)})
    df.attrs['slim_qualified'] = True
    return df


def _member_frames(feed, names, now, entry, saved, witness, report):
    """Per member: reuse committed state, read only the changed tail, or (cold /
    unprovable change) read the member's full window. Never reads an unchanged
    member, never rereads the unchanged part of a changed one."""
    frames = {}
    for sym in names:
        old = saved['members'].get(sym) if saved and entry is not None else None
        now_w = witness['members'][sym] if witness else None
        pending = saved['next_ready'].get(sym) if saved else None
        if old is not None and old == now_w and (pending is None or now < pending):
            frame = _slim(entry, sym)
            if frame is not None:
                frames[sym] = frame
                report['reused'] += 1
                continue
        if (old is not None and now_w is not None and old[1:] == now_w[1:]
                and (pending is None or now < pending) and sym in entry.series):
            change = feed.committed_changes(sym, '4h', old[0])
            if change is not None and change[1] and change[0] is not None:
                base = _slim(entry, sym)
                tail = feed.ohlcv_tail(sym, '4h', as_of_ms=now, min_event_ms=change[0])
                if base is not None:
                    floor = pd.Timestamp(change[0], unit='ms', tz='UTC')
                    kept = base[pd.to_datetime(base['ts'], utc=True) < floor]
                    if tail is not None and len(tail):
                        ts = list(pd.to_datetime(kept['ts'], utc=True)) + list(pd.to_datetime(tail['ts'], utc=True))
                        close = list(kept['close']) + list(tail['close'])
                        rid = list(kept['revision_id']) + list(tail['revision_id'])
                    else:
                        ts, close, rid = list(pd.to_datetime(kept['ts'], utc=True)), list(kept['close']), list(kept['revision_id'])
                    frames[sym] = _slim_frame(pd.DatetimeIndex(ts), close, rid)
                    report['tail'] += 1
                    report['tail_rows'] += 0 if tail is None else len(tail)
                    continue
        frames[sym] = feed.cached_ohlcv(sym, "4h", limit=40000, as_of_ms=now)
        report['full'] += 1
    return frames


def _save_alts(feed, names, store, now, frames, witness, key, report, out, started):
    """Compute + commit the alt index from fully prepared member frames."""
    from .alt_index_state import CACHE
    from . import market_provenance as mp
    try:
        last_before = store.last_ts("alts")
    except Exception:
        last_before = None
    plan = CACHE.prepare(key, frames, 5, store_last_ts=last_before, allow_skip=False)
    if plan.kind == 'unchanged':
        out["alts"] = 0
    else:
        plan.frame.attrs['request_started_ms'] = started
        saved_now = store._clock_ms()
        out["alts"] = store.save("alts", plan.frame, now_ms=saved_now)
        try:
            last_after = store.last_ts("alts")
        except Exception:
            last_after = None
        CACHE.commit(key, plan, out["alts"], saved_now, last_after)
        after = mp.witness(store.db, 'reference:alts')
        if witness is not None and after is not None and out["alts"] >= int(plan.frame['close'].notna().sum()):
            # Members are the PRE-load witnesses: a commit during the load
            # leaves the record unequal to the next read, forcing a refresh.
            _ALT_SAVED[key] = dict(members=witness['members'], output=after,
                                   next_ready={s: feed.pending_ready(s, '4h', now) for s in names})
    out["alts_reuse"] = dict(plan.report(), members=report)


def _refresh_alts(feed, members, store, now, out, *, allow_cold=False):
    """Current reference refresh of the equal-weight alt index. Never raises.

    Unchanged detection happens BEFORE any history is loaded. A changed member
    is refreshed from its committed change metadata (tail only, exact for pure
    insertions); an edit/deletion, an unproven log range or a crossed
    future-availability point reads that member's full window. A COLD start
    (no committed state) is history work: it is deferred to ``rebuild_alts_cold``
    (budgeted, resumable, run by the maintenance job) unless ``allow_cold``.
    """
    from .alt_index_state import CACHE
    from . import market_provenance as mp
    try:
        started = store._clock_ms()
        names = tuple(dict.fromkeys(members))
        key = (getattr(store, '_db_path', None) or id(store), names, 5)
        witness = _alts_witness(feed, store, names)
        saved = _ALT_SAVED.get(key)
        pending_now = saved is not None and any(v is not None and now >= v for v in saved['next_ready'].values())
        if (witness is not None and witness['output'] is not None and saved is not None and not pending_now
                and saved['members'] == witness['members'] and saved['output'] == witness['output']):
            out["alts"] = 0
            out["alts_reuse"] = dict(kind='unchanged', reason='witness_unchanged_before_load', rows=0, recomputed=0)
            return
        with CACHE._lock:
            entry = CACHE._entries.get(key)
        entry = entry if entry is not None and entry.committed else None
        if entry is None and not allow_cold:
            out["alts"] = 0
            out["alts_reuse"] = dict(kind='deferred', reason='cold_rebuild_is_history_maintenance', rows=0, recomputed=0)
            return
        report = dict(reused=0, tail=0, tail_rows=0, full=0)
        frames = _member_frames(feed, names, now, entry, saved, witness, report)
        _save_alts(feed, names, store, now, frames, witness, key, report, out, started)
    except Exception as e:
        out["alts"] = f"error: {e}"
        log.warning(f"reference alts: {e}")


_COLD: dict = {}


def alts_cold_pending(store, members):
    from .alt_index_state import CACHE
    names = tuple(dict.fromkeys(members))
    key = (getattr(store, '_db_path', None) or id(store), names, 5)
    with CACHE._lock:
        entry = CACHE._entries.get(key)
    return bool(names) and not (entry is not None and entry.committed)


def rebuild_alts_cold(feed, members, store, now, *, members_per_slice=8, yield_hook=None):
    """Budgeted, resumable cold rebuild of the alt index (history maintenance).

    Loads at most ``members_per_slice`` members' full windows per call (yielding
    at every member boundary) and keeps them as slim frames between calls; the
    index is computed and committed once every member is in. Independent of the
    current reference refresh and of any scan, so neither can wait on the other.
    Returns a small status dict.
    """
    from . import market_provenance as mp
    names = tuple(dict.fromkeys(members))
    key = (getattr(store, '_db_path', None) or id(store), names, 5)
    if not alts_cold_pending(store, members):
        return dict(state='not_needed')
    state = _COLD.setdefault(key, dict(frames={}, witness=_alts_witness(feed, store, names), started=store._clock_ms()))
    loaded = 0
    for sym in names:
        if sym in state['frames']:
            continue
        if loaded >= members_per_slice:
            return dict(state='in_progress', loaded=len(state['frames']), total=len(names))
        if yield_hook is not None:
            yield_hook()
        frame = feed.cached_ohlcv(sym, "4h", limit=40000, as_of_ms=now)
        if frame is not None and len(frame) >= 2 and all(k in frame for k in mp.META):
            state['frames'][sym] = _slim_frame(pd.to_datetime(frame['ts'], utc=True), frame['close'],
                                               frame['revision_id'])
        else:
            state['frames'][sym] = frame
        loaded += 1
    out, report = {}, dict(reused=0, tail=0, tail_rows=0, full=len(names))
    try:
        _save_alts(feed, names, store, now, state['frames'], state['witness'], key, report, out, state['started'])
    finally:
        _COLD.pop(key, None)
    return dict(state='committed', rows=out.get('alts'))


#: last witnesses recorded after a fully committed alts save (process memory;
#: a restart can only mean one rebuild, never a stale skip)
_ALT_SAVED: dict = {}


def refresh_all(feed, members: list, store=None, get=get_json,
                now_ms: int | None = None,
                cg_every_ms: int | None = None, allow_cold_alts: bool = False,
                current_window_ms: int | None = None) -> dict:
    """Bring every reference current. Each source is isolated: its failure
    is recorded as "error: …" and costs nothing else. Never raises."""
    from .references import HOUR, REFS, RefStore
    store = store or RefStore()
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    cg_every = 4 * HOUR if cg_every_ms is None else int(cg_every_ms)
    out: dict = {}

    def run(key, fn):
        try:
            started=store._clock_ms()
            frame=fn()
            if current_window_ms is not None and frame is not None and len(frame):
                # Current refresh observes new bars and a correction overlap.
                # Historical retrieval retains the original default path.
                last = store.last_ts(key)
                period = TF_MS.get(REFS[key].tf, HOUR)
                floor = now - current_window_ms
                if last is not None:
                    floor = max(floor, last - 2 * period)
                from .market_provenance import ms
                frame = frame.loc[ms(frame['ts']) >= floor].copy()
            frame.attrs['request_started_ms']=started
            out[key] = store.save(key, frame, now_ms=store._clock_ms())
        except Exception as e:
            out[key] = f"error: {e}"
            log.warning(f"reference {key}: {e}")

    for key, ref in REFS.items():
        if ref.source == "yahoo":
            first = store.last_ts(key) is None
            if ref.tf == "1h":
                interval, rng = "1h", ("730d" if first else "5d")
            else:
                interval, rng = "1d", ("10y" if first else "1mo")
            run(key, lambda t=ref.symbol, i=interval, g=rng:
                yahoo_frame(t, i, g, get))
        elif ref.source == "binance":
            last = store.last_ts(key)
            since = BTCDOM_FROM_MS if last is None \
                else last - 4 * TF_MS[ref.tf]      # re-read the tail
            run(key, lambda s=ref.symbol, tf=ref.tf, a=since:
                binance_klines(feed.ex.fetch_ohlcv, s, tf, a, now))
        elif ref.source == "defillama":
            run(key, lambda: defillama_stables(get))

    _refresh_alts(feed, members, store, now, out, allow_cold=allow_cold_alts)

    last = store.last_ts("cg_btc_d")
    if last is None or now - last >= cg_every:
        try:
            started=store._clock_ms()
            snap = coingecko_global(get)
            received = store._clock_ms()
            ts = pd.to_datetime([received], unit="ms", utc=True)
            for k, v in snap.items():
                frame = pd.DataFrame({"ts": ts, "close": [v]})
                frame.attrs.update(source=COINGECKO_GLOBAL,
                                   request_started_ms=started,
                                   raw_source_record={'response':snap.raw_source_record,
                                     'event_time_basis':'local_capture', 'field':k,
                                     'derivation':'coingecko_global.v1'})
                out[k] = store.save(k, frame, now_ms=received)
        except Exception as e:
            out["coingecko"] = f"error: {e}"
            log.warning(f"reference coingecko: {e}")
    return out
