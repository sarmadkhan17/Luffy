"""Bounded snapshot adapter around the offline attention evaluator.

Only allowlisted numeric candle fields cross the process boundary. Availability
means first observed by this collector, never inferred from a candle's close.
"""
from __future__ import annotations

import math
import re
import threading
from dataclasses import asdict
from pathlib import Path
import hashlib
import time

from trader.cognition.attention import CognitionConfig, evaluate
from trader.cognition.contracts import INPUT_SCHEMA, TF_MS, load_input, to_dict
from trader.world.model import WorldModel


SCHEMA = "attention.telemetry.v1"
FIELDS = ("open", "high", "low", "close", "volume")


def settings(raw=None):
    raw = raw or {}
    out = {"timeframe": raw.get("timeframe", "4h")}
    if out["timeframe"] not in TF_MS:
        raise ValueError("unsupported attention timeframe")
    for name, default, lo, hi in (
        ("max_symbols", 16, 2, 64), ("queue_size", 16, 1, 128),
        ("max_causes", 64, 1, 256), ("max_scans", 512, 1, 10080),
        ("max_bytes", 64 * 1024 * 1024, 65536, 1024 * 1024 * 1024),
        ("max_age_seconds", 604800, 60, 2592000),
        ("timeout_seconds", 10, 1, 60), ("stale_seconds", 300, 10, 86400),
    ):
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f"invalid attention {name}")
        out[name] = value
    return out


def _number(value):
    value = float(value)
    return value if math.isfinite(value) else None


def capture(frames, members, scan_id, cfg, as_of_ms=None, supplemental=None,
            provenance=None):
    """Detach a bounded primitive snapshot; never pass mutable DataFrames on.

    `supplemental` is at most one observation-only SupplementalResult for a
    symbol outside the strategy scan. It sits outside the strategy-subset
    cap; a usable window joins the capture input, any other status is
    recorded as an issue without candles or membership. `provenance`
    ({mode, selection_id, snapshot_id}) links a registry-selected
    supplemental to its durable selection; manual capture carries none.
    """
    if provenance is not None:
        if supplemental is None:
            raise ValueError("provenance without a supplemental result")
        provenance = _provenance(provenance)
    started = time.perf_counter()
    tf = cfg["timeframe"]
    tf_ms = TF_MS[tf]
    as_of = int(time.time() * 1000) if as_of_ms is None else as_of_ms
    need = CognitionConfig().window + CognitionConfig().short + 1
    symbols = list(dict.fromkeys(members))
    included = symbols[:cfg["max_symbols"]]
    candles, issues = [], []
    sources = [(symbol, (frames.get(symbol) or {}).get(tf), "kernel.universe_frames")
               for symbol in included]
    extra = None
    if supplemental is not None:
        if supplemental.timeframe != tf:
            raise ValueError("supplemental timeframe does not match attention")
        if supplemental.symbol in symbols:
            raise ValueError("supplemental symbol is already a scan member")
        extra = supplemental.summary()
        if supplemental.usable:
            sources.append((supplemental.symbol, supplemental.frame, supplemental.source))
        else:
            issues.append({"symbol": supplemental.symbol,
                           "reason": "supplemental_" + supplemental.status,
                           "detail": supplemental.reason})
    for symbol, df, source in sources:
        if df is None or not len(df):
            issues.append({"symbol": symbol, "reason": "missing_timeframe"})
            continue
        try:
            # DataFeed frames are chronological. A malformed tail is refused,
            # not sorted over an unbounded history on the execution thread.
            tail_n = need + 2
            # Slice backing arrays before detaching values: constructing six
            # short pandas Series per symbol dominates producer overhead.
            opens = df["ts"].array[-tail_n:].to_numpy(dtype="datetime64[ms]").astype("int64").tolist()
            columns = [df[field].array[-tail_n:].tolist() for field in FIELDS]
            rows = []
            previous = -1
            for ms, *values in zip(opens, *columns):
                if ms <= previous:
                    raise ValueError("unordered candle tail")
                previous = ms
                if ms + tf_ms > as_of:
                    continue
                rows.append({"symbol": symbol, "open_ms": ms,
                             **dict(zip(FIELDS, map(_number, values))),
                             "available_ms": as_of, "source": source})
            candles.extend(rows[-need:])
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            issues.append({"symbol": symbol, "reason": "invalid_frame"})
    if as_of_ms is None:
        # Availability is when this process holds the detached values, not
        # the earlier instant at which copying began.
        as_of = int(time.time() * 1000)
        for candle in candles:
            candle["available_ms"] = as_of
    membership = [{"symbol": sym, "from_ms": as_of, "to_ms": None,
                   "available_ms": as_of, "source": "kernel.scan_symbols"}
                  for sym in included]
    scope = {"kind": "strategy_volume_subset", "candidate_count": len(symbols),
             "included_count": len(included), "cap": cfg["max_symbols"],
             "excluded_count": max(0, len(symbols) - len(included)),
             "exclusions": "trailing candidates beyond cap; not venue-wide",
             "universe_membership_before_capture": "unknown",
             "upstream_endpoint_and_cache_layer": "not_recorded_by_DataFeed"}
    if extra is not None:
        if provenance is not None:
            extra["provenance"] = provenance
        scope["supplemental"] = extra
        if supplemental.usable:
            membership.append({"symbol": supplemental.symbol, "from_ms": as_of,
                               "to_ms": None, "available_ms": as_of,
                               "source": supplemental.source})
    return {
        "kind": "scan", "schema_version": SCHEMA, "scan_id": scan_id,
        "as_of_ms": as_of, "capture_settings": dict(cfg), "capture_ms": (time.perf_counter() - started) * 1000,
        "scope": scope,
        "issues": issues,
        "input": {"schema": INPUT_SCHEMA, "timeframe": tf,
                  "decision_times": [as_of], "candles": candles,
                  "membership": membership, "participation": []},
    }


