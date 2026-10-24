"""RF01: dependency-aware reuse of the computed alt index (fixtures only).

Every incremental result is compared bit-for-bit with `alts_index`, the
definition, including its raw-source ancestry record.
"""
import types

import numpy as np
import pandas as pd
import pytest

from trader.data import alt_index_state as AS
from trader.data import market_provenance as mp
from trader.data import ref_sources as S
from trader.data.references import HOUR, RefStore

STEP = 4 * HOUR
NOW = 10 ** 13


@pytest.fixture(autouse=True)
def _fresh_cache():
    AS.CACHE.invalidate()
    yield
    AS.CACHE.invalidate()


def walk(n, seed, start=0):
    rng = np.random.default_rng(seed)
    px = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, n))
    return pd.DataFrame({"ts": pd.to_datetime([(start + i) * STEP for i in range(n)], unit="ms", utc=True),
                         "close": px})


def members(count=12, bars=300, seed=7):
    return {"M%02d" % i: walk(bars, seed + i) for i in range(count)}


def grow(base, extra):
    """`extra` new bars per member, deterministic and member-specific."""
    out = {}
    for i, (sym, d) in enumerate(base.items()):
        n, last = len(d), d["close"].iloc[-1]
        new = pd.DataFrame({"ts": pd.to_datetime([(n + k) * STEP for k in range(extra)], unit="ms", utc=True),
                            "close": [last * (1 + 0.002 * (i + 1)) ** (k + 1) for k in range(extra)]})
        out[sym] = pd.concat([d, new], ignore_index=True)
    return out


def qualify(frames, tag="a"):
    """Receipt-qualified copies: all provenance columns, deterministic revision IDs."""
    out = {}
    for sym, df in frames.items():
        d = df.copy()
        for k in mp.META:
            d[k] = None
        d["revision_id"] = ["%s-%s-%d" % (tag, sym, i) for i in range(len(d))]
        out[sym] = d
    return out


def same(plan_frame, frames, min_members=5):
    want = S.alts_index(frames, min_members)
    pd.testing.assert_frame_equal(plan_frame, want, check_exact=True)
    assert plan_frame.attrs == want.attrs


KEY = ("db", ("M",), 5)


def tick(frames, store_last=None, commit=True, min_members=5, key=KEY):
    plan = AS.CACHE.prepare(key, frames, min_members, store_last_ts=store_last)
    if commit and plan.frame is not None:
        AS.CACHE.commit(key, plan, plan.rows, NOW, 123)
    return plan


class Count:
    """Counts numeric rebuilds (full and suffix) behind the helpers."""

    def __init__(self, monkeypatch):
        self.full = self.suffix = 0
        full, suffix = S._full_numeric, S._suffix_numeric

        def f(*a, **k):
            self.full += 1
            return full(*a, **k)

        def s(*a, **k):
            self.suffix += 1
            return suffix(*a, **k)
        monkeypatch.setattr(S, "_full_numeric", f)
        monkeypatch.setattr(S, "_suffix_numeric", s)


# ── parity ──────────────────────────────────────────────────────────────
def test_cold_tick_is_the_definition():
    frames = qualify(members())
    plan = tick(frames)
    assert plan.kind == "full" and plan.reason == "cold"
    same(plan.frame, frames)


@pytest.mark.parametrize("count", [5, 9, 12, 30])
def test_appended_bars_recompute_only_the_new_rows_and_match_exactly(count):
    base = members(count, 300)
    tick(qualify(base))
    for extra in (1, 2, 7):                          # successive ticks keep committing
        base = grow(base, extra)
        frames = qualify(base, "t%d" % extra)
        plan = tick(frames)
        assert plan.kind == "suffix" and plan.recomputed == extra
        same(plan.frame, frames)


