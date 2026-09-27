"""RiskManager — the ONLY hard-gate component in Luffy (REQUIREMENTS §10).

Everything else votes or suggests; this enforces capital survival rules:
- portfolio heat cap (total open risk ≤ 15%)
- per-symbol risk cap (≤ 8%)
- max open positions (10)
- daily loss breaker (−6% blocks NEW entries until UTC midnight)
- staged de-risk on rolling drawdown (×0.5 @ −8%, ×0.25 @ −12%)
- full halt at −20% (raises → kernel flips ControlState.HALTED)
- proving period: first N live trades at ×0.5 size

Position sizing = risk-based: notional sized so that stop-distance loss
equals allowed risk, then clamped by caps and min notional.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ..core.types import ControlState, Position, RiskError

log = logging.getLogger(__name__)


@dataclass
class SizingResult:
    ok: bool
    reason: str
    size_usdt: float          # margin/notional to deploy
    amount: float             # base units
    risk_usdt: float
    size_mult: float          # de-risk/proving multiplier applied


@dataclass(frozen=True)
class RiskRelease:
    """Risk's answer to "may the system be ACTIVE now?" (fail closed).

    reason: risk_release_ok | risk_halt_active | risk_equity_unreadable |
            risk_equity_not_authoritative | risk_state_unreadable |
            risk_state_corrupt (a baseline existed and is now unusable, latched) |
            risk_baseline_uninitialized (first run, before the first baseline)

    A release is a proof bound to the exact Risk state it was computed from:
    the issuing RiskManager (`identity`), its in-memory `generation` (bumped
    by every re-evaluation, latch and repair) and the SHA-256 of the durable
    `risk_state` blob. `verify` (the issuer's hold_release) re-checks that
    identity under the Risk lock and keeps the lock for the final ACTIVE
    compare-and-set, so nothing can change Risk between the check and the CAS.
    """
    allowed: bool
    reason: str
    equity: float | None = None
    peak_equity: float | None = None
    drawdown_pct: float | None = None
    halt_drawdown_pct: float | None = None
    authoritative: bool = False
    generation: int | None = None
    identity: str | None = None
    state_digest: str | None = None
    read_at: float | None = None          # time.monotonic() of the check
    verify: Callable | None = field(default=None, compare=False, repr=False)
    #: set whenever the baseline itself verified (allowed or halted): re-checks
    #: that the durable baseline is still that exact, unlatched one.
    verify_baseline: Callable | None = field(default=None, compare=False, repr=False)

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason,
                "equity": self.equity, "peak_equity": self.peak_equity,
                "drawdown_pct": self.drawdown_pct,
                "halt_drawdown_pct": self.halt_drawdown_pct,
                "authoritative": self.authoritative,
                "generation": self.generation, "identity": self.identity,
                "state_digest": self.state_digest}


def _digest(raw) -> str:
    return hashlib.sha256(("" if raw is None else str(raw)).encode()).hexdigest()


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class RiskManager:
    def __init__(self, cfg: dict, journal):
        r = cfg["risk"]
        self.risk_pct = float(r["risk_per_trade_pct"]) / 100.0
        self.proving_trades = int(r.get("proving_period_trades", 30))
        self.proving_mult = float(r.get("proving_size_mult", 0.5))
        self.heat_cap = float(r["portfolio_heat_cap_pct"]) / 100.0
        self.symbol_cap = float(r["per_symbol_risk_cap_pct"]) / 100.0
        self.max_positions = int(r["max_open_positions"])
        self.daily_loss_block = float(r["max_daily_loss_pct"]) / 100.0
        self.derisk_steps = sorted(
            ((float(s["dd_pct"]), float(s["size_mult"]))
             for s in r.get("drawdown_derisk_steps", [])))
        self.halt_dd = float(r["halt_drawdown_pct"]) / 100.0
        self.leverage = int(r["leverage"])
        self.sl_atr_mult = float(r["stop_loss_atr_mult"])
        self.min_notional = float(r["min_notional_usdt"])
        # A stop bounds the loss only when it FILLS; a gap through it loses
        # notional, not the risk budget. And `amount = allowed / stop_dist`
        # bounds distance-to-stop and nothing else, so the 0.4% stop floor
        # turned a $23 budget into $5,750 of notional — a quarter of the
        # account in one position, eight of which need margin that does not
        # exist. Survivable at a 4-position cap; reachable at 8.
        self.max_pos_margin = float(r.get("max_position_margin_pct", 20.0)) / 100.0
        self.max_total_margin = float(r.get("max_total_margin_pct", 70.0)) / 100.0
        self.taker_fee = float(r.get("taker_fee_pct", 0.05)) / 100.0

        self.journal = journal
        self._day_key: str | None = None
        self._day_start_equity: float | None = None
        self._peak_equity: float | None = None
        #: "ok" | "uninitialized" | "unreadable" | "corrupt" — see _classify().
        #: "corrupt" is a latch (also durable, _LATCH_KEY): nothing but
        #: repair_baseline() clears it.
        self.baseline_status = "uninitialized"
        self.corrupt_detail: dict | None = None
        #: a baseline was established (loaded or initialized) in this process
        self._established = False
        #: release-proof identity: this instance, and a counter bumped by every
        #: re-evaluation, latch and repair (see RiskRelease).
        self._identity = uuid.uuid4().hex
        self._generation = 0
        #: a release proof older than this is not fresh enough to activate on
        self.release_max_age_s = 30.0
        self._lock = threading.RLock()
        self._load_state()

    # ── persistence ─────────────────────────────────────────────────────
    #: the high-water mark and the day's baseline live here, not only in RAM.
    #: Held in memory alone, a restart made the first equity reading the new
    #: peak, so an account 20% underwater reported 0% drawdown: the derisk
    #: ladder and halt_drawdown_pct were both disarmed, and a losing day was
    #: forgiven — precisely when a restart is most likely to be happening.
    _STATE_KEY = "risk_state"
    #: written once, in the same transaction as the first durable baseline (or
    #: backfilled beside an existing valid one): "a Risk baseline has existed".
    #: With it, a missing risk_state is lost evidence, not a first run.
    _MARKER_KEY = "risk_baseline_marker"
    #: durable copy of the corruption latch; only repair_baseline() deletes it.
    _LATCH_KEY = "risk_state_corrupt_latch"

    def _load_state(self) -> None:
        with self._lock:
            status, state, detail = self._classify()
            if status == "ok":
                self._peak_equity = state["peak_equity"]
                self._day_start_equity = state["day_start_equity"]
                self._day_key = state["day_key"]
                self.baseline_status = "ok"
                self._established = True
            elif status == "corrupt":
                self._mark_corrupt(detail)
            elif status == "unreadable":
                self.baseline_status = "unreadable"

    # ── baseline integrity: first initialization vs lost evidence ───────
    @staticmethod
    def _parse(raw: str) -> dict | None:
        """A durable baseline, validated; None when it is not a usable one."""
        try:
            d = json.loads(raw)
            peak = d["peak_equity"]
            day = d.get("day_start_equity")
            key = d.get("day_key")
        except Exception:
            return None
        if not _is_number(peak) or not math.isfinite(peak) or peak <= 0:
            return None
        if day is not None and (not _is_number(day) or not math.isfinite(day) or day <= 0):
            return None
        if key is not None and not isinstance(key, str):
            return None
        return {"peak_equity": float(peak),
                "day_start_equity": None if day is None else float(day),
                "day_key": key}

    def _classify(self) -> tuple[str, dict | None, dict | None]:
        """("ok", state, None) | ("uninitialized", None, None) |
        ("unreadable", None, detail) | ("corrupt", None, detail).

        `state` carries the exact durable blob as `raw`. A durable latch is
        corrupt whatever the blob now says. Present but unusable is corrupt.
        Absent is a first initialization only when the initialization marker
        is absent and no baseline was established in this process; equity
        rows prove nothing (a failed first cycle writes them too). A failed
        read is unreadable: fail closed, but not evidence of corruption.
        """
        if not self.journal:
            return "uninitialized", None, None
        try:
            latch = self.journal.kv_get(self._LATCH_KEY)
            raw = self.journal.kv_get(self._STATE_KEY)
            marker = self.journal.kv_get(self._MARKER_KEY)
        except Exception as e:
            return "unreadable", None, {"cause": "risk_state_read_failed",
                                        "error": type(e).__name__}
        if latch:
            return "corrupt", None, {"cause": "risk_state_latched",
                                     "latch": str(latch)[:300], "raw": str(raw)[:200]}
        if raw:
            state = self._parse(raw)
            if state is not None:
                return "ok", {**state, "raw": raw}, None
            return "corrupt", None, {"cause": "risk_state_malformed",
                                     "raw": str(raw)[:200], "marker": marker}
        if marker or self._established:
            return "corrupt", None, {"cause": "risk_state_missing", "marker": marker,
                                     "memory_peak": self._peak_equity}
        return "uninitialized", None, None

    def _write_kv(self, sets: dict, *, insert_only: dict | None = None,
                  deletes: tuple = (), conn=None) -> None:
        """All writes in one journal transaction (sequential for duck journals).
        `conn`: an open transaction to write in (no nested transaction)."""
        if conn is not None:
            self._kv_rows(conn, sets, insert_only, deletes)
            return
        tx = getattr(self.journal, "_tx", None)
        if tx is None:
            for k, v in sets.items():
                self.journal.kv_set(k, v)
            for k, v in (insert_only or {}).items():
                if not self.journal.kv_get(k):
                    self.journal.kv_set(k, v)
            return
        with tx() as c:
            self._kv_rows(c, sets, insert_only, deletes)

    @staticmethod
    def _kv_rows(c, sets, insert_only, deletes) -> None:
        for k, v in sets.items():
            c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)", (k, v))
        for k, v in (insert_only or {}).items():
            c.execute("INSERT OR IGNORE INTO state_kv(key,value) VALUES (?,?)", (k, v))
        for k in deletes:
            c.execute("DELETE FROM state_kv WHERE key=?", (k,))

    def _mark_corrupt(self, detail: dict | None, conn=None) -> None:
        """Latch fail-closed (memory and durable); audit once per latch."""
        first = self.baseline_status != "corrupt"
        self.baseline_status = "corrupt"
        self._generation += 1
        if first or self.corrupt_detail is None:
            self.corrupt_detail = detail
        if not first:
            return
        log.error(f"RISK STATE CORRUPT — activation contained until repaired: {detail}")
        if not self.journal:
            return
        record = {**(detail or {}), "memory_peak": self._peak_equity,
                  "memory_day_start": self._day_start_equity,
                  "memory_day_key": self._day_key}
        try:
            self._write_kv({}, insert_only={self._LATCH_KEY: json.dumps(
                {"latched_at": datetime.now(timezone.utc).isoformat(),
                 "cause": (detail or {}).get("cause"),
                 "memory_peak": self._peak_equity}, default=str)}, conn=conn)
        except Exception as e:
            log.error(f"durable Risk latch not written (memory latch holds): {e}")
        try:
            if conn is not None:
                conn.execute("INSERT INTO control_events(ts,event,from_state,to_state,actor,"
                             "detail) VALUES (?,?,?,?,?,?)",
                             (datetime.now(timezone.utc).isoformat(), "risk_state_corrupt",
                              "", "", "risk_engine", json.dumps(record, default=str)))
            else:
                self.journal.log_control_event(
                    "risk_state_corrupt", "risk_engine",
                    detail=json.loads(json.dumps(record, default=str)))
        except Exception:
            pass

    def repair_baseline(self, peak_equity, *, actor: str, reason: str) -> dict:
        """Explicit repair of a corrupt baseline; the only way to clear the latch.

        Refused (ValueError, nothing written) unless the peak is a finite
        positive number (not a bool), actor and reason are non-empty, the
        equity history is readable, and the peak is at least every proven
        high-water mark: the highest valid historical equity, the in-memory
        peak, the peak recorded by the latch and any parseable durable peak.
        Validation and persistence run under the Risk lock inside one
        IMMEDIATE journal transaction, so a peak or history written by anyone
        before the repair is seen and one written after cannot be overwritten.
        The blob, marker, latch removal and the `risk_state_repaired` audit
        commit together or not at all. Repair never changes control state.
        """
        if not _is_number(peak_equity) or not math.isfinite(peak_equity) or peak_equity <= 0:
            raise ValueError("repair peak must be a finite positive number")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("repair requires a non-empty actor")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("repair requires a non-empty reason")
        tx = getattr(self.journal, "_tx", None)
        if tx is None:
            raise ValueError("repair requires a transactional journal")
        peak = float(peak_equity)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            with tx() as c:
                c.execute("BEGIN IMMEDIATE")          # serialize against every writer
                try:
                    # Only finite positive numbers are equity. SQLite orders TEXT
                    # above every number, so MAX over mixed rows could pick 'bad'
                    # and hide the true high-water mark: filter by type first.
                    hist = c.execute(
                        "SELECT MAX(equity) AS m FROM equity "
                        "WHERE typeof(equity) IN ('real','integer') AND equity > 0 "
                        "AND equity <= 1.7976931348623157e308").fetchone()
                    history_max = hist["m"] if hist is not None else None
                    invalid_rows = c.execute(
                        "SELECT COUNT(*) AS n FROM equity WHERE typeof(equity) NOT IN "
                        "('real','integer') OR equity <= 0 OR equity > 1.7976931348623157e308").fetchone()["n"]
                    first_today = c.execute(
                        "SELECT equity FROM equity WHERE ts >= ? AND "
                        "typeof(equity) IN ('real','integer') AND equity > 0 "
                        "AND equity <= 1.7976931348623157e308 ORDER BY ts LIMIT 1", (today,)).fetchone()
                    kv = {r["key"]: r["value"] for r in c.execute(
                        "SELECT key, value FROM state_kv WHERE key IN (?,?)",
                        (self._STATE_KEY, self._LATCH_KEY)).fetchall()}
                except Exception as e:
                    raise ValueError(f"repair refused: equity history unreadable ({e})") from e
                durable = self._parse(kv.get(self._STATE_KEY) or "")
                try:
                    latch_peak = json.loads(kv.get(self._LATCH_KEY) or "{}").get("memory_peak")
                except Exception:
                    latch_peak = None
                proven = [history_max, self._peak_equity, latch_peak,
                          durable and durable["peak_equity"],
                          (self.corrupt_detail or {}).get("memory_peak")]
                floor = max([float(v) for v in proven
                             if _is_number(v) and math.isfinite(v) and v > 0], default=0.0)
                if history_max is not None and not (_is_number(history_max)
                                                    and math.isfinite(history_max)):
                    raise ValueError("repair refused: equity history maximum is not finite")
                if peak < floor:
                    raise ValueError(f"repair peak {peak} below proven high-water mark {floor}")
                day_start = float(first_today["equity"]) if first_today else None
                if day_start is not None and not (math.isfinite(day_start) and day_start > 0):
                    day_start = None
                day_key = today if day_start is not None else None
                prior = self.corrupt_detail
                record = {"peak_equity": peak, "day_start_equity": day_start,
                          "proven_floor": floor, "history_max": history_max,
                          "invalid_history_rows": invalid_rows,
                          "reason": reason, "prior": prior}
                if self._parse(json.dumps({"peak_equity": peak, "day_start_equity": day_start,
                                           "day_key": day_key})) is None:
                    raise ValueError("repair refused: repaired state would be invalid")
                c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)",
                          (self._STATE_KEY, json.dumps({"peak_equity": peak,
                                                        "day_start_equity": day_start,
                                                        "day_key": day_key})))
                c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)",
                          (self._MARKER_KEY, json.dumps({"established": True,
                                                         "source": "repair_baseline"})))
                c.execute("DELETE FROM state_kv WHERE key=?", (self._LATCH_KEY,))
                c.execute("INSERT INTO control_events(ts,event,from_state,to_state,actor,detail) "
                          "VALUES (?,?,?,?,?,?)",
                          (datetime.now(timezone.utc).isoformat(), "risk_state_repaired",
                           "", "", actor, json.dumps(record, default=str)))
            # committed; memory follows while the Risk lock is still held, so no
            # newer latch/repair can land between the commit and this publication
            self._peak_equity, self._day_start_equity, self._day_key = peak, day_start, day_key
            self.baseline_status = "ok"
            self.corrupt_detail = None
            self._established = True
            self._generation += 1
        return record

    def _save_state(self) -> bool:
        if not self.journal:
            return True
        try:
            self._write_kv({self._STATE_KEY: json.dumps({
                "peak_equity": self._peak_equity,
                "day_start_equity": self._day_start_equity,
                "day_key": self._day_key})},
                insert_only={self._MARKER_KEY: json.dumps(
                    {"established": True,
                     "at": datetime.now(timezone.utc).isoformat()})})
            return True
        except Exception as e:
            log.warning(f"risk state not persisted: {e}")
            return False

    # ── equity tracking ────────────────────────────────────────────────
    def update_equity(self, equity: float, *, authoritative: bool = True) -> dict:
        """Call once per cycle with marked equity. Returns status flags.

        A corrupt baseline is never replaced: the peak is not re-seeded, nothing
        is saved, and `risk_state="corrupt"` tells the kernel to contain. A first
        baseline is established only from an authoritative (freshly read),
        finite, positive equity, and only if it is durably written together
        with the initialization marker. Every call is a re-evaluation: it
        bumps the generation, so any outstanding release proof is superseded.
        """
        with self._lock:
            self._generation += 1
            status, state, detail = self._classify()
            if status == "corrupt" or self.baseline_status == "corrupt":
                self._mark_corrupt(detail or self.corrupt_detail)
                return self._unusable(equity, "corrupt")
            if status == "unreadable":
                self.baseline_status = "unreadable"
                return self._unusable(equity, "unreadable")
            valid = _is_number(equity) and math.isfinite(equity) and equity > 0
            if status == "ok" and state["peak_equity"] > (self._peak_equity or 0):
                self._peak_equity = state["peak_equity"]     # the more conservative peak
            if status == "uninitialized":
                if not (authoritative and valid):
                    self.baseline_status = "uninitialized"
                    return self._unusable(equity, "uninitialized")
                prior = (self._peak_equity, self._day_start_equity, self._day_key)
            elif not valid:
                return self._unusable(equity, "equity_unusable")
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self._day_key:
                self._day_key = today
                self._day_start_equity = equity
            if self._peak_equity is None or equity > self._peak_equity:
                self._peak_equity = equity
            dd_pct = (self._peak_equity - equity) / self._peak_equity if self._peak_equity else 0.0
            day_pnl_pct = (
                (equity - self._day_start_equity) / self._day_start_equity
                if self._day_start_equity else 0.0)
            saved = self._save_state()
            if status == "uninitialized":
                if not saved:           # no durable baseline + marker: not initialized
                    self._peak_equity, self._day_start_equity, self._day_key = prior
                    return self._unusable(equity, "uninitialized")
                log.warning(f"risk baseline initialized at equity {equity}")
            self.baseline_status = "ok"
            self._established = True
            return {
                "equity": equity,
                "drawdown_pct": round(dd_pct * 100, 2),
                "daily_pnl_pct": round(day_pnl_pct * 100, 2),
                "halt_breached": dd_pct >= self.halt_dd,
                "risk_state": "ok",
            }

    @staticmethod
    def _unusable(equity, risk_state: str) -> dict:
        return {"equity": equity, "drawdown_pct": None, "daily_pnl_pct": None,
                "halt_breached": False, "risk_state": risk_state}

    def release_check(self, equity: float | None, *, authoritative: bool = True) -> RiskRelease:
        """Would Risk permit ACTIVE now, at this freshly read equity?

        The same HALT rule as update_equity()/check_entry() — drawdown from
        the high-water mark at or beyond halt_drawdown_pct — evaluated without
        moving the peak or the day baseline, so it cannot clear a halt by
        forgetting it. The baseline is the larger of the in-memory peak and
        the durable one. Any corruption it detects is latched here too. A
        corrupt, unreadable or not-yet-initialized baseline, a non-authoritative
        or non-positive/non-finite equity fails closed. The daily loss breaker
        only blocks entries (it never HALTs), so it is not a release condition.
        The answer is a proof bound to this exact Risk state (see RiskRelease).
        """
        halt = round(self.halt_dd * 100, 2)
        # One serialized evaluation: nothing interleaves with it, and its proof
        # carries the generation of *this* evaluation, captured at its own
        # bump. Any later check (even a nested one, e.g. from an equity
        # object's __float__) bumps past it, so this proof is superseded.
        with self._lock:
            self._generation += 1       # any re-evaluation supersedes older proofs
            generation = self._generation
            return self._evaluate_release(equity, authoritative, halt, generation)

    def _evaluate_release(self, equity, authoritative: bool, halt: float,
                          generation: int) -> RiskRelease:
        try:
            equity = float(equity) if not isinstance(equity, bool) else float("nan")
        except (TypeError, ValueError):
            equity = float("nan")
        if not math.isfinite(equity) or equity <= 0:
            return RiskRelease(False, "risk_equity_unreadable", halt_drawdown_pct=halt)
        if not authoritative:
            return RiskRelease(False, "risk_equity_not_authoritative", equity=equity,
                               halt_drawdown_pct=halt)
        with self._lock:
            status, state, detail = self._classify()
            if status == "corrupt" or self.baseline_status == "corrupt":
                self._mark_corrupt(detail or self.corrupt_detail)   # latch on detection
                status = "corrupt"
            memory = self._peak_equity
            ident = dict(generation=generation, identity=self._identity,
                         state_digest=_digest(state["raw"]) if state else None,
                         read_at=time.monotonic(), authoritative=True)
        if status != "ok":
            reason = {"corrupt": "risk_state_corrupt",
                      "unreadable": "risk_state_unreadable"}.get(
                          status, "risk_baseline_uninitialized")
            return RiskRelease(False, reason, equity=equity, peak_equity=memory,
                               halt_drawdown_pct=halt, **ident)
        peak, dd = self._drawdown(state["peak_equity"], memory, equity)
        allowed = dd < self.halt_dd
        return RiskRelease(allowed, "risk_release_ok" if allowed else "risk_halt_active",
                           equity=equity, peak_equity=peak,
                           drawdown_pct=round(dd * 100, 2), halt_drawdown_pct=halt,
                           verify=self.hold_release if allowed else None,
                           verify_baseline=self.hold_baseline, **ident)

    @staticmethod
    def _drawdown(durable: float, memory, equity: float) -> tuple[float, float]:
        valid_memory = memory is not None and math.isfinite(memory) and memory > 0
        peak = max(durable, memory) if valid_memory else durable
        return peak, max(0.0, (peak - equity) / peak)

    @contextmanager
    def _durable_hold(self):
        """Risk lock + one IMMEDIATE journal transaction. While held, no Risk
        writer in this process and no writer on any other connection or
        process can change durable Risk state. Yields the transaction's
        connection (None for journals without transactions): the caller's
        final writes go on it and commit atomically with the verification;
        an exception rolls both back."""
        with self._lock:
            tx = getattr(self.journal, "_tx", None) if self.journal else None
            if tx is None:
                yield None
                return
            with tx() as c:
                c.execute("BEGIN IMMEDIATE")
                yield c

    @contextmanager
    def hold_release(self, proof: RiskRelease):
        """Revalidate a release proof and hold it through the ACTIVE CAS.

        Yields (refusal | None, conn). None: the proof still describes the
        exact current Risk state, and nothing can change that state until the
        block exits; the CAS must write on `conn` (see _durable_hold).
        """
        with self._durable_hold() as c:
            yield self._revalidate(proof, c), c

    @contextmanager
    def hold_baseline(self, proof):
        """Yields (refusal | None, conn): is the durable baseline still exactly
        the verified one? Held for the caller's final decision and its audit,
        which must be written on `conn`.

        For rollback readiness: the baseline an older kernel will load must be
        the valid, unlatched blob this proof saw (drawdown is not a condition:
        the older kernel's own Risk re-halts on it).
        """
        with self._durable_hold() as c:
            yield self._baseline_refusal(proof, c), c

    def baseline_intact(self, proof) -> str | None:
        with self.hold_baseline(proof) as (refusal, _):
            return refusal

    def _baseline_refusal(self, proof, conn=None) -> str | None:
        if not isinstance(proof, RiskRelease) or proof.state_digest is None:
            return "risk_baseline_unverified"
        if proof.identity != self._identity:
            return "risk_proof_identity_mismatch"
        status, state, detail = self._classify()
        if status == "corrupt" or self.baseline_status == "corrupt":
            self._mark_corrupt(detail or self.corrupt_detail, conn)
            return "risk_state_corrupt"
        if status != "ok":
            return ("risk_state_unreadable" if status == "unreadable"
                    else "risk_baseline_uninitialized")
        if _digest(state["raw"]) != proof.state_digest:
            return "risk_proof_state_changed"
        return None

    def _revalidate(self, proof, conn=None) -> str | None:
        if not isinstance(proof, RiskRelease) or not proof.allowed:
            return getattr(proof, "reason", None) or "risk_proof_invalid"
        if not proof.authoritative:
            return "risk_equity_not_authoritative"
        if proof.identity != self._identity:
            return "risk_proof_identity_mismatch"
        if proof.read_at is None or time.monotonic() - proof.read_at > self.release_max_age_s:
            return "risk_proof_stale"
        status, state, detail = self._classify()
        if status == "corrupt" or self.baseline_status == "corrupt":
            self._mark_corrupt(detail or self.corrupt_detail, conn)
            return "risk_state_corrupt"
        if status == "unreadable":
            return "risk_state_unreadable"
        if status != "ok":
            return "risk_baseline_uninitialized"
        if _digest(state["raw"]) != proof.state_digest:
            return "risk_proof_state_changed"
        if proof.generation != self._generation:
            return "risk_proof_superseded"
        _, dd = self._drawdown(state["peak_equity"], self._peak_equity, proof.equity)
        if dd >= self.halt_dd:
            return "risk_halt_active"
        return None

    def derisk_multiplier(self, dd_pct: float) -> float:
        m = 1.0
        for step_dd, mult in self.derisk_steps:
            if dd_pct >= step_dd:
                m = mult
        return m

    # ── entry permission + sizing ───────────────────────────────────────
    def check_entry(self, state: ControlState, symbol: str, price: float,
                    atr: float, side_risk_frac: float,
                    open_positions: list[Position], equity: float,
                    closed_trades_count: int, market_type: str) -> SizingResult:
        """side_risk_frac: stop distance as fraction of price (e.g. 0.02)."""
        if state == ControlState.FROZEN:
            return SizingResult(False, "state=FROZEN: entries blocked", 0, 0, 0, 0)
        if state == ControlState.HALTED:
            return SizingResult(False, "state=HALTED", 0, 0, 0, 0)
        if state != ControlState.ACTIVE:  # RECOVERY, and fail closed on any other
            return SizingResult(False, f"state={state.value}: entries blocked",
                                0, 0, 0, 0)

        # A stale journal fallback cannot initialize here: with no baseline
        # there is no equity history, so the only fallback is 0 (refused).
        st = self.update_equity(equity)
        if st["risk_state"] != "ok":
            return SizingResult(False, f"risk_state={st['risk_state']}: entries blocked",
                                0, 0, 0, 0)
        if st["halt_breached"]:
            raise RiskError(f"drawdown {st['drawdown_pct']}% ≥ halt "
                            f"{self.halt_dd*100:.0f}% — flip HALTED")
        if st["daily_pnl_pct"] <= -self.daily_loss_block * 100:
            return SizingResult(False,
                                f"daily breaker {st['daily_pnl_pct']:.1f}%", 0, 0, 0, 0)
        if len(open_positions) >= self.max_positions:
            return SizingResult(False, "max positions reached", 0, 0, 0, 0)
        if any(p.symbol == symbol for p in open_positions):
            return SizingResult(False, "already exposed here", 0, 0, 0, 0)

        # heat accounting: open risk + this trade's intended risk
        open_risk = sum(self._position_risk(p, price) for p in open_positions)
        same_symbol_risk = sum(self._position_risk(p, price) for p in open_positions
                               if p.symbol == symbol)
        budget = equity * self.risk_pct
        headroom_heat = equity * self.heat_cap - open_risk
        headroom_sym = equity * self.symbol_cap - same_symbol_risk
        allowed = min(budget, headroom_heat, headroom_sym)
        if allowed <= 0:
            return SizingResult(
                False,
                f"risk budget exhausted (heat {open_risk/equity:.1%}/"
                f"{self.heat_cap:.0%})", 0, 0, 0, 0)

        mult = self.derisk_multiplier(st["drawdown_pct"])
        if closed_trades_count < self.proving_trades:
            mult *= self.proving_mult
        allowed *= mult

        # notional such that stop-loss hit ≈ `allowed` loss
        stop_dist = max(price * side_risk_frac, price * 0.004)   # ≥0.4% floor
        amount = allowed / stop_dist
        notional = amount * price

        # ── margin caps: reduce to fit rather than refuse ────────────────
        open_margin = sum(p.notional_usdt for p in open_positions) / self.leverage
        margin_room = min(equity * self.max_pos_margin,
                          equity * self.max_total_margin - open_margin)
        if margin_room <= 0:
            return SizingResult(
                False,
                f"margin cap reached ({open_margin/equity:.0%}/"
                f"{self.max_total_margin:.0%} of equity committed)",
                0, 0, 0, 0)
        margin = notional / self.leverage
        if margin > margin_room:
            scale = margin_room / margin
            amount *= scale
            notional *= scale
            allowed *= scale          # the realised risk shrinks with the size

        if notional / self.leverage < self.min_notional:
            return SizingResult(False, "below min notional", 0, 0, 0, 0)
        return SizingResult(True, "ok",
                            size_usdt=round(notional / self.leverage, 2),
                            amount=round(amount, 8),
                            risk_usdt=round(allowed, 2),
                            size_mult=mult)

    def _position_risk(self, p: Position, mark: float) -> float:
        """Open risk ≈ distance to stop × amount × leverage."""
        if p.stop_loss <= 0:
            return p.notional_usdt * 0.05     # unprotected fallback estimate
        dist = abs(p.entry_price - p.stop_loss) / p.entry_price
        return p.notional_usdt * dist

    # ── protective levels ───────────────────────────────────────────────
    def protection_levels(self, price: float, atr: float, side: str,
                          tp_mult: float) -> tuple[float, float]:
        sl_dist = max(atr * self.sl_atr_mult, price * 0.004)
        if side == "long":
            return price - sl_dist, price + atr * tp_mult
        return price + sl_dist, price - atr * tp_mult