def evaluate_snapshot(event, world_model: WorldModel | None = None):
    if world_model is not None:
        if not isinstance(world_model, WorldModel):
            raise TypeError("world_model must be a WorldModel or None")
        if world_model.as_of_ms != event["as_of_ms"]:
            raise ValueError("world_model cut does not match as_of")
    cfg = CognitionConfig(max_symbols=len(event["input"]["membership"]) or 1)
    ds = load_input(event["input"])
    result = evaluate(ds, event["as_of_ms"], cfg, event["scan_id"], {}, world_model)
    return {"schema_version": SCHEMA, "scan_id": event["scan_id"],
            "as_of_ms": event["as_of_ms"], "scope": event["scope"],
            "capture_ms": event["capture_ms"], "issues": event["issues"],
            "capture_settings": event["capture_settings"],
            "capture_settings_id": digest(event["capture_settings"]),
            "rows": result["universe"], "market": result["market"],
            "observations": [to_dict(o) for o in result["observations"]],
            "config": asdict(cfg), "config_id": digest(asdict(cfg)),
            "rejected_inputs": ds.rejected,
            "limitations": ["Attention is an observation, not a trading signal.",
                            "Availability before first capture is unknown.",
                            "First-seen and revision lineage extend only over retained snapshots.",
                            "Participation inputs are not captured in this milestone.",
                            "No hypotheses, edge claims or learned ranking are produced."]
            + (["Positioning values are as stored at capture read time; no claim they were stored at as_of."]
               if "positioning" in event["input"] else [])}


def digest(value):
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def code_manifest():
    base = Path(__file__).resolve().parents[1]
    names = ("observability/attention.py", "observability/supplemental.py",
             "observability/_supplemental_child.py",
             "data/registry_provider.py", "data/binance_usdm_registry.py",
             "core/instrument_registry.py", "observability/registry_selector.py",
             "observability/selection_persistence.py", "core/journal.py",
             "observability/store.py",
             "observability/collector.py", "observability/worker.py",
             "observability/declared.py",
             "cognition/attention.py", "cognition/contracts.py",
             "observability/positioning.py",
             "observability/diagnostics.py", "strategy/library.py", "strategy/compile.py",
             "engine/orchestrator.py", "kernel.py")
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in names}


# ── automatic registry-backed supplemental slot ───────────────────────────

SUPPLEMENTAL_MODES = ("off", "manual", "registry")
PRODUCTION = "production"
#: implementation policy from current repository cadence, not SDD constants
REFRESH_SECONDS = 14400
RETRY_SECONDS = 300
MAX_SNAPSHOT_AGE_MS = 28_800_000
_PROVENANCE_KEYS = ("mode", "selection_id", "snapshot_id")
_NOT_TOKEN = re.compile(r"[^a-z0-9_]")


def _provenance(raw) -> dict:
    if not isinstance(raw, dict) or set(raw) != set(_PROVENANCE_KEYS):
        raise ValueError("provenance must be exactly {mode, selection_id, snapshot_id}")
    if raw["mode"] != "registry" or not all(
            isinstance(raw[k], str) and raw[k] for k in _PROVENANCE_KEYS):
        raise ValueError("invalid supplemental provenance")
    return {k: raw[k] for k in _PROVENANCE_KEYS}