def test_old_close_correction_without_a_new_timestamp_recomputes_from_that_row():
    base = members(12, 300)
    tick(qualify(base))
    tau = 120
    fixed = {s: d.copy() for s, d in base.items()}
    fixed["M03"].loc[tau, "close"] *= 1.07           # correction to history; same timestamps
    frames = qualify(fixed, "b")
    plan = tick(frames)
    assert plan.kind == "suffix" and plan.reason == "changed_from_row_%d" % tau
    assert plan.recomputed == 300 - tau              # row tau, tau+1 (its predecessor) and the cumulative suffix
    same(plan.frame, frames)
    # rows strictly before the corrected event (by timestamp; _finish drops leading NaN rows) are untouched
    old = S.alts_index(qualify(base)).set_index("ts")["close"]
    new = plan.frame.set_index("ts")["close"]
    cut = base["M03"]["ts"].iloc[tau]
    before_old, before_new = old[old.index < cut], new[new.index < cut]
    assert len(before_old) and before_old.index.equals(before_new.index)
    assert np.array_equal(before_old.to_numpy(), before_new.to_numpy(), equal_nan=True)
    assert not np.allclose(old[old.index >= cut].to_numpy(), new[new.index >= cut].to_numpy(), equal_nan=True)


def test_member_gap_and_its_repair_match_the_definition():
    base = members(12, 300)
    tick(qualify(base))
    gapped = dict(base)
    gapped["M05"] = base["M05"].drop(index=[150, 151]).reset_index(drop=True)   # missing bars: no return, not filled
    plan = tick(qualify(gapped, "g"))
    assert plan.kind == "suffix" and plan.recomputed == 300 - 150
    same(plan.frame, qualify(gapped, "g"))
    plan = tick(qualify(base, "h"))                  # the gap is later repaired
    assert plan.kind == "suffix"
    same(plan.frame, qualify(base, "h"))


def test_insufficient_min_members_stays_nan_through_the_incremental_path():
    base = members(6, 200)
    tick(qualify(base))
    thin = dict(base)
    for sym in ("M00", "M01"):                       # only 4 members measured over this stretch
        thin[sym] = base[sym].drop(index=range(100, 140)).reset_index(drop=True)
    frames = qualify(thin, "m")
    plan = tick(frames)
    assert plan.kind == "suffix"
    out = plan.frame
    assert len(out) < 200 and out["close"].notna().all()      # NaN bars are dropped, never levels
    same(out, frames)


def test_membership_change_and_min_members_change_recompute_in_full():
    base = members(12, 200)
    tick(qualify(base))
    fewer = {s: d for s, d in base.items() if s != "M04"}
    plan = tick(qualify(fewer, "x"))
    assert (plan.kind, plan.reason) == ("full", "membership_changed")
    same(plan.frame, qualify(fewer, "x"))
    plan = tick(qualify(fewer, "x"), min_members=8)
    assert (plan.kind, plan.reason) == ("full", "membership_changed")
    same(plan.frame, qualify(fewer, "x"), 8)


def test_moving_origin_rebases_every_level_so_it_is_a_full_recompute():
    full = members(12, 400)
    first = {s: d.iloc[:300].reset_index(drop=True) for s, d in full.items()}
    rolled = {s: d.iloc[40:340].reset_index(drop=True) for s, d in full.items()}    # window rolled over
    tick(qualify(first))
    plan = tick(qualify(rolled, "r"))
    assert (plan.kind, plan.reason) == ("full", "origin_changed")
    same(plan.frame, qualify(rolled, "r"))
    # the rebase is real: a pure suffix reuse of the old levels would be wrong
    old = S.alts_index(qualify(first)).set_index("ts")["close"]
    new = plan.frame.set_index("ts")["close"]
    common = old.index.intersection(new.index)
    assert not np.allclose(old[common], new[common])


def test_uncommitted_state_is_never_reused():
    base = members(12, 200)
    plan = tick(qualify(base), commit=False)          # the save "failed": nothing installed
    assert plan.kind == "full"
    plan = tick(qualify(base))
    assert (plan.kind, plan.reason) == ("full", "cold")


