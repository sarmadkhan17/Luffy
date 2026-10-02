"""Strategy health observation — strategy-health-observation.v1.

One record per deployed spec attempted in one `Analyst.review_deployed`
sweep, plus one sweep record, both in `brain_events`. It remembers what the
decay sweep saw BEFORE retirement, not only at it.

Two separate facts per record:

- ``lifecycle_branch`` — what `rolling.has_decayed` returned ("idle",
  "still_working", "decayed"), and ``retirement_action_selected`` — whether
  the Analyst selected a retirement action. The kernel applies it later;
  this record never claims it was applied.
- ``verdict`` — how far the observation can be read:

  - ``still_working``    — branch still_working on complete usable coverage.
    A lenient retirement check, NOT admission-grade evidence.
  - ``decayed``          — branch decayed on complete usable coverage. A
    trigger, not a proven loss of edge.
  - ``idle``             — branch idle (fewer than decay_min_trades) on
    complete usable coverage: every relevant symbol loaded, long enough,
    evaluated without error, with scorable bars and its required data.
    Strategy quiet. Neither healthy nor unhealthy.
  - ``compile_failed``   — the spec did not compile; nothing was scored.
  - ``evaluation_failed`` — the branch cannot be read truthfully: evaluation
    raised, or coverage was incomplete (see ``coverage_faults``). The
    underlying branch and selected action are still recorded unchanged.

Sweeps: `sweep_id` is audit/correlation provenance only. Repeated sweeps
re-read overlapping windows of the same market; they are NOT independent
samples. Nothing here counts, rates, thresholds, triggers or ranks.

Telemetry is subordinate to retirement. `open_sweep` never raises (a failed
setup yields a disabled sweep); during the sweep the Analyst only appends
raw facts in memory; records are built and written by `flush()`, which the
caller runs after the lifecycle work it describes has been applied, and
which swallows every failure.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core.types import TF_MS
from . import signal_occurrence as so

log = logging.getLogger("strategy.health_observation")

SCHEMA = "strategy-health-observation.v1"
KIND_SPEC = "strategy_health_observed"
KIND_SWEEP = "strategy_health_sweep"

# ── verdicts ─────────────────────────────────────────────────────────────
IDLE = "idle"
STILL_WORKING = "still_working"
DECAYED = "decayed"
COMPILE_FAILED = "compile_failed"
EVALUATION_FAILED = "evaluation_failed"
VERDICTS = (IDLE, STILL_WORKING, DECAYED, COMPILE_FAILED, EVALUATION_FAILED)
BRANCHES = (IDLE, STILL_WORKING, DECAYED)

# ── evaluation_failed reasons, in precedence order ───────────────────────
EVALUATION_EXCEPTION = "evaluation_exception"
COVERAGE_UNAVAILABLE = "coverage_unavailable"
NO_SYMBOL_SCORED = "no_symbol_scored"
SYMBOL_EVALUATION_ERRORS = "symbol_evaluation_errors"
REQUIRED_CONTEXT_MISSING = "required_context_missing"
NO_SCORABLE_BARS = "no_scorable_bars"
SCORING_EVIDENCE_UNAVAILABLE = "scoring_evidence_unavailable"
DECLARED_NOT_LOADED = "declared_not_loaded"
NO_FRAME = "no_frame"
INSUFFICIENT_HISTORY = "insufficient_history"
COVERAGE_FAULTS = (SYMBOL_EVALUATION_ERRORS, REQUIRED_CONTEXT_MISSING,
                   NO_SCORABLE_BARS, SCORING_EVIDENCE_UNAVAILABLE,
                   DECLARED_NOT_LOADED, NO_FRAME, INSUFFICIENT_HISTORY)

NOT_SUPPLIED = "not_supplied"

# ── pooled PF interpretation (rolling._score_window sentinels) ───────────
PF_RATIO = "ratio"                       # gross_loss > 0
PF_NO_LOSSES = "sentinel_99_no_losses"   # gross_loss == 0, gross_win > 0
PF_NO_GROSS = "sentinel_0_no_gross"      # gross_win == gross_loss == 0

SWEEP_COMPLETED = "completed"
SWEEP_ABORTED = "aborted"

MAX_MESSAGE = 300

SEMANTICS = ("sweep observation of a backtest on close fills over the "
             "recent window; not live fills, not admission evidence, not an "
             "independent sample across sweeps, not salience")

HEALTH_FINGERPRINT_SCHEMA = "strategy-health-eval-fingerprint.v1"
#: spec content the decay simulation reads: the signal (entries, filters,
#: timeframe, universe, direction) AND the exits it simulates. Names,
#: thesis, provenance, regime_filter, markets and the compiler-derived
#: data_requires do not reach rolling._score_window.
HEALTH_FINGERPRINT_FIELDS = ("entry_long", "entry_short", "filters",
                             "timeframe", "universe", "direction", "exit")

#: risk keys simulate() reads, with the default it applies when absent
_SIM_DEFAULTS = {"taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
                 "risk_per_trade_pct": None, "funding_rate_8h": 0.0001,
                 "bar_minutes": 15, "real_funding": True}
_SIM_FIXED = {"equity": 2000.0, "warmup_bars": 210, "fill": "bar_close",
              "atr_period": 14}

CODE_MANIFEST_KIND = "partial_code_manifest"
CODE_MANIFEST_NOTE = ("hashes of the modules named here only; feature, "
                      "indicator and data modules evaluation also imports "
                      "are not covered, so this does NOT establish complete "
                      "reproducibility")
#: the code whose behaviour produces a verdict — a partial list
_CODE = ("strategy/health_observation.py", "strategy/rolling.py",
         "strategy/vector_backtest.py", "strategy/compile.py",
         "strategy/dsl.py", "strategy/features.py",
         "strategy/spec_evidence.py", "brain/analyst.py")


def code_manifest() -> dict:
    base = Path(__file__).resolve().parents[1]
    out = {}
    for name in _CODE:
        try:
            out[name] = hashlib.sha256((base / name).read_bytes()).hexdigest()
        except OSError:
            out[name] = None
    return out


def _sha(payload) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def spec_content_sha256(spec) -> str | None:
    """sha256 of the whole spec — artifact provenance only. Changes with
    prose, provenance and Analyst-rewritten regime_filter, so it is NOT a
    behavioural version; see health_fingerprint."""
    try:
        return _sha(json.loads(json.dumps(spec.to_dict(), default=str)))
    except Exception:
        return None


def health_fingerprint(spec) -> str | None:
    """sha256 of the canonical JSON of the spec content the decay
    simulation reads (HEALTH_FINGERPRINT_FIELDS). History uses this for
    `spec_version_changed`."""
    try:
        payload = {"schema": HEALTH_FINGERPRINT_SCHEMA}
        for f in HEALTH_FINGERPRINT_FIELDS:
            v = getattr(spec, f)
            payload[f] = asdict(v) if is_dataclass(v) else v
        return _sha(payload)
    except Exception:
        return None


def simulation_settings(risk) -> dict:
    """The settings the decay backtest actually ran with: each risk key
    simulate() reads, from config or simulate()'s own default."""
    risk = risk if isinstance(risk, dict) else {}
    applied, source = {}, {}
    for k, default in _SIM_DEFAULTS.items():
        if k in risk:
            applied[k], source[k] = risk[k], "config"
        else:
            applied[k], source[k] = default, "simulate_default"
    out = {"applied": _clean(applied), "source": source,
           "fixed": dict(_SIM_FIXED)}
    try:
        out["sha256"] = _sha({"applied": out["applied"],
                              "fixed": out["fixed"]})
    except Exception:
        out["sha256"] = None
    return out