def supplemental_mode(raw) -> str:
    """off | manual | registry. Key absent: a supplemental_symbol means
    manual, none means off. Registry plus a manual symbol, or manual without
    one, is a conflict (ValueError); the caller disables the slot only."""
    raw = raw or {}
    symbol = raw.get("supplemental_symbol")
    has_symbol = symbol not in (None, "", [], ())
    if "supplemental_mode" not in raw:
        return "manual" if has_symbol else "off"
    mode = raw["supplemental_mode"]
    if mode is False:  # YAML reads a bare `off` as False
        mode = "off"
    if mode not in SUPPLEMENTAL_MODES:
        raise ValueError("invalid supplemental_mode")
    if mode == "registry" and has_symbol:
        raise ValueError("supplemental_mode_conflict")
    if mode == "manual" and not has_symbol:
        raise ValueError("manual supplemental_mode needs supplemental_symbol")
    return mode


def registry_settings(raw) -> dict:
    """Explicit production observation target and refresh policy. The target
    is never inferred from BINANCE_DEMO or any other environment variable."""
    raw = raw or {}
    if raw.get("observation_target") != PRODUCTION:
        raise ValueError("registry mode requires observation_target: production")
    out = {"observation_target": PRODUCTION}
    for name, default in (("registry_refresh_seconds", REFRESH_SECONDS),
                          ("registry_retry_seconds", RETRY_SECONDS),
                          ("registry_max_snapshot_age_ms", MAX_SNAPSHOT_AGE_MS)):
        value = raw.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"invalid attention {name}")
        out[name] = value
    return out


def reason_token(reason) -> str:
    """Deterministic durable token: lowercase, [^a-z0-9_] -> _, <= 64 chars."""
    token = _NOT_TOKEN.sub("_", str(reason).lower())[:64]
    return token or "unknown"


def _wall_ms() -> int:
    return time.time_ns() // 1_000_000


class RegistryRefresher:
    """Single-flight background refresh of a registry provider.

    One daemon thread runs refresh() sequentially: every REFRESH seconds after
    a success, RETRY seconds after a failure. The trading cycle never calls
    refresh(); it reads only provider.latest(), so a hung refresh (including
    unbounded DNS) stalls this thread and nothing else.
    """

    def __init__(self, provider, *, refresh_s=REFRESH_SECONDS, retry_s=RETRY_SECONDS,
                 clock_ms=_wall_ms):
        self.provider, self.refresh_s, self.retry_s = provider, refresh_s, retry_s
        self._clock = clock_ms
        self._stop = threading.Event()
        self._start_lock = threading.Lock()
        self.thread = None
        self._in_flight_since = None
        self._attempts = self._failures = self._errors = 0
        self._last_success_ms = self._last_failure_ms = None

    def start(self):
        with self._start_lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._loop, daemon=True,
                                               name="attention-registry-refresh")
                self.thread.start()
        return self.thread

    def close(self):
        self._stop.set()  # never join: a hung refresh must not block exit

    def refresh_once(self) -> bool:
        self._in_flight_since = self._clock()
        self._attempts += 1
        try:
            ok = bool(self.provider.refresh().ok)
        except Exception:
            self._errors += 1
            ok = False
        finally:
            self._in_flight_since = None
        if not ok:
            self._failures += 1
            self._last_failure_ms = self._clock()
        else:
            self._last_success_ms = self._clock()
        return ok

    def _loop(self):
        while not self._stop.is_set():
            ok = self.refresh_once()
            self._stop.wait(self.refresh_s if ok else self.retry_s)

    def health(self) -> dict:
        since = self._in_flight_since
        attempt = self.provider.latest_attempt()
        good = self.provider.latest_provenance()
        return {"thread_alive": bool(self.thread and self.thread.is_alive()),
                "in_flight": since is not None, "in_flight_since_ms": since,
                "attempts": self._attempts, "failures": self._failures,
                "errors": self._errors,
                "last_success_ms": self._last_success_ms,
                "last_failure_ms": self._last_failure_ms,
                "last_attempt_outcome": attempt.outcome if attempt else None,
                "last_attempt_reason": attempt.failure_reason if attempt else None,
                "last_attempt_start_ms": attempt.request_start_ms if attempt else None,
                "snapshot_id": good.snapshot_id if good else None,
                "snapshot_as_of_ms": good.as_of_ms if good else None,
                "environment": self.provider.target.environment}