# ── unchanged inputs / new ancestry ─────────────────────────────────────
def test_unchanged_inputs_do_no_numeric_rebuild_and_ask_for_no_save(monkeypatch):
    count = Count(monkeypatch)
    frames = qualify(members(12, 300))
    tick(frames)
    assert (count.full, count.suffix) == (1, 0)
    for _ in range(4):                                # four hourly ticks, no new 4h bar
        plan = tick(frames, store_last=123)
        assert plan.kind == "unchanged" and plan.frame is None
    assert (count.full, count.suffix) == (1, 0)


def test_new_ancestry_with_identical_numbers_updates_the_evidence_without_numeric_rebuild(monkeypatch):
    count = Count(monkeypatch)
    base = members(12, 300)
    first = qualify(base, "a")
    tick(first)
    again = qualify(base, "b")                        # same closes, newly fetched revisions
    plan = tick(again, store_last=123)
    assert plan.kind == "ancestry" and plan.frame is not None
    assert (count.full, count.suffix) == (1, 0)
    same(plan.frame, again)
    assert plan.frame.attrs["raw_source_record"]["constituents"]["M00"][0] == "b-M00-0"


def test_one_member_revision_change_is_enough_to_save_again():
    base = members(12, 300)
    frames = qualify(base, "a")
    tick(frames)
    changed = {s: d.copy() for s, d in frames.items()}
    changed["M07"]["revision_id"] = changed["M07"]["revision_id"].str.replace("a-", "z-", regex=False)
    plan = tick(changed, store_last=123)
    assert plan.kind == "ancestry"
    same(plan.frame, changed)


def test_unchanged_requires_the_store_to_still_end_where_the_output_did():
    frames = qualify(members(12, 300))
    tick(frames)
    assert tick(frames, store_last=999).kind == "ancestry"       # store moved/was replaced: re-save
    assert tick(frames, store_last=None).kind == "ancestry"      # store cannot say: re-save


# ── through refresh_all with a real RefStore ────────────────────────────
class Venue:
    def fetch_ohlcv(self, *a, **k):
        return []


def feed_of(frames_holder):
    def cached(symbol, timeframe, limit=None, *, as_of_ms):
        assert type(as_of_ms) is int
        return frames_holder["frames"].get(symbol)
    return types.SimpleNamespace(ex=Venue(), cached_ohlcv=cached)


def get(url, params=None):
    if "yahoo" in url:
        raise ConnectionError("down")
    if "llama" in url:
        raise ConnectionError("down")
    if "coingecko" in url:
        raise ConnectionError("down")
    raise AssertionError(url)


def test_refresh_all_skips_rebuild_and_save_for_unchanged_inputs_and_saves_new_ancestry(tmp_path, monkeypatch):
    count = Count(monkeypatch)
    store = RefStore(tmp_path / "c.db")
    saves = []
    real = store.save
    monkeypatch.setattr(store, "save", lambda key, df, now_ms=None: (saves.append(key), real(key, df, now_ms=now_ms))[1])
    holder = {"frames": qualify(members(12, 120), "a")}
    feed, names = feed_of(holder), list(holder["frames"])
    first = S.refresh_all(feed, names, store, get, now_ms=NOW)
    assert first["alts"] > 0 and first["alts_reuse"]["kind"] == "full"
    n_rev = store.db.execute("SELECT COUNT(*) FROM market_revisions WHERE series_key='reference:alts'").fetchone()[0]
    for i in range(4):                                # same inputs: numbers reused, evidence still persisted
        out = S.refresh_all(feed, names, store, get, now_ms=NOW)
        assert out["alts"] > 0 and out["alts_reuse"]["kind"] == "ancestry" and out["alts_reuse"]["recomputed"] == 0
    assert saves.count("alts") == 5 and (count.full, count.suffix) == (1, 0)
    assert store.db.execute("SELECT COUNT(*) FROM market_revisions WHERE series_key='reference:alts'").fetchone()[0] > n_rev
    holder["frames"] = qualify(members(12, 120), "b")                 # newly acquired revisions, same closes
    out = S.refresh_all(feed, names, store, get, now_ms=NOW)
    assert out["alts_reuse"]["kind"] == "ancestry" and out["alts"] > 0 and saves.count("alts") == 6
    assert (count.full, count.suffix) == (1, 0)
    held = store.load("alts")
    assert held is not None and set(held["source"]) == {"derived:equal_weight_alt_index.v1"}
    assert store.load("tv_total") is None and store.load("tv_total2") is None    # published identities untouched