def _fingerprint(spec):
    try:
        return so.spec_fingerprint(spec)
    except Exception:
        return None


def _num(v):
    """JSON-safe number: non-finite and non-numeric become None."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clean(v):
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if v is None or isinstance(v, str):
        return v
    return _num(v)


def _error(stage, exc) -> dict:
    return {"stage": stage, "error_class": type(exc).__name__,
            "message": str(exc)[:MAX_MESSAGE]}


def _declared(spec) -> list:
    try:
        return [str(s) for s in ((spec.universe or {}).get("include") or ())]
    except Exception:
        return []


def pf_kind(gross_win, gross_loss) -> str | None:
    gw, gl = _num(gross_win), _num(gross_loss)
    if gw is None or gl is None:
        return None
    if gl > 0:
        return PF_RATIO
    return PF_NO_LOSSES if gw > 0 else PF_NO_GROSS


def _iso_ms(iso):
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
    except Exception:
        return None


def coverage(spec, diag: dict) -> dict:
    """Which relevant symbols were usably scored, and why the rest were not.

    Relevant = the spec's declared include list plus every symbol that was
    actually attempted (each one enters the pooled result). When the spec
    declares no universe, the attempted frames stand in.

    "returned" = simulate() returned for the symbol. "usable" = returned
    with at least one scorable bar and every declared data requirement
    present. Only usable symbols count toward complete coverage."""
    declared = _declared(spec)
    attempted = list(diag.get("attempted") or [])
    scored = diag.get("scored") or {}
    relevant = sorted(set(declared) | set(attempted))
    errored = {s: e if isinstance(e, dict) else
               {"stage": None, "error_class": str(e), "message": None}
               for s, e in sorted((diag.get("errored") or {}).items())}
    short = diag.get("insufficient_history") or {}
    if not isinstance(short, dict):
        short = {s: None for s in short}
    no_frame = sorted(diag.get("no_frame") or [])
    not_loaded = sorted(set(declared) - set(attempted))

    no_scorable, scoring_unknown, missing_ctx, usable = [], [], {}, []
    for sym in sorted(scored):
        v = scored[sym] or {}
        sc = v.get("scoring") or {}
        bars = sc.get("scorable_bars")
        mc = v.get("missing_context")
        if bars == 0:
            no_scorable.append(sym)
        elif bars is None:
            scoring_unknown.append(sym)
        if mc:
            missing_ctx[sym] = list(mc)
        elif mc is None:
            scoring_unknown.append(sym)
        if bars and mc == []:
            usable.append(sym)
    scoring_unknown = sorted(set(scoring_unknown))

    unavailable = {}
    for s in not_loaded:
        unavailable[s] = {"stage": "frame_load", "reason": DECLARED_NOT_LOADED,
                          "detail": NOT_SUPPLIED}
    for s in no_frame:
        unavailable[s] = {"stage": "frame_load", "reason": NO_FRAME,
                          "detail": NOT_SUPPLIED}
    for s, info in sorted(short.items()):
        unavailable[s] = {"stage": "frame_load",
                          "reason": INSUFFICIENT_HISTORY, "detail": info}
    for s, e in errored.items():
        unavailable[s] = {**e, "reason": SYMBOL_EVALUATION_ERRORS}
    for s in no_scorable:
        unavailable[s] = {"stage": "simulation", "reason": NO_SCORABLE_BARS,
                          "detail": (scored[s] or {}).get("scoring")}
    for s, mc in missing_ctx.items():
        unavailable.setdefault(s, {"stage": "feature_context",
                                   "reason": REQUIRED_CONTEXT_MISSING,
                                   "detail": mc})
    for s in scoring_unknown:
        unavailable.setdefault(s, {"stage": "diagnostics",
                                   "reason": SCORING_EVIDENCE_UNAVAILABLE,
                                   "detail": NOT_SUPPLIED})

    faults = []
    for f, present in ((SYMBOL_EVALUATION_ERRORS, errored),
                       (REQUIRED_CONTEXT_MISSING, missing_ctx),
                       (NO_SCORABLE_BARS, no_scorable),
                       (SCORING_EVIDENCE_UNAVAILABLE, scoring_unknown),
                       (DECLARED_NOT_LOADED, not_loaded),
                       (NO_FRAME, no_frame),
                       (INSUFFICIENT_HISTORY, short)):
        if present:
            faults.append(f)
    return {
        "declared_symbols": declared,
        "declared_source": "universe.include" if declared
        else "attempted_frames",
        "relevant_symbols": relevant,
        "attempted_symbols": attempted,
        "returned_symbols": sorted(scored),
        "usable_symbols": usable,
        "declared_usable_symbols": sorted(set(declared or attempted)
                                          & set(usable)),
        "skipped_insufficient_history": sorted(short),
        "skipped_no_frame": no_frame,
        "declared_not_loaded": not_loaded,
        "errored_symbols": errored,          # {symbol: stage/class/message}
        "no_scorable_bars_symbols": no_scorable,
        "missing_required_context": missing_ctx,
        "unavailable_symbols": unavailable,
        "coverage_faults": faults,
        "coverage_complete": bool(relevant) and set(usable) == set(relevant),
    }


def classify(branch, diag, cov) -> tuple[str, str | None]:
    """(verdict, evaluation_failed reason). A branch is passed through only
    on complete usable coverage; otherwise evaluation_failed with the first
    fault in COVERAGE_FAULTS order. The branch itself is recorded as-is."""
    if branch not in BRANCHES or not diag or "scored" not in diag:
        return EVALUATION_FAILED, COVERAGE_UNAVAILABLE
    if cov["coverage_faults"]:
        return EVALUATION_FAILED, cov["coverage_faults"][0]
    if not cov["coverage_complete"] or not cov["declared_usable_symbols"]:
        return EVALUATION_FAILED, NO_SYMBOL_SCORED
    return branch, None


def _window(diag, timeframe, sweep_ms) -> dict:
    scored = diag.get("scored") or {}
    per = {}
    for s, v in sorted(scored.items()):
        sc = (v or {}).get("scoring") or {}
        per[s] = {k: sc.get(k) for k in (
            "source_bars", "source_start_bar_ts", "source_end_bar_ts",
            "effective_score_from", "scorable_range_bars", "scorable_bars",
            "scorable_start_bar_ts", "scorable_end_bar_ts", "last_bar_ts")}
    firsts = [v["scorable_start_bar_ts"] for v in per.values()
              if v["scorable_start_bar_ts"]]
    lasts = [v["last_bar_ts"] for v in per.values() if v["last_bar_ts"]]
    first = min(firsts, key=_iso_ms) if firsts else None
    last = max(lasts, key=_iso_ms) if lasts else None
    tf_ms = TF_MS.get(timeframe)
    last_ms = _iso_ms(last) if last else None
    close_ms = last_ms + tf_ms if last_ms is not None and tf_ms else None
    return {
        "window_bars": diag.get("window_bars"),
        "window_start_bar_open_ts": first,
        "window_end_bar_open_ts": last,
        "last_bar_open_ts": last,
        "last_bar_close_ms": close_ms,
        # checked here, against the sweep clock — not assumed from the store
        "last_bar_closed_at_sweep": (close_ms <= sweep_ms
                                     if close_ms is not None
                                     and sweep_ms is not None else None),
        "per_symbol": per,
    }


def _metrics(ev, diag) -> dict:
    trades = diag.get("trades")
    wins = diag.get("wins")
    gw, gl = diag.get("gross_win"), diag.get("gross_loss")
    per = {}
    for s, v in sorted((diag.get("scored") or {}).items()):
        per[s] = {"trades": v.get("trades"), "wins": v.get("wins"),
                  "gross_win": v.get("gross_win"),
                  "gross_loss": v.get("gross_loss"),
                  "pf_kind": pf_kind(v.get("gross_win"), v.get("gross_loss")),
                  "funding_series_used": v.get("funding_series_used"),
                  **{k: (ev.get("per_symbol") or {}).get(s, {}).get(k)
                     for k in ("pf", "pnl", "wr")}}
    return {
        "trades": trades if trades is not None else ev.get("trades"),
        "wins": wins,
        "gross_win": gw,
        "gross_loss": gl,
        "pooled_pf": ev.get("pooled_pf"),
        "pooled_pf_kind": pf_kind(gw, gl),
        # ev.winrate is 0.0 when there are no trades; null says "undefined"
        "winrate": (wins / trades) if trades else None,
        "pnl": ev.get("pnl"),
        "per_symbol": per,
    }


class _Disabled:
    """Stand-in when telemetry setup failed: every call is a no-op."""
    enabled = False

    def __getattr__(self, _name):
        return lambda *a, **k: None


def open_sweep(journal, specs, thresholds: dict, now=None):
    """A Sweep, or a disabled no-op when any part of setup (uuid, clock,
    spec ids) fails. Never raises."""
    try:
        return Sweep(journal, specs, thresholds, now=now)
    except Exception as e:
        try:
            log.warning(f"health observation: disabled for this sweep: {e}")
        except Exception:
            pass
        return _Disabled()


class Sweep:
    """One review_deployed() invocation.

    In-loop methods only append raw facts to memory: no I/O, no record
    building. `flush()` builds and writes every record afterwards, once the
    lifecycle work it describes has been applied. Every method swallows
    its own failures: observation has no authority over retirement."""
    enabled = True

    def __init__(self, journal, specs, thresholds: dict, now=None):
        self.journal = journal
        self.sweep_id = uuid.uuid4().hex
        self.started = now or datetime.now(timezone.utc)
        self.started_ms = int(self.started.timestamp() * 1000)
        self.thresholds = dict(thresholds)
        self.intended = [s.id for s in specs]
        self.events = []            # (kind, spec, payload) in order
        self.attempted = []
        self.loop_finished = False
        self.aborted_at, self.abort_error = None, None
        self.flushed = False

    # ── in-loop: memory only ────────────────────────────────────────────
    def attempt(self, spec):
        try:
            self.attempted.append(spec.id)
        except Exception:
            pass

    def compile_failed(self, spec, exc):
        try:
            self.events.append(("compile_failed", spec, _error("compile", exc)))
        except Exception:
            pass

    def evaluation_raised(self, spec, stage, exc):
        """Evaluation raised; the sweep aborts exactly as before."""
        try:
            err = _error(stage, exc)
            self.events.append(("raised", spec, err))
            self.aborted_at, self.abort_error = spec.id, err
        except Exception:
            pass

    def observed(self, spec, dead, ev, diag, risk=None):
        try:
            self.events.append(("observed", spec, (dead, ev, diag, risk)))
        except Exception:
            pass

    def finish(self):
        """The evaluation loop ran to its end (no I/O)."""
        self.loop_finished = True

    # ── after lifecycle: build and write ────────────────────────────────
    def _base(self, spec, manifest) -> dict:
        return {
            "schema": SCHEMA, "record": "spec",
            "sweep_id": self.sweep_id,
            "sweep_started_at": self.started.isoformat(),
            "spec_id": getattr(spec, "id", None),
            "spec_name": getattr(spec, "name", None),
            "health_fingerprint": health_fingerprint(spec),
            "health_fingerprint_schema": HEALTH_FINGERPRINT_SCHEMA,
            "health_fingerprint_fields": list(HEALTH_FINGERPRINT_FIELDS),
            "spec_fingerprint": _fingerprint(spec),
            "spec_fingerprint_schema": so.FINGERPRINT_SCHEMA,
            "spec_content_sha256": spec_content_sha256(spec),
            "timeframe": getattr(spec, "timeframe", None),
            # the lifecycle state is not supplied to review_deployed
            "lifecycle_state": None,
            "lifecycle_state_reason": NOT_SUPPLIED,
            "code_manifest": manifest,
            "code_manifest_kind": CODE_MANIFEST_KIND,
            "code_manifest_note": CODE_MANIFEST_NOTE,
            "thresholds": self.thresholds,
            "semantics": SEMANTICS,
        }

    def _build(self, kind, spec, payload, manifest) -> dict:
        base = self._base(spec, manifest)
        if kind == "compile_failed":
            return {**base, "verdict": COMPILE_FAILED, "verdict_reason": None,
                    "lifecycle_branch": None,
                    "retirement_action_selected": False, "error": payload}
        if kind == "raised":
            return {**base, "verdict": EVALUATION_FAILED,
                    "verdict_reason": EVALUATION_EXCEPTION,
                    "lifecycle_branch": None,
                    "retirement_action_selected": False, "error": payload}
        dead, ev, diag, risk = payload
        branch = diag.get("branch") if isinstance(diag, dict) else None
        diag = diag if isinstance(diag, dict) else {}
        ev = ev if isinstance(ev, dict) else {}
        cov = coverage(spec, diag)
        verdict, reason = classify(branch, diag, cov)
        return {
            **base, "verdict": verdict, "verdict_reason": reason,
            "lifecycle_branch": branch,
            # selected by the Analyst; the kernel applies it afterwards
            "retirement_action_selected": bool(dead),
            "lifecycle_verdict_text": ev.get("verdict"),
            "recent_days": ev.get("recent_days"),
            "window_days": ev.get("window_days"),
            "window_widened": ev.get("window_widened"),
            "simulation_settings": simulation_settings(risk),
            "metrics": _metrics(ev, diag),
            "window": _window(diag, getattr(spec, "timeframe", None),
                              self.started_ms),
            "coverage": cov, "error": None}

    def _write(self, kind, subject, rec):
        try:
            self.journal.log_brain_event(kind, subject, _clean(rec))
            return True
        except Exception as e:
            log.warning(f"health observation: write {kind} {subject} "
                        f"failed: {e}")
            return False

    def flush(self):
        """Build and write every record. Idempotent; never raises."""
        try:
            if self.flushed:
                return
            self.flushed = True
            self._flush()
        except Exception as e:
            try:
                log.warning(f"health observation: flush failed: {e}")
            except Exception:
                pass

    def _flush(self):
        try:
            manifest = code_manifest()
        except Exception:
            manifest = None
        recorded, failed, completed, compile_failed = [], {}, [], []
        n_selected = 0
        for kind, spec, payload in self.events:
            sid = getattr(spec, "id", None)
            if kind == "observed":
                completed.append(sid)
                if payload[0]:
                    n_selected += 1
            elif kind == "compile_failed":
                compile_failed.append(sid)
            try:
                rec = self._build(kind, spec, payload, manifest)
            except Exception as e:
                failed[sid] = {"step": "build", **_error("build", e)}
                continue
            if self._write(KIND_SPEC, sid, rec):
                recorded.append(sid)
            else:
                failed[sid] = {"step": "write", "stage": "write",
                               "error_class": None, "message": None}
        attempted = list(self.attempted)
        try:
            finished_at = datetime.now(timezone.utc).isoformat()
        except Exception:
            finished_at = None
        status = SWEEP_COMPLETED if (self.loop_finished
                                     and self.aborted_at is None) \
            else SWEEP_ABORTED
        self._write(KIND_SWEEP, self.sweep_id, {
            "schema": SCHEMA, "record": "sweep",
            "sweep_id": self.sweep_id,
            "sweep_started_at": self.started.isoformat(),
            "sweep_finished_at": finished_at,
            # authoritative evaluation status; telemetry failures below
            "status": status,
            "intended_spec_ids": list(self.intended),
            # processing of the spec began (context load onwards)
            "evaluation_attempted_spec_ids": attempted,
            # has_decayed returned a branch
            "evaluation_completed_spec_ids": completed,
            "compile_failed_spec_ids": compile_failed,
            # never reached: the sweep aborted before them
            "not_evaluated_spec_ids": [s for s in self.intended
                                       if s not in set(attempted)],
            "observation_recorded_spec_ids": recorded,
            # evaluated (or attempted) but its record could not be built or
            # written — NOT "not evaluated"
            "observation_failed_spec_ids": sorted(failed),
            "observation_failures": failed,
            "aborted_at_spec_id": self.aborted_at,
            "abort_error": self.abort_error,
            "n_retirement_actions_selected": n_selected,
            "thresholds": self.thresholds,
            "code_manifest": manifest,
            "code_manifest_kind": CODE_MANIFEST_KIND,
            "code_manifest_note": CODE_MANIFEST_NOTE,
            "semantics": SEMANTICS})


# ── read-only history ────────────────────────────────────────────────────
_SPEC_FIELDS = ("sweep_id", "sweep_started_at", "spec_id", "verdict",
                "health_fingerprint", "spec_content_sha256", "thresholds")
_SWEEP_FIELDS = ("sweep_id", "status", "intended_spec_ids",
                 "evaluation_completed_spec_ids")


def _decode(row):
    """(record, None) or (None, reason)."""
    raw = row.get("detail")
    try:
        rec = json.loads(raw) if isinstance(raw, str) else None
    except (TypeError, ValueError):
        return None, "detail_undecodable"
    if not isinstance(rec, dict):
        return None, "detail_not_object"
    if rec.get("schema") != SCHEMA:
        return None, "schema_unsupported"
    kind = row.get("kind")
    need = _SPEC_FIELDS if kind == KIND_SPEC else _SWEEP_FIELDS
    if any(f not in rec for f in need):
        return None, "missing_fields"
    if kind == KIND_SPEC:
        if rec.get("verdict") not in VERDICTS:
            return None, "verdict_unknown"
        if rec.get("spec_id") != row.get("subject"):
            return None, "subject_mismatch"
    elif rec.get("sweep_id") != row.get("subject"):
        return None, "subject_mismatch"
    return rec, None


def _key(row):
    return (str(row.get("ts")), row.get("id") if isinstance(
        row.get("id"), int) else -1, str(row.get("id")))


def _qualification(o) -> dict:
    return {k: o[k] for k in ("verdict", "verdict_reason",
                              "coverage_complete", "coverage_faults",
                              "lifecycle_branch",
                              "retirement_action_selected")}


def history(rows) -> dict:
    """Per-spec ordered health history. Pure; independent of row order.

    Descriptive only: ordered observations, the latest verdict WITH its
    coverage qualification, verdict transitions and health-fingerprint
    (behavioural version) transitions. It infers no probability, rate,
    confidence or salience. The only count is labelled a sweep-observation
    count — repeated sweeps re-read overlapping windows and are not
    independent evidence."""
    rows = sorted(rows, key=_key)
    by_spec: dict[str, list] = {}
    sweeps, invalid = {}, []
    for row in rows:
        if row.get("kind") not in (KIND_SPEC, KIND_SWEEP):
            continue
        rec, bad = _decode(row)
        if bad:
            invalid.append({"id": row.get("id"), "ts": row.get("ts"),
                            "kind": row.get("kind"), "reason": bad})
            continue
        if row["kind"] == KIND_SWEEP:
            sweeps[rec["sweep_id"]] = {
                k: rec.get(k) for k in (
                    "sweep_id", "sweep_started_at", "sweep_finished_at",
                    "status", "intended_spec_ids",
                    "evaluation_attempted_spec_ids",
                    "evaluation_completed_spec_ids",
                    "compile_failed_spec_ids", "not_evaluated_spec_ids",
                    "observation_recorded_spec_ids",
                    "observation_failed_spec_ids", "aborted_at_spec_id")}
            continue
        m = rec.get("metrics") or {}
        cov = rec.get("coverage") or {}
        by_spec.setdefault(rec["spec_id"], []).append({
            "ts": row.get("ts"), "event_id": row.get("id"),
            "sweep_id": rec["sweep_id"],
            "health_fingerprint": rec["health_fingerprint"],
            "spec_fingerprint": rec.get("spec_fingerprint"),
            "spec_content_sha256": rec["spec_content_sha256"],
            "simulation_settings_sha256": (rec.get("simulation_settings")
                                           or {}).get("sha256"),
            "verdict": rec["verdict"],
            "verdict_reason": rec.get("verdict_reason"),
            # coverage qualification travels with every verdict
            "coverage_complete": cov.get("coverage_complete"),
            "coverage_faults": cov.get("coverage_faults"),
            "lifecycle_branch": rec.get("lifecycle_branch"),
            "retirement_action_selected": rec.get(
                "retirement_action_selected"),
            "trades": m.get("trades"),
            "pooled_pf": m.get("pooled_pf"),
            "pooled_pf_kind": m.get("pooled_pf_kind"),
            "window_end_bar_open_ts": (rec.get("window") or {}).get(
                "window_end_bar_open_ts"),
            "thresholds": rec["thresholds"],
        })

    specs = []
    for sid in sorted(by_spec):
        obs = by_spec[sid]
        changes = [{"from": a["verdict"], "to": b["verdict"],
                    "from_verdict_reason": a["verdict_reason"],
                    "to_verdict_reason": b["verdict_reason"],
                    "at_ts": b["ts"], "sweep_id": b["sweep_id"],
                    "spec_version_changed": (
                        a["health_fingerprint"] != b["health_fingerprint"]),
                    "simulation_settings_changed": (
                        a["simulation_settings_sha256"]
                        != b["simulation_settings_sha256"]),
                    "thresholds_changed": a["thresholds"] != b["thresholds"]}
                   for a, b in zip(obs, obs[1:])
                   if a["verdict"] != b["verdict"]]
        versions, fp_changes = [], []
        for a, b in zip(obs, obs[1:]):
            if a["health_fingerprint"] != b["health_fingerprint"]:
                fp_changes.append({"from": a["health_fingerprint"],
                                   "to": b["health_fingerprint"],
                                   "at_ts": b["ts"],
                                   "sweep_id": b["sweep_id"]})
        for o in obs:
            if o["health_fingerprint"] not in versions:
                versions.append(o["health_fingerprint"])
        specs.append({
            "spec_id": sid,
            "latest_verdict": obs[-1]["verdict"],
            "latest": _qualification(obs[-1]),
            "latest_health_fingerprint": obs[-1]["health_fingerprint"],
            "first_observed_ts": obs[0]["ts"],
            "last_observed_ts": obs[-1]["ts"],
            "health_fingerprints": versions,
            "verdict_changes": changes,
            "health_fingerprint_changes": fp_changes,
            "n_sweep_observations": len(obs),
            "observations": obs,
        })

    seen = {o["sweep_id"] for s in by_spec.values() for o in s}
    return {
        "schema": SCHEMA,
        "counts_are": "sweep_observations_not_independent_evidence",
        "semantics": SEMANTICS,
        "specs": specs,
        "sweeps": [sweeps[k] for k in sorted(
            sweeps, key=lambda k: (str(sweeps[k]["sweep_started_at"]), k))],
        # a spec observation whose sweep never wrote its record (process
        # killed mid-flush, or the sweep write failed)
        "sweeps_without_record": sorted(seen - set(sweeps)),
        "invalid_records": invalid,
    }


def history_journal(journal) -> dict:
    return history(journal.strategy_health_rows())
