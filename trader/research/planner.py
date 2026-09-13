"""What to evaluate next.

The order is forced by what each step needs from the one before:

1. MEASURE   a threshold cannot be guessed, and a gauge that is NaN over the
             slice is not a gauge. Nothing can be generated before this.
2. CONTROL   a result in a restricted window cannot be LABELLED before the
             known-good rule has been run in that same window. Without it a
             negative reads as "no edge" when it may be "no power".
3. SINGLES   every part alone, both directions, both geometries.
4. ABLATION  a k-part combination is not a survivor until every one of its
             one-part ablations has been scored.
5. GROW      extend only parents that carried information, beam-limited.
6. SEEDED    the same, around the book's own strategies.

Held-out results never enter this file. If a held-out failure steered the
next round, the held-out set would silently become part of the search.

TWO RULES ON CONTROL GATING (found by review, both load-bearing):

- Control gating is PER WINDOW, not per horizon. Singles in an already
  controlled window proceed while another window still awaits its control;
  gating globally would stall every horizon behind its slowest window.
- EVERY batch about to be returned has its windows controlled first —
  growth, ablation and seeded combinations as much as singles. A grown rule
  can span two restricted windows (`funding` + `basis` -> the composite
  window `basis|funding`) that no single alone ever requests a control for,
  and this module is the ONLY gatekeeper: evaluate.py and growth.py never
  look at a control. `_gate()` is the one place this runs, called from
  every round, so a rule reaching a verdict with no baseline is not
  possible by construction rather than by each round remembering to check.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from . import control, vocab
from .combo import Combination, compatible, subsets

log = logging.getLogger(__name__)


@dataclass
class Batch:
    kind: str                       # measure | evaluate | idle
    tf: str
    geo: str = ""
    round: str = ""
    combos: list = field(default_factory=list)
    reason: str = ""


def _rcfg(cfg: dict) -> dict:
    return cfg.get("research", {}) or {}


def seed_parts(journal) -> list:
    """The book's own strategies, as parts to grow context around.

    "Donchian + X, judged with and without" is the one search the book can
    run against a mechanism it already believes in.
    """
    out = []
    if journal is None:
        return out
    try:
        rows = journal.query(
            "SELECT id, spec_json FROM strategies "
            "WHERE state IN ('paper','active') AND kind='spec'")
    except Exception as e:                              # noqa: BLE001
        log.debug(f"seed parts unavailable: {e}")
        return out
    for r in rows:
        try:
            s = json.loads(r["spec_json"] or "{}")
        except Exception:                               # noqa: BLE001
            continue
        lo, sh = (s.get("entry_long") or "").strip(), \
                 (s.get("entry_short") or "").strip()
        if not lo or not sh:
            continue
        key = f"seed:{r['id']}"
        out.append(vocab.Part(key=key, kind="event", gauge=key,
                              long=lo, short=sh))
    return out


def _parts(led, tf: str) -> list:
    return vocab.parts_for(tf, led.gauges(tf))


def _windows_needing_control(led, tf: str, combos) -> list:
    missing, seen = [], set()
    for c in combos:
        w = c.window
        if w in seen:
            continue
        seen.add(w)
        if led.control(tf, w) is None:
            ctrl = control.control_combo(tf, w)
            if ctrl is not None:
                missing.append(ctrl)
            else:
                # cannot be calibrated; record that rather than pretend
                led.record_control(tf, w, {"verdict": "uncalibrated",
                                           "consistency_p": None}, False,
                                  status="uncalibrated")
    return missing


def _take(combos, led, tf, geo, n) -> list:
    known = led.known(tf, geo)
    out, seen = [], set()
    for c in combos:
        h = c.hash
        if h in known or h in seen:
            continue
        seen.add(h)
        out.append(c)
        if len(out) >= n:
            break
    return out


def _pending(combos, led, tf, geo) -> list:
    """`combos` that have never been scored, deduplicated by hash.

    A combination already in the ledger is a look already spent — under
    any provenance, singles included — so it neither needs a fresh
    evaluation nor a window control raised on its account.
    """
    known = led.known(tf, geo)
    out, seen = [], set()
    for c in combos:
        if c.hash in known or c.hash in seen:
            continue
        seen.add(c.hash)
        out.append(c)
    return out


def _ready(combos, led, tf) -> list:
    """`combos` whose window already has a control on record.

    A window's own combinations wait on ITS control, never on every other
    window's — the funding family being uncalibrated must not stall the
    plain OHLCV rules that are ready right now.
    """
    return [c for c in combos if led.control(tf, c.window) is not None]


def _gate(combos, led, tf, geo, size) -> tuple:
    """Route one round's candidates: what is ready to evaluate now, or the
    controls its still-pending combos need first.

    Applies to every round, not only singles — a grown rule, an ablation
    subset or a seeded pairing can land in a window (or a composite of two,
    e.g. `basis|funding`) that no single alone ever asked a control for.
    This function is the single place that check runs, so calling it from
    every round is what makes "no returned batch ever contains an
    uncontrolled window" true rather than aspirational.

    Per-window, not per-horizon: a combo whose own window already has a
    control goes out immediately even while a DIFFERENT window among the
    same candidates is still uncalibrated. Returns (ready, controls_needed);
    at most one is non-empty.
    """
    pending = _pending(combos, led, tf, geo)
    if not pending:
        return [], []
    ready = _take(_ready(pending, led, tf), led, tf, geo, size)
    if ready:
        return ready, []
    return [], _take(_windows_needing_control(led, tf, pending), led, tf,
                     geo, size)


def needs_ablation(led, tf: str, geo: str, parts_by_key: dict) -> list:
    """Subsets of grown children that have not been scored yet."""
    out = []
    for row in led.rows(tf, geo, verdict="grow"):
        if int(row["k"]) < 2:
            continue
        try:
            keys = json.loads(row["parts"] or "[]")
        except Exception:                               # noqa: BLE001
            continue
        parts = [parts_by_key[k] for k in keys if k in parts_by_key]
        if len(parts) != len(keys):
            continue                    # vocabulary changed under the row
        c = Combination(tuple(parts), tf, geo)
        out.extend(s for s in subsets(c) if not led.has(s.hash))
    return out


def _grow_children(led, tf, geo, parts_by_key: dict, beam, max_parts) -> list:
    """Extend the best parents at the deepest level that has any.

    `parts_by_key` must be the UNION of the vocabulary and the seeded parts
    (see `next_batch`): a seeded pairing is keyed `seed:<strategy id>`, which
    exists nowhere in `vocab.parts_for`, and a lookup that only checks the
    vocabulary silently drops every row it cannot reconstruct — so a seeded
    parent that grew could never itself be extended.
    """
    all_parts = list(parts_by_key.values())
    for k in range(1, int(max_parts)):
        parents = [r for r in led.rows(tf, geo, k=k, verdict="grow")]
        if not parents:
            continue
        # ablations first: a level is not finished until its children know
        # which of their parts earn a place
        out = []
        for row in parents[:int(beam)]:
            keys = json.loads(row["parts"] or "[]")
            base = [parts_by_key[x] for x in keys if x in parts_by_key]
            if len(base) != len(keys):
                continue                    # vocabulary changed under the row
            for p in all_parts:
                cand = tuple(base) + (p,)
                if not compatible(cand):
                    continue
                out.append(Combination(cand, tf, geo, trigger=keys[0],
                                       round="grow", parent=row["hash"]))
        if out:
            return out
    return []


def next_batch(led, cfg: dict, journal=None) -> Batch:
    r = _rcfg(cfg)
    horizons = list(r.get("horizons") or [])
    geos = list(r.get("geometries") or ["trail", "fixed"])
    size = int(r.get("batch_combos", 40))
    beam = int(r.get("beam", 12))
    max_parts = int(r.get("max_parts", 6))

    for tf in horizons:
        gauges = led.gauges(tf)
        if not gauges:
            return Batch("measure", tf, reason="no thresholds measured yet")
        parts = _parts(led, tf)
        if not parts:
            continue
        seeds = seed_parts(journal)
        # ONE key->Part map, unioning the vocabulary with the book's own
        # seeded parts, handed to every reconstructor below. A part keyed
        # `seed:<id>` exists nowhere in `vocab.parts_for`, so a map built
        # from the vocabulary alone silently drops any row it cannot look
        # up — the seeded round could grow but never be ablated or extended.
        by_key = {p.key: p for p in list(parts) + list(seeds)}
        for geo in geos:
            singles = [Combination((p,), tf, geo, trigger=p.key,
                                   round="singles") for p in parts]
            take, ctrl = _gate(singles, led, tf, geo, size)
            if take:
                return Batch("evaluate", tf, geo, "singles", take,
                             "every condition alone, both directions")
            if ctrl:
                return Batch("evaluate", tf, geo, "control", ctrl,
                             "a window cannot be read without its control")

            abl = needs_ablation(led, tf, geo, by_key)
            take, ctrl = _gate(abl, led, tf, geo, size)
            if take:
                return Batch("evaluate", tf, geo, "ablation", take,
                             "a part is not kept until its removal is priced")
            if ctrl:
                return Batch("evaluate", tf, geo, "control", ctrl,
                             "a window cannot be read without its control")

            if max_parts > 1:
                grown = _grow_children(led, tf, geo, by_key, beam, max_parts)
                take, ctrl = _gate(grown, led, tf, geo, size)
                if take:
                    return Batch("evaluate", tf, geo, "grow", take,
                                 "extend what carried information")
                if ctrl:
                    return Batch("evaluate", tf, geo, "control", ctrl,
                                 "a window cannot be read without its control")

            if seeds and max_parts > 1:
                seeded = []
                for s in seeds:
                    for p in parts:
                        cand = (s, p)
                        if compatible(cand):
                            seeded.append(Combination(
                                cand, tf, geo, trigger=s.key,
                                round="seeded"))
                take, ctrl = _gate(seeded, led, tf, geo, size)
                if take:
                    return Batch("evaluate", tf, geo, "seeded", take,
                                 "context around what the book already trades")
                if ctrl:
                    return Batch("evaluate", tf, geo, "control", ctrl,
                                 "a window cannot be read without its control")
    return Batch("idle", horizons[0] if horizons else "",
                 reason="nothing left to evaluate at this depth")
