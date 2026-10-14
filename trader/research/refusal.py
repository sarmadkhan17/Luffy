"""Remember — in this process only — that a bundle was refused for a reason
that cannot change until the evidence does.

The planner re-offers the same batch every `interval_seconds` because a
refusal writes nothing the planner reads. When the child has proved that the
requested bundle holds too little replay-eligible history at this cut, the
next launch can only repeat that proof. This module lets the runner skip the
launch — and only that launch:

- it suppresses nothing that is transient, unknown, crashed or unclassified
  (the child must say `deterministic`, and the runner must agree);
- it never decides admission: whenever the evidence might have moved, or an
  hour has passed, the child is launched and is the only authority;
- it persists nothing. A restart forgets everything, which costs one probe.

`stamp()` is a CHANGE DETECTOR over the provenance table's own indexed
columns. It is not an eligibility test and never reads a raw candle count:
two equal stamps mean "nothing was appended or revised since the child
looked", two different ones mean "ask the child again".
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time

log = logging.getLogger(__name__)

#: bump when what a refusal means changes
CONTRACT = "rp07.refusal.v1"

#: a suppressed bundle is probed by a real child at least this often, stamp
#: or no stamp. Below 3600 s so one step of slack still lands inside the hour.
REPROBE_S = 3300.0

#: a suppressed bundle's stamp is re-read at most this often. This is the
#: detection latency for appended or revised evidence (the full 16-symbol
#: stamp measured 2 ms warm on the real store, ~100 ms warm / ~300 ms cold
#: at 11,000 revisions per series).
STAMP_CHECK_S = 300.0

#: refusal reasons that may ever be suppressed
SUPPRESSIBLE = ("insufficient_replay_history", "insufficient_symbol_coverage")

#: source files whose contents define what "admitted" means. A change to any
#: of them is a change of contract even when no constant was bumped.
_CODE = ("trader/research/evaluate.py", "trader/research/job.py",
         "trader/research/slices.py", "trader/data/market_provenance.py",
         "trader/data/feed.py", "trader/research/refusal.py")


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def code_identity() -> str:
    """Digest of the admission-defining sources as they are ON DISK now.

    The child imports them fresh on every spawn, so a working-tree change is
    the one thing the parent's own imported modules cannot show.
    """
    h = hashlib.sha256(CONTRACT.encode())
    for rel in _CODE:
        try:
            with open(os.path.join(_root(), rel), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"?" + rel.encode())
    return h.hexdigest()[:16]


def candle_path(cfg: dict) -> str:
    p = ((cfg.get("research") or {}).get("paths") or {}).get("candles")
    if p:
        return str(p)
    return os.path.join(_root(), "data", "candles.db")


def _file_identity(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [os.path.realpath(path), st.st_dev, st.st_ino]


def series_key(symbol: str, tf: str) -> str:
    from ..core.types import norm_symbol
    return "candle:" + norm_symbol(symbol) + ":" + tf


_STAMP_SQL = (
    "SELECT series_key, COUNT(*), MIN(event_ms), MAX(event_ms), "
    "MAX(observed_ms), MAX(available_ms), SUM(available_ms), "
    "SUM(observed_ms), MAX(rowid) FROM market_revisions "
    "WHERE series_key IN ({q}) GROUP BY series_key")


def stamp(path: str, tf: str, symbols) -> list | None:
    """Per requested series: (revisions, first/last event, newest receipt,
    availability sums, newest rowid) — read from the covering index only.

    Appended revisions move the counts and maxima; a revised receipt moves
    the sums; a replaced file moves the file identity in `identity()`.
    Returns None when the store cannot be read at all: an unreadable stamp
    can never confirm "unchanged", so the caller launches.
    """
    keys = sorted({series_key(s, tf) for s in symbols})
    if not keys:
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        sql = _STAMP_SQL.format(q=",".join("?" * len(keys)))
        got = {r[0]: list(r[1:]) for r in con.execute(sql, keys)}
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return [[k] + got.get(k, [0]) for k in keys]


def retrospective_stamp(path: str, tf: str, symbols) -> list | None:
    """Detect projection changes using inventory and file metadata only.

    Legacy projections can change without acquisition revisions. Price
    corrections move SQLite's file/WAL clock; appends also move inventory.
    This only invalidates a refusal and never decides data eligibility.
    """
    from pathlib import Path
    from ..core.types import norm_symbol
    want = sorted({norm_symbol(sym) for sym in symbols})
    if not want:
        return None

    def files():
        out = []
        for filename in (path, str(path) + "-wal"):
            try:
                st = os.stat(filename)
                out.append([filename, st.st_dev, st.st_ino, st.st_size,
                            st.st_mtime_ns, st.st_ctime_ns])
            except FileNotFoundError:
                out.append([filename, None])
        return out

    try:
        before = files()
        con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True,
                              timeout=2.0)
        try:
            tables = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            rows = con.execute(
                "SELECT symbol, COUNT(*), MIN(ts), MAX(ts), SUM(ts) FROM candles "
                "WHERE tf=? AND symbol IN (" + ",".join("?" for _ in want) +
                ") GROUP BY symbol", [tf, *want]).fetchall()
        finally:
            con.close()
        revisions = stamp(path, tf, symbols) if "market_revisions" in tables else []
        if revisions is None or before != files():
            return None
        inventory = {row[0]: list(row[1:]) for row in rows}
        return [before, [[sym, *inventory.get(sym, [0])] for sym in want], revisions]
    except (OSError, sqlite3.Error):
        return None


def identity(cfg: dict, tf: str, round_: str, floor_key: str, floor: int,
             symbols, cap_ms, cut_ms) -> str:
    """Everything a refusal was reached under, except the data stamp.

    A control and a discovery request never share one: the round class, the
    floor key and the symbol tuple are all in it.
    """
    path = candle_path(cfg)
    doc = {"contract": CONTRACT, "code": code_identity(), "tf": tf,
           "control": round_ == "control", "floor_key": floor_key,
           "floor": int(floor), "symbols": sorted(symbols),
           "cap_ms": None if cap_ms is None else int(cap_ms),
           "cut_ms": None if cut_ms is None else int(cut_ms),
           "source": _file_identity(path),
           "retrospective": (cfg.get("research") or {}).get("retrospective")}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()
                          ).hexdigest()


class Refusals:
    """Process-local `(slot) -> refusal` map with an injectable clock."""

    def __init__(self, clock=time.monotonic, reprobe_s: float = REPROBE_S,
                 stamp_check_s: float = STAMP_CHECK_S):
        self._clock = clock
        self.reprobe_s = float(reprobe_s)
        self.stamp_check_s = float(stamp_check_s)
        self._by_slot: dict = {}

    @staticmethod
    def slot(tf, round_, floor_key, symbols) -> tuple:
        return (tf, round_ == "control", floor_key, tuple(sorted(symbols)))

    def __len__(self):
        return len(self._by_slot)

    def clear(self, slot) -> None:
        self._by_slot.pop(slot, None)

    def record(self, slot, ident: str, st, adm: dict) -> bool:
        """Remember a CONFIRMED deterministic refusal. `st` must be the
        stamp read BEFORE the child launched: data appended while it ran
        then reads as a change instead of being absorbed."""
        if (not adm or adm.get("admitted") or not adm.get("deterministic")
                or adm.get("reason") not in SUPPRESSIBLE or st is None):
            self.clear(slot)
            return False
        now = self._clock()
        self._by_slot[slot] = {"identity": ident, "stamp": st,
                               "reason": adm["reason"],
                               "loaded": adm.get("loaded_symbols"),
                               "required": adm.get("required_symbols"),
                               "probed_at": now, "checked_at": now,
                               "suppressed": 0}
        return True

    def decide(self, slot, ident: str, read_stamp) -> dict | None:
        """The refusal to honour instead of launching, or None to launch.

        `read_stamp` is a thunk: it is called only when a re-read is due.
        Every doubt resolves to a launch.
        """
        e = self._by_slot.get(slot)
        if e is None:
            return None
        now = self._clock()
        if e["identity"] != ident:
            self.clear(slot)
            return None
        if now - e["probed_at"] >= self.reprobe_s:
            return None                      # entry stays until the probe
        if now - e["checked_at"] >= self.stamp_check_s:
            st = read_stamp()
            if st is None or st != e["stamp"]:
                self.clear(slot)
                return None
            e["checked_at"] = now
        e["suppressed"] += 1
        return e