class RegistryAttention:
    """One registry-selected supplemental slot per cycle, in the locked order:
    snapshot -> durable cursor -> pure select -> atomic selection+cursor
    commit -> fetch -> separate fetch-outcome commit -> result for capture.

    Every failure returns no result and leaves trading alone. A corrupt
    durable cursor disables the slot for the process; it is never repaired.
    """

    def __init__(self, provider, source, journal, *, max_snapshot_age_ms,
                 clock_ms=_wall_ms):
        from trader.observability.supplemental import IsolatedSource
        from trader.data.registry_provider import BinanceUsdmRegistryProvider
        if not isinstance(provider, BinanceUsdmRegistryProvider):
            raise TypeError("registry provider required")
        if not isinstance(source, IsolatedSource):
            raise TypeError("IsolatedSource required")
        target = provider.target
        if target.environment != PRODUCTION:
            raise ValueError("registry Attention observes production Binance USD-M only")
        if source.url != target.base_url + "/fapi/v1/klines":
            raise ValueError("supplemental fetch and registry must share the production venue")
        self.provider, self.source, self.journal = provider, source, journal
        self.max_snapshot_age_ms = max_snapshot_age_ms
        self._clock = clock_ms
        self.disabled = None

    def run(self, scan_symbols, timeframe):
        """(SupplementalResult | None, provenance | None, telemetry dict)."""
        from trader.observability import registry_selector as rs
        from trader.observability import selection_persistence as sp
        from trader.observability import supplemental as S
        tel = {"mode": "registry"}

        def stop(stage, reason):
            tel.update(stage=stage, reason=reason)
            return None, None, tel

        if self.disabled:
            return stop("disabled", self.disabled)
        snapshot = self.provider.latest()                          # 1
        if snapshot is None:
            return stop("snapshot", "no_snapshot")
        tel["snapshot_id"] = snapshot.snapshot_id
        try:
            cursor = sp.load_cursor(self.journal)                  # 2
        except sp.SelectionPersistenceError as exc:
            if exc.reason == sp.CORRUPT_CURSOR:
                self.disabled = sp.CORRUPT_CURSOR
            return stop("cursor", exc.reason)
        except Exception as exc:
            return stop("cursor", f"persistence_error:{type(exc).__name__}")
        cut_ms = self._clock()
        try:
            selection = rs.select(snapshot, cycle_as_of_ms=cut_ms,         # 3
                                  max_snapshot_age_ms=self.max_snapshot_age_ms,
                                  strategy_scan=scan_symbols, cursor_before=cursor)
        except rs.SelectionRefused as exc:
            tel["detail"] = str(exc)
            return stop("select", exc.reason)
        except Exception as exc:
            return stop("select", f"selector_error:{type(exc).__name__}")
        sid = selection.selection_id
        tel.update(selection_id=sid, outcome=selection.outcome,
                   selected_id=selection.selected_id, symbol=selection.selected_symbol,
                   cursor_before=selection.cursor_before, cursor_after=selection.cursor_after)
        try:                                                       # 4-5
            status = sp.record_selection(self.journal, selection, selection_id=sid,
                                         recorded_at_ms=self._clock())
        except sp.SelectionPersistenceError as exc:
            return stop("persist_selection", exc.reason)
        except Exception as exc:
            return stop("persist_selection", f"persistence_error:{type(exc).__name__}")
        if status != sp.INSERTED:
            return stop("persist_selection", status)
        if selection.outcome != rs.SELECTED:
            return stop("select", selection.outcome)
        started = self._clock()                                    # 6
        try:
            try:
                result = S.fetch(self.source, selection.selected_symbol, timeframe, cut_ms)
            finally:
                ended = self._clock()
            fetch_status, reason = result.status, result.reason
        except Exception as exc:
            result, fetch_status, reason = None, S.ERROR, f"fetch_raised:{type(exc).__name__}"
        tel.update(status=fetch_status, fetch_reason=reason,
                   fetch_reason_token=reason_token(reason),
                   started_ms=started, ended_ms=ended)
        try:                                                       # 7
            sp.record_fetch_outcome(self.journal, sid, status=fetch_status,
                                    reason=reason_token(reason), started_ms=started,
                                    ended_ms=max(started, ended), recorded_at_ms=ended)
        except Exception as exc:
            detail = exc.reason if isinstance(exc, sp.SelectionPersistenceError) else type(exc).__name__
            tel["persistence_error"] = str(exc)
            return stop("persist_fetch_outcome", detail)
        if result is None:
            return stop("fetch", reason)
        tel["stage"] = "captured"                                  # 8
        return result, {"mode": "registry", "selection_id": sid,
                        "snapshot_id": snapshot.snapshot_id}, tel