def test_failed_save_never_installs_state_and_a_restart_recomputes_the_same_output(tmp_path, monkeypatch):
    store = RefStore(tmp_path / "c.db")
    base = members(12, 120)
    holder = {"frames": qualify(base, "a")}
    feed, names = feed_of(holder), list(base)
    S.refresh_all(feed, names, store, get, now_ms=NOW)
    # new bar appears, but the save fails
    grown = grow(base, 2)
    holder["frames"] = qualify(grown, "c")
    real = store.save
    monkeypatch.setattr(store, "save", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")) if a[0] == "alts" else real(*a, **k))
    out = S.refresh_all(feed, names, store, get, now_ms=NOW)
    assert str(out["alts"]).startswith("error:")
    monkeypatch.setattr(store, "save", real)
    out = S.refresh_all(feed, names, store, get, now_ms=NOW)          # state still the OLD committed one
    assert out["alts_reuse"]["kind"] == "suffix"
    incremental = store.db.execute("SELECT ts,close FROM refs WHERE key='alts' ORDER BY ts").fetchall()
    AS.CACHE.invalidate()                                              # "restart"
    store2 = RefStore(tmp_path / "c2.db")
    out2 = S.refresh_all(feed, names, store2, get, now_ms=NOW)
    assert out2["alts_reuse"]["kind"] == "full"
    assert store2.db.execute("SELECT ts,close FROM refs WHERE key='alts' ORDER BY ts").fetchall() == incremental


def test_a_feed_member_raising_isolates_alts_only(tmp_path):
    store = RefStore(tmp_path / "c.db")

    def boom(*a, **k):
        raise RuntimeError("cache unreadable")
    feed = types.SimpleNamespace(ex=Venue(), cached_ohlcv=boom)
    out = S.refresh_all(feed, ["A", "B"], store, get, now_ms=NOW)
    assert str(out["alts"]).startswith("error: ")


def test_refresh_never_skips_save_even_when_last_ts_is_unchanged_after_external_replacement(tmp_path):
    store = RefStore(tmp_path / "c.db")
    holder = {"frames": qualify(members(12, 120), "a")}
    feed, names = feed_of(holder), list(holder["frames"])
    S.refresh_all(feed, names, store, get, now_ms=NOW)
    last = store.last_ts("alts")
    with store.db:                                     # external correction: same last ts, different history
        store.db.execute("UPDATE refs SET close=close*2 WHERE key='alts' AND ts<?", (last,))
    assert store.last_ts("alts") == last
    out = S.refresh_all(feed, names, store, get, now_ms=NOW)
    assert out["alts_reuse"]["kind"] == "ancestry" and out["alts"] > 0
    want = S.alts_index(holder["frames"]).set_index("ts")["close"]
    got = dict(store.db.execute("SELECT ts,close FROM refs WHERE key='alts'").fetchall())
    assert all(abs(got[int(t.timestamp() * 1000)] - v) < 1e-9 for t, v in want.items())   # repaired by the re-save


def test_pure_cache_skip_remains_available_only_when_the_caller_allows_it():
    frames = qualify(members(12, 120))
    tick(frames)
    assert tick(frames, store_last=123).kind == "unchanged"
    plan = AS.CACHE.prepare(KEY, frames, 5, store_last_ts=123, allow_skip=False)
    assert plan.kind == "ancestry" and plan.frame is not None and plan.recomputed == 0
