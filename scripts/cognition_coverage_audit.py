"""Cognition coverage audit — fixed-window repair experiment (offline research).

    ./venv/bin/python -m scripts.cognition_coverage_audit plan \\
        --original-dir /tmp/luffy-history-20260915 --output-dir PATH
    ./venv/bin/python -m scripts.cognition_coverage_audit fetch --audit-dir PATH

Spec: docs/superpowers/specs/2026-09-15-cognition-coverage-audit.md.

`plan` is network-free: it verifies the pinned baseline (byte and canonical
hashes, frozen config, 63/116/940 reconciliation), copies it, and freezes one
bounded request range per affected symbol. `fetch` is the explicit public-network
step: it re-derives and checks the frozen plan, queries only the public Binance
USD-M production klines endpoint (no fallback, no retries, no credentials),
keeps raw evidence, accounts for every missing key exactly once, writes a
standalone research.db and repaired-input.json into a new exclusive `fetch/`
directory, and replays through `trader.cognition.replay.run` only when every
overlap comparison matches.

This tests sensitivity to missing data. It makes no scanner-quality,
tradability, point-in-time availability, significance, edge or profitability
claim. Stdlib plus `trader.cognition` and `scripts.cognition_history` only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shlex
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from scripts.cognition_history import iso, sha256_json, summarize
from trader.cognition.attention import CognitionConfig
from trader.cognition.contracts import INPUT_SCHEMA, TF_MS, load_input
from trader.cognition.replay import dump, run

PLAN_SCHEMA = "cognition.coverage_audit.plan.v1"
MANIFEST_SCHEMA = "cognition.coverage_audit.manifest.v1"
REPO = Path(__file__).resolve().parents[1]
REPO_DATA = REPO / "data"
BASELINE_FILES = ("input.json", "trace.json", "report.json", "manifest.json", "report.md")
PINNED_FILES = ("input.json", "trace.json", "report.json")

VENUE = "binance_usdm_futures_production_public"
BASE_URL = "https://fapi.binance.com"
KLINES_PATH = "/fapi/v1/klines"
KLINES_URL = BASE_URL + KLINES_PATH
ENDPOINT_REFERENCE = [
    "https://github.com/binance/binance-futures-connector-python/blob/main/binance/"
    "um_futures/market.py#L118",
    "https://github.com/binance/binance-futures-connector-python/blob/main/binance/"
    "um_futures/__init__.py#L3"]
SOURCE_TAG = "binance_usdm_public_rest:historical_retrieval"
MAPPING_RULE = "exact plain BASE/USDT -> BASEUSDT; no other mapping, no symbol fallback"
_PLAIN = re.compile(r"([A-Z0-9]+)/USDT")
LIMIT = 100
TIMEOUT_S = 20.0
MIN_INTERVAL_S = 0.3
MAX_RESPONSE_BYTES = 1_000_000
STOP_STATUSES = (418, 429)
KLINE_FIELDS = 12
MAX_ERRORS_KEPT = 20
_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")
KEPT_HEADERS = ("content-type", "content-length", "date", "location", "retry-after")
KEPT_HEADER_PREFIXES = ("x-mbx-used-weight", "x-mbx-order-count")
STATUSES = ("recovered", "absent_in_response", "request_failed", "invalid_response",
            "not_attempted")
OHLCV = ("open", "high", "low", "close", "volume")
RESEARCH_SCHEMA = ("CREATE TABLE candles(symbol TEXT NOT NULL, tf TEXT NOT NULL, "
                   "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, "
                   "volume REAL, taker_buy REAL, PRIMARY KEY(symbol, tf, ts))")

LIMITATIONS = [
    "Sensitivity to missing data only: not scanner quality, historical tradability, "
    "profitability or point-in-time availability.",
    "Baseline candles are from a local store of mixed/unknown provenance; recovered "
    "candles come from Binance USD-M public REST retrieved now, not at the time.",
    "available_ms = close_ms for recovered candles is a retrospective simulation "
    "assumption, not a claim the bars were available historically; actual retrieval "
    "times are in the request ledger.",
    "An absent response is not proof of delisting or a market-calendar exception; "
    "source-calendar explanations need separate evidence and are not inferred.",
    "Only the requested overlap bars are compared; no claim about revisions elsewhere.",
    "No interpolation, aggregation or resampling. Missing bars stay missing.",
    "No significance, predictive, causal, edge, PnL or profitability claim; no tuning "
    "or widening of the evaluation.",
]


class AuditError(ValueError):
    """A clear, user-facing refusal."""


@dataclass(frozen=True)
class Experiment:
    timeframe: str
    warmup_start_ms: int
    start_ms: int
    stop_ms: int
    resolve_until_ms: int
    n_decisions: int
    n_symbols: int
    bars_per_symbol: int
    present_total: int
    missing_total: int
    affected_symbols: int
    files_sha256: dict = field(default_factory=dict)
    max_bars_per_request: int = 25
    max_requests: int = 42


def _utc(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp()) * 1000


PINNED = Experiment(
    timeframe="4h", warmup_start_ms=_utc("2026-08-26T16:00:00Z"),
    start_ms=_utc("2026-08-31T00:00:00Z"), stop_ms=_utc("2026-09-14T00:00:00Z"),
    resolve_until_ms=_utc("2026-09-15T00:00:00Z"), n_decisions=84, n_symbols=63,
    bars_per_symbol=116, present_total=6368, missing_total=940, affected_symbols=42,
    files_sha256={
        "input.json": "910fe6d6aae89977b8569261ba1957885bdae26c86f75fde44a95a85e2bf0f65",
        "trace.json": "9aace3fe8e8e40ecfdebd989768894c527e10690ff558b21885fc349aa749449",
        "report.json": "b804cc66171c9280fdc9a7cfe65679d8d6ec884da762ed39f40322bce3d5d2a7"})


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _reject_constant(name):
    raise ValueError(f"non-finite JSON constant {name}")


def strict_json(data):
    """JSON with NaN/Infinity refused."""
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data, parse_constant=_reject_constant)


def iso_ms(ms: int) -> str:
    return (datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z")


def venue_symbol(symbol) -> str:
    m = _PLAIN.fullmatch(symbol) if isinstance(symbol, str) else None
    if not m:
        raise AuditError(f"symbol {symbol!r} has no exact plain BASE/USDT -> BASEUSDT "
                         "mapping; refusing (no symbol fallback)")
    return m.group(1) + "USDT"


# ---------------------------------------------------------------- destinations

def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def check_destination(dest: Path, original: Path, repo_data: Path = REPO_DATA,
                      must_be_empty: bool = True) -> Path:
    """Symlinks are resolved before every check."""
    out = Path(dest).resolve()
    orig = Path(original).resolve()
    if _overlaps(out, orig):
        raise AuditError(f"destination {out} overlaps the original baseline {orig}")
    if out.is_relative_to(Path(repo_data).resolve()):
        raise AuditError(f"destination {out} is inside repository data/")
    if must_be_empty and out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise AuditError(f"destination exists and is not empty: {out}")
    return out


# ---------------------------------------------------------------- baseline

def grid(exp: Experiment) -> list:
    tf = TF_MS[exp.timeframe]
    return list(range(exp.warmup_start_ms, exp.resolve_until_ms, tf))


def verify_baseline(bdir: Path, exp: Experiment) -> dict:
    """Pinned byte hashes, manifest canonical hashes and config, window, cohort
    and the present/missing key reconciliation. Any drift is a refusal."""
    bdir = Path(bdir)
    if exp.timeframe not in TF_MS:
        raise AuditError(f"timeframe must be one of {sorted(TF_MS)}")
    tf = TF_MS[exp.timeframe]
    files = {}
    for name in BASELINE_FILES:
        p = bdir / name
        if not p.is_file():
            raise AuditError(f"baseline file missing: {p}")
        files[name] = p.read_bytes()
    hashes = {n: sha256_bytes(b) for n, b in files.items()}
    for name in PINNED_FILES:
        if hashes[name] != exp.files_sha256.get(name):
            raise AuditError(f"baseline {name} sha256 {hashes[name]} != pinned "
                             f"{exp.files_sha256.get(name)}")
    try:
        manifest = strict_json(files["manifest.json"])
        raw = strict_json(files["input.json"])
        trace = strict_json(files["trace.json"])
        report = strict_json(files["report.json"])
    except ValueError as e:
        raise AuditError(f"baseline JSON unreadable: {e}") from None
    if manifest.get("files_sha256") != {n: hashes[n] for n in PINNED_FILES}:
        raise AuditError("manifest files_sha256 disagrees with baseline file bytes")
    cfg = asdict(CognitionConfig())
    mh = manifest.get("hashes", {})
    checks = [
        (sha256_json(raw) == mh.get("input_sha256"), "input canonical hash"),
        (sha256_json(trace) == mh.get("trace_sha256"), "trace canonical hash"),
        (manifest.get("config") == cfg, "manifest config != current CognitionConfig defaults"),
        (sha256_json(cfg) == mh.get("config_sha256"), "config canonical hash"),
        (trace.get("config") == cfg, "trace config != current defaults"),
        (trace.get("config_id") == manifest.get("config_id"), "config_id"),
        (report == strict_json(json.dumps(summarize(trace), allow_nan=False)),
         "report.json != summarize(trace)"),
        (manifest.get("end_ms") == exp.resolve_until_ms, "end_ms"),
        (manifest.get("load_rejected") == [] and manifest.get("export_rejected") == [],
         "baseline has rejected rows"),
    ]
    win = manifest.get("window", {})
    decisions = list(range(exp.start_ms, exp.stop_ms, tf))
    checks += [
        (win.get("timeframe") == exp.timeframe and raw.get("timeframe") == exp.timeframe,
         "timeframe"),
        (win.get("tf_ms") == tf, "tf_ms"),
        (win.get("start_ms") == exp.start_ms and win.get("stop_ms") == exp.stop_ms, "window"),
        (win.get("resolve_until_ms") == exp.resolve_until_ms, "resolve_until"),
        (win.get("warmup_start_ms") == exp.warmup_start_ms, "warmup start"),
        (len(decisions) == exp.n_decisions, "decision count"),
        (win.get("decision_times") == decisions == raw.get("decision_times"), "decision times"),
        (raw.get("schema") == INPUT_SCHEMA, "input schema"),
        (raw.get("participation") == [], "participation"),
    ]
    for ok, what in checks:
        if not ok:
            raise AuditError(f"baseline drift: {what}")
    members = [m.get("symbol") for m in raw.get("membership", [])]
    if (members != manifest.get("universe") or len(members) != exp.n_symbols
            or len(set(members)) != len(members)):
        raise AuditError("baseline drift: membership/universe")
    if load_input(raw).rejected:
        raise AuditError("baseline drift: input rows rejected by load_input")
    opens = grid(exp)
    if len(opens) != exp.bars_per_symbol:
        raise AuditError(f"baseline drift: grid has {len(opens)} bars, expected "
                         f"{exp.bars_per_symbol}")
    grid_set, member_set = set(opens), set(members)
    present: dict = {s: {} for s in members}
    for c in raw["candles"]:
        s, o = c["symbol"], c["open_ms"]
        if s not in member_set or o not in grid_set:
            raise AuditError(f"baseline drift: candle {s!r} {o!r} outside the fixed grid")
        if o in present[s]:
            raise AuditError(f"baseline drift: duplicate candle {s} {iso(o)}")
        present[s][o] = c
    missing = [(s, o) for s in sorted(members) for o in opens if o not in present[s]]
    n_present = sum(len(v) for v in present.values())
    per = manifest.get("coverage", {}).get("per_symbol", {})
    for s in members:
        p = per.get(s, {})
        if (p.get("valid_bars") != len(present[s])
                or p.get("missing_bars") != exp.bars_per_symbol - len(present[s])):
            raise AuditError(f"baseline drift: coverage for {s} disagrees with input")
    affected = sorted({s for s, _ in missing})
    counts = {"symbols": len(members), "bars_per_symbol": len(opens),
              "expected_total": len(opens) * len(members), "present_total": n_present,
              "missing_total": len(missing), "affected_symbols": len(affected)}
    want = {"symbols": exp.n_symbols, "bars_per_symbol": exp.bars_per_symbol,
            "expected_total": exp.n_symbols * exp.bars_per_symbol,
            "present_total": exp.present_total, "missing_total": exp.missing_total,
            "affected_symbols": exp.affected_symbols}
    if counts != want or n_present + len(missing) != counts["expected_total"]:
        raise AuditError(f"baseline drift: counts {counts} != fixed {want}")
    return {"files": files, "files_sha256": hashes, "manifest": manifest, "raw": raw,
            "trace": trace, "report": report, "config": cfg, "present": present,
            "missing": missing, "affected": affected, "counts": counts, "tf_ms": tf,
            "canonical": {"input_sha256": mh["input_sha256"],
                          "trace_sha256": mh["trace_sha256"],
                          "config_sha256": mh["config_sha256"]}}


def _request_params(vsym: str, start_ms: int, end_ms: int, exp: Experiment) -> dict:
    return {"symbol": vsym, "interval": exp.timeframe, "startTime": start_ms,
            "endTime": end_ms, "limit": LIMIT}


def build_plan(base: dict, exp: Experiment, original_dir: str) -> dict:
    tf = base["tf_ms"]
    last_open = exp.resolve_until_ms - tf
    by_sym: dict = {}
    for s, o in base["missing"]:
        by_sym.setdefault(s, []).append(o)
    requests = []
    for i, s in enumerate(sorted(by_sym)):
        vsym = venue_symbol(s)
        miss = by_sym[s]
        before = [o for o in base["present"][s] if o < miss[0]]
        if not before:
            raise AuditError(f"baseline drift: {s} has no existing bar before its first "
                             "missing key to use as overlap")
        start = max(before)
        bars = (last_open - start) // tf + 1
        if bars > exp.max_bars_per_request or bars > LIMIT:
            raise AuditError(f"{s}: request range of {bars} bars exceeds "
                             f"{min(exp.max_bars_per_request, LIMIT)}")
        end = last_open + tf - 1
        params = _request_params(vsym, start, end, exp)
        requests.append({
            "index": i, "symbol": s, "venue_symbol": vsym, "venue": VENUE,
            "mapping": MAPPING_RULE, "start_ms": start, "end_ms": end,
            "last_open_ms": last_open, "bars": bars,
            "overlap_opens": sorted(o for o in base["present"][s] if o >= start),
            "missing_opens": miss, "url": KLINES_URL + "?" + urllib.parse.urlencode(params),
            "params": params})
    if len(requests) > exp.max_requests:
        raise AuditError(f"{len(requests)} requests exceeds max_requests={exp.max_requests}")
    return {
        "schema": PLAN_SCHEMA, "experiment": asdict(exp), "original_dir": original_dir,
        "baseline_files_sha256": base["files_sha256"], "baseline_canonical": base["canonical"],
        "config": base["config"], "counts": base["counts"],
        "decision_times": base["raw"]["decision_times"],
        "membership": base["raw"]["membership"],
        "missing_keys": [[s, o] for s, o in base["missing"]],
        "missing_by_symbol": {s: len(v) for s, v in sorted(by_sym.items())},
        "source": {"venue": VENUE, "url": KLINES_URL, "reference": ENDPOINT_REFERENCE,
                   "mapping_rule": MAPPING_RULE, "limit": LIMIT, "timeout_s": TIMEOUT_S,
                   "min_interval_s": MIN_INTERVAL_S, "max_response_bytes": MAX_RESPONSE_BYTES,
                   "retries": 0, "stop_on_status": list(STOP_STATUSES),
                   "credentials": "none; unsigned public GET only"},
        "requests": requests}


def _dump_json(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, allow_nan=False)


def plan(original_dir: Path, output_dir: Path, *, experiment: Experiment = PINNED,
         repo_data: Path = REPO_DATA) -> dict:
    """Network-free. Verify, copy and freeze the fetch plan."""
    orig = Path(original_dir).resolve()
    out = check_destination(output_dir, orig, repo_data)
    base = verify_baseline(orig, experiment)
    p = build_plan(base, experiment, str(orig))
    text = _dump_json(p)
    out.mkdir(parents=True, exist_ok=True)
    (out / "baseline").mkdir()
    for name in BASELINE_FILES:
        with open(out / "baseline" / name, "xb") as f:
            f.write(base["files"][name])
    with open(out / "plan.json", "x") as f:
        f.write(text)
    after = verify_baseline(orig, experiment)                     # originals unchanged
    copies = verify_baseline(out / "baseline", experiment)
    if after["files_sha256"] != base["files_sha256"]:
        raise AuditError("baseline changed during planning")
    if copies["files_sha256"] != base["files_sha256"]:
        raise AuditError("baseline copy differs from original")
    return {"output_dir": str(out), "plan": p}


# ---------------------------------------------------------------- transport

@dataclass
class Response:
    status: int
    headers: dict
    body: bytes
    url: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                                  # 3xx surfaces as HTTPError


def urllib_transport(url: str, timeout: float, max_bytes: int) -> Response:
    """Unsigned GET, no redirects, no environment proxies, bounded read."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    req = urllib.request.Request(url, method="GET",
                                 headers={"Accept": "application/json",
                                          "User-Agent": "cognition-coverage-audit"})
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read(max_bytes + 1) if e.fp is not None else b""
        return Response(e.code, dict(e.headers.items()) if e.headers else {}, body, url)
    with resp:
        return Response(resp.status, dict(resp.headers.items()),
                        resp.read(max_bytes + 1), resp.geturl())


def _kept_headers(headers: dict) -> dict:
    return {k: v for k, v in sorted((str(k).lower(), str(v)) for k, v in headers.items())
            if k in KEPT_HEADERS or k.startswith(KEPT_HEADER_PREFIXES)}


# ---------------------------------------------------------------- validation

def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _number(v):
    """A finite float, else None — including decimal strings or ints that overflow."""
    if isinstance(v, bool):
        return None
    try:
        if isinstance(v, str):
            f = float(v) if _DECIMAL.fullmatch(v) else None
        elif isinstance(v, (int, float)):
            f = float(v)
        else:
            return None
    except OverflowError:
        return None
    return f if f is not None and math.isfinite(f) else None


def validate_klines(body: bytes, rq: dict, tf: int, retrieval_ms: int) -> tuple:
    """Returns (rows by open_ms, errors). Any error fails the whole response."""
    errors: list = []
    try:
        data = strict_json(body)
    except (ValueError, UnicodeDecodeError) as e:
        return {}, [{"row": None, "reason": "unparseable_json", "detail": str(e)[:200]}]
    if not isinstance(data, list):
        return {}, [{"row": None, "reason": "wrong_shape", "detail": type(data).__name__}]
    rows: dict = {}
    for j, row in enumerate(data):
        def bad(reason, detail=None):
            errors.append({"row": j, "reason": reason, "detail": detail})
        if not isinstance(row, list) or len(row) != KLINE_FIELDS:
            bad("wrong_row_shape")
            continue
        o_ms, c_ms = row[0], row[6]
        if not _is_int(o_ms) or o_ms < 0:
            bad("bad_open_time", repr(o_ms))
            continue
        if o_ms % tf:
            bad("misaligned_open_time", o_ms)
            continue
        if not rq["start_ms"] <= o_ms <= rq["last_open_ms"]:
            bad("out_of_range", o_ms)
            continue
        if not _is_int(c_ms) or c_ms != o_ms + tf - 1:
            bad("bad_close_time", repr(c_ms))
            continue
        if o_ms + tf > retrieval_ms:
            bad("not_closed", o_ms)
            continue
        vals = [_number(row[k]) for k in range(1, 6)]
        if any(v is None for v in vals):
            bad("bad_number", [row[k] for k in range(1, 6)])
            continue
        o, h, lo, c, v = vals
        if min(o, h, lo, c) <= 0 or v < 0 or h < max(o, c) or lo > min(o, c):
            bad("inconsistent_ohlcv", vals)
            continue
        if o_ms in rows:
            bad("duplicate_open_time", o_ms)
            continue
        tb = _number(row[9])
        rows[o_ms] = {"open": o, "high": h, "low": lo, "close": c, "volume": v,
                      "taker_buy": tb if tb is not None and tb >= 0 else None}
    return rows, errors


# ---------------------------------------------------------------- fetch

def _load_plan(audit: Path, exp: Experiment) -> tuple:
    try:
        p = strict_json((audit / "plan.json").read_bytes())
    except (OSError, ValueError) as e:
        raise AuditError(f"plan.json unreadable: {e}") from None
    if p.get("schema") != PLAN_SCHEMA or p.get("experiment") != asdict(exp):
        raise AuditError("plan.json schema/experiment disagrees with this experiment")
    base = verify_baseline(audit / "baseline", exp)
    rederived = strict_json(_dump_json(build_plan(base, exp, p.get("original_dir"))))
    if rederived != p:
        raise AuditError("plan.json disagrees with the plan re-derived from the baseline copy")
    for rq in p["requests"]:
        params = _request_params(venue_symbol(rq["symbol"]), rq["start_ms"], rq["end_ms"], exp)
        if (rq["params"] != params or rq["venue_symbol"] != params["symbol"]
                or rq["url"] != KLINES_URL + "?" + urllib.parse.urlencode(params)):
            raise AuditError(f"request {rq['index']} does not target the fixed endpoint")
    return p, base


def _original_check(original_dir: str, exp: Experiment) -> dict:
    p = Path(original_dir)
    if not p.exists():
        return {"path": original_dir, "status": "absent_not_checked"}
    try:
        return {"path": original_dir, "status": "verified",
                "files_sha256": verify_baseline(p, exp)["files_sha256"]}
    except AuditError as e:
        return {"path": original_dir, "status": "drift", "error": str(e)}


def _perform(rq, idx, raw_dir, transport, clock, tf) -> dict:
    rec = {k: rq[k] for k in ("index", "symbol", "venue_symbol", "venue", "mapping", "url",
                              "params", "start_ms", "end_ms", "bars")}
    rec.update(overlap_opens=rq["overlap_opens"], missing_opens=rq["missing_opens"])
    rec["request_utc"] = iso_ms(int(clock() * 1000))
    try:
        resp = transport(rq["url"], TIMEOUT_S, MAX_RESPONSE_BYTES)
    except Exception as e:                    # network error: evidence, not a crash
        rec["retrieval_utc"] = iso_ms(int(clock() * 1000))
        rec.update(outcome="request_failed", http_status=None, headers={},
                   error={"kind": "network_error", "type": type(e).__name__,
                          "detail": str(e)[:500]}, body_file=None, body_sha256=None,
                   body_bytes=0)
        return rec
    retrieval_ms = int(clock() * 1000)
    rec["retrieval_utc"] = iso_ms(retrieval_ms)
    body = bytes(resp.body)
    name = f"{idx:03d}-{rq['venue_symbol']}.body"
    with open(raw_dir / name, "xb") as f:
        f.write(body)
    rec.update(http_status=resp.status, headers=_kept_headers(resp.headers or {}),
               final_url=resp.url, body_file=f"raw/{name}", body_sha256=sha256_bytes(body),
               body_bytes=len(body), error=None)
    if resp.url != rq["url"]:
        rec.update(outcome="request_failed", error={"kind": "redirect_refused",
                                                     "detail": resp.url})
    elif 300 <= resp.status < 400:
        rec.update(outcome="request_failed", error={"kind": "redirect_refused",
                                                     "detail": rec["headers"].get("location")})
    elif resp.status != 200:
        rec.update(outcome="request_failed", error={"kind": "http_error",
                                                     "detail": resp.status})
    elif len(body) > MAX_RESPONSE_BYTES:
        rec.update(outcome="request_failed", error={"kind": "oversized_response",
                                                     "detail": len(body)})
    else:
        rows, errors = validate_klines(body, rq, tf, retrieval_ms)
        if errors:
            rec.update(outcome="invalid_response", validation_error_count=len(errors),
                       validation_errors=errors[:MAX_ERRORS_KEPT])
        else:
            rec.update(outcome="validated", rows_returned=len(rows), empty_response=not rows)
            rec["_rows"] = rows
    return rec


def _compare_overlap(rq, rows, present) -> tuple:
    comparisons, flags = [], []
    for o in rq["overlap_opens"]:
        base = present[rq["symbol"]][o]
        got = rows.get(o)
        if got is None:
            comparisons.append({"open_ms": o, "status": "missing_in_response"})
            flags.append({"symbol": rq["symbol"], "open_ms": o, "kind": "overlap_missing"})
            continue
        diffs = [{"field": k, "baseline": base[k], "venue": got[k]}
                 for k in OHLCV if float(base[k]) != got[k]]
        comparisons.append({"open_ms": o, "status": "mismatch" if diffs else "match",
                            "differences": diffs})
        if diffs:
            flags.append({"symbol": rq["symbol"], "open_ms": o, "kind": "overlap_mismatch",
                          "differences": diffs})
    return comparisons, flags


def write_research_db(path: Path, raw: dict, recovered: dict, timeframe: str) -> int:
    if path.exists():
        raise AuditError(f"refusing to overwrite {path}")
    conn = sqlite3.connect(path)
    try:
        conn.execute(RESEARCH_SCHEMA)
        conn.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,NULL)",
                         [(c["symbol"], timeframe, c["open_ms"], c["open"], c["high"],
                           c["low"], c["close"], c["volume"]) for c in raw["candles"]])
        conn.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                         [(s, timeframe, o, r["open"], r["high"], r["low"], r["close"],
                           r["volume"], r["taker_buy"])
                          for (s, o), r in sorted(recovered.items())])
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    finally:
        conn.close()


def build_repaired(raw: dict, recovered: dict, tf: int, complete: bool) -> dict:
    rep = json.loads(json.dumps(raw, allow_nan=False))
    for (s, o), r in sorted(recovered.items()):
        rep["candles"].append({"symbol": s, "open_ms": o, "open": r["open"], "high": r["high"],
                               "low": r["low"], "close": r["close"], "volume": r["volume"],
                               "closed": True, "available_ms": o + tf, "source": SOURCE_TAG})
    rep["metadata"]["coverage_audit"] = {
        "kind": "coverage_repair_sensitivity",
        "original_candle_provenance": "mixed/unknown: local candle store, retrospective "
                                      "reconstruction (see original candle source labels)",
        "recovered_candle_provenance": f"{VENUE} {KLINES_URL}, retrieved during this audit; "
                                       "actual retrieval times in fetch/requests.json",
        "recovered_source_tag": SOURCE_TAG, "recovered_candles": len(recovered),
        "complete_recovery": complete,
        "availability_assumption": "available_ms = close_ms for recovered candles is a "
                                   "retrospective simulation assumption, NOT a claim the "
                                   "bars were available historically.",
        "membership": "unchanged original assumed static research cohort"}
    return rep


def verify_repaired(rep: dict, raw: dict, recovered: dict, missing: set, tf: int) -> None:
    n = len(raw["candles"])
    checks = [
        (rep["membership"] == raw["membership"], "membership"),
        (rep["participation"] == raw["participation"], "participation"),
        (rep["decision_times"] == raw["decision_times"], "decision times"),
        (rep["timeframe"] == raw["timeframe"] and rep["schema"] == raw["schema"], "timeframe"),
        (rep["candles"][:n] == raw["candles"], "baseline candles preserved"),
        ({k: v for k, v in rep["metadata"].items() if k != "coverage_audit"}
         == raw["metadata"], "metadata preserved"),
    ]
    added = rep["candles"][n:]
    keys = [(c["symbol"], c["open_ms"]) for c in added]
    checks += [
        (len(keys) == len(set(keys)) and set(keys) == set(recovered), "recovered keys"),
        (set(keys) <= missing, "recovered keys within the missing inventory"),
        (all(c["source"] == SOURCE_TAG and c["available_ms"] == c["open_ms"] + tf
             for c in added), "recovered tags"),
        (not load_input(rep).rejected, "repaired input rejected rows"),
    ]
    for ok, what in checks:
        if not ok:
            raise AuditError(f"repaired input check failed: {what}")


def _trace_view(trace: dict) -> dict:
    rows = {}
    for d in trace["decisions"]:
        for r in d["universe"]:
            rows[(d["as_of_ms"], r["symbol"])] = {
                "status": r["status"], "eligible": r["eligible"],
                "selected": bool(r.get("selected")), "reason": r.get("reason")}
    unresolved = Counter(f"{o['subject_kind']}:{o['measurement'].get('reason')}"
                         for o in trace["outcomes"] if o["status"] == "unresolved")
    return {"rows": rows,
            "selected": sorted((d["as_of_ms"], s) for d in trace["decisions"]
                               for s in d["selected"]),
            "episodes": sum(len(d["episodes"]) for d in trace["decisions"]),
            "unresolved": dict(sorted(unresolved.items()))}


def _grid_coverage(raw: dict, exp: Experiment) -> dict:
    opens = set(grid(exp))
    per = Counter()
    for c in raw["candles"]:
        if c["open_ms"] in opens:
            per[c["symbol"]] += 1
    syms = [m["symbol"] for m in raw["membership"]]
    total = exp.bars_per_symbol * len(syms)
    present = sum(per[s] for s in syms)
    return {"expected_total": total, "present_total": present, "missing_total": total - present,
            "missing_by_symbol": {s: exp.bars_per_symbol - per[s] for s in sorted(syms)
                                  if exp.bars_per_symbol - per[s]}}


def compare(raw, trace, rep, rep_trace, exp: Experiment) -> dict:
    a, b = _trace_view(trace), _trace_view(rep_trace)
    sa, sb = summarize(trace), summarize(rep_trace)
    changed = [{"as_of_ms": t, "symbol": s, "original": a["rows"].get((t, s)),
                "repaired": b["rows"].get((t, s))}
               for t, s in sorted(set(a["rows"]) | set(b["rows"]))
               if a["rows"].get((t, s)) != b["rows"].get((t, s))]
    return {
        "coverage": {"original": _grid_coverage(raw, exp), "repaired": _grid_coverage(rep, exp)},
        "row_status": {"original": sa["row_status"], "repaired": sb["row_status"]},
        "eligible": {"original": sa["eligible"], "repaired": sb["eligible"]},
        "stale": {"original": sa["row_status"].get("stale", 0),
                  "repaired": sb["row_status"].get("stale", 0)},
        "selected": {"original": len(a["selected"]), "repaired": len(b["selected"]),
                     "only_original": [list(x) for x in sorted(set(a["selected"]) - set(b["selected"]))],
                     "only_repaired": [list(x) for x in sorted(set(b["selected"]) - set(a["selected"]))]},
        "episodes": {"original": a["episodes"], "repaired": b["episodes"]},
        "unresolved_reasons": {"original": a["unresolved"], "repaired": b["unresolved"]},
        "baselines": {"original": sa["baselines"], "repaired": sb["baselines"]},
        "row_changes_by_decision_and_symbol": changed,
        "descriptive_only": "Descriptive comparison of one sensitivity replay. No "
                            "significance, predictive, causal, edge or PnL claim."}


def fetch(audit_dir: Path, *, experiment: Experiment = PINNED, transport=None,
          clock=time.time, sleep=time.sleep, repo_data: Path = REPO_DATA,
          command: str = "") -> dict:
    exp = experiment
    audit = Path(audit_dir).resolve()
    p, base = _load_plan(audit, exp)
    check_destination(audit, Path(p["original_dir"]), repo_data, must_be_empty=False)
    tf = base["tf_ms"]
    if clock() * 1000 < exp.resolve_until_ms:
        raise AuditError("clock precedes resolve_until; requested bars cannot all be closed")
    original_before = _original_check(p["original_dir"], exp)
    if original_before["status"] != "verified":
        raise AuditError(f"original baseline not verified ({original_before['status']}): "
                         f"{original_before.get('error', original_before['path'])}")
    if original_before["files_sha256"] != base["files_sha256"]:
        raise AuditError("original baseline drifted: file bytes differ from the frozen copy")
    transport = transport or urllib_transport
    fdir = audit / "fetch"
    try:
        fdir.mkdir()
    except FileExistsError:
        raise AuditError(f"{fdir} exists: never overwrite a prior attempt; plan afresh "
                         "into a new directory to retry") from None
    raw_dir = fdir / "raw"
    raw_dir.mkdir()

    records, status, flags, recovered = [], {}, [], {}
    stopped, last_start = None, None
    for rq in p["requests"]:
        keys = [(rq["symbol"], o) for o in rq["missing_opens"]]
        if stopped is not None or rq["index"] >= exp.max_requests:
            why = stopped or "max_requests"
            records.append({"index": rq["index"], "symbol": rq["symbol"],
                            "venue_symbol": rq["venue_symbol"], "url": rq["url"],
                            "params": rq["params"], "outcome": "not_attempted",
                            "reason": why})
            status.update({k: ("not_attempted", rq["index"], why) for k in keys})
            continue
        if last_start is not None:
            wait = MIN_INTERVAL_S - (clock() - last_start)
            if wait > 0:
                sleep(wait)
        last_start = clock()
        rec = _perform(rq, rq["index"], raw_dir, transport, clock, tf)
        rows = rec.pop("_rows", None)
        if rec["outcome"] == "validated":
            got = [k for k in keys if k[1] in rows]
            rec["recovered_keys"], rec["absent_keys"] = len(got), len(keys) - len(got)
            for k in keys:
                if k[1] in rows:
                    status[k] = ("recovered", rq["index"], None)
                    recovered[k] = rows[k[1]]
                else:
                    status[k] = ("absent_in_response", rq["index"],
                                 "empty_response" if not rows else "not_in_response")
            rec["overlap"], new_flags = _compare_overlap(rq, rows, base["present"])
            flags += new_flags
        else:
            status.update({k: (rec["outcome"], rq["index"], (rec.get("error") or {}).get("kind")
                               or "validation_failed") for k in keys})
            if rec.get("http_status") in STOP_STATUSES:
                stopped = f"stopped_after_http_{rec['http_status']}"
        records.append(rec)

    # Request attribution for the raw evidence is persisted before any later step
    # (ledger, research.db, replay) can fail.
    with open(fdir / "requests.json", "x") as f:
        f.write(_dump_json(records))
    missing = [tuple(k) for k in p["missing_keys"]]
    ledger = [{"symbol": s, "open_ms": o, "open_utc": iso(o), "status": status[(s, o)][0],
               "request_index": status[(s, o)][1], "detail": status[(s, o)][2]}
              for s, o in missing]
    counts = Counter(r["status"] for r in ledger)
    counts = {k: counts.get(k, 0) for k in STATUSES}
    if len(ledger) != exp.missing_total or sum(counts.values()) != exp.missing_total:
        raise AuditError("ledger accounting failed")
    complete = counts["recovered"] == exp.missing_total

    with open(fdir / "ledger.json", "x") as f:
        f.write(_dump_json(ledger))

    texts = {}
    db_rows = write_research_db(fdir / "research.db", base["raw"], recovered, exp.timeframe)
    repaired = build_repaired(base["raw"], recovered, tf, complete)
    verify_repaired(repaired, base["raw"], recovered, set(missing), tf)
    rep_text = json.dumps(repaired, indent=1, sort_keys=True, allow_nan=False)
    texts["repaired-input.json"] = rep_text

    cfg = CognitionConfig()
    if asdict(cfg) != base["config"]:
        raise AuditError("config drift before replay")
    replay = {"performed": False, "withheld_reasons": [], "complete_recovery": complete,
              "label": "complete" if complete else "incomplete: remaining keys stay missing",
              "end_ms": exp.resolve_until_ms, "config_id": base["trace"]["config_id"]}
    if flags:
        replay["withheld_reasons"] = sorted({f"{f['kind']}:{f['symbol']}" for f in flags})
    else:
        base_text = dump(run(json.loads(base["files"]["input.json"]), cfg, exp.resolve_until_ms))
        replay["baseline_rerun_identical"] = base_text == base["files"]["trace.json"].decode()
        if not replay["baseline_rerun_identical"]:
            raise AuditError("baseline replay no longer reproduces trace.json; refusing")
        t1 = run(json.loads(rep_text), cfg, exp.resolve_until_ms)
        t2 = run(json.loads(rep_text), cfg, exp.resolve_until_ms)
        trace_text = dump(t1)
        if dump(t2) != trace_text:
            raise AuditError("repaired replay is not deterministic")
        comparison = compare(base["raw"], base["trace"], repaired, t1, exp)
        replay.update(performed=True, rerun_identical=True,
                      repaired_trace_sha256=sha256_json(t1))
        texts["repaired-trace.json"] = trace_text
        texts["repaired-report.json"] = _dump_json(summarize(t1))
        texts["comparison.json"] = _dump_json(comparison)
    for name, text in texts.items():
        with open(fdir / name, "x") as f:
            f.write(text)

    original_after = _original_check(p["original_dir"], exp)
    copies_after = _original_check(str(audit / "baseline"), exp).get("files_sha256")
    preserved = (original_after["status"] == "verified"
                 and original_after["files_sha256"] == base["files_sha256"]
                 and copies_after == base["files_sha256"])
    manifest = {
        "schema": MANIFEST_SCHEMA, "kind": "coverage_repair_sensitivity", "command": command,
        "experiment": asdict(exp), "plan_sha256": sha256_bytes((audit / "plan.json").read_bytes()),
        "source": p["source"], "source_tag": SOURCE_TAG,
        "baseline": {"copy_files_sha256_before": base["files_sha256"],
                     "copy_files_sha256_after": copies_after,
                     "canonical": base["canonical"], "original_before": original_before,
                     "original_after": original_after,
                     "preserved": preserved},
        "requests": {"planned": len(p["requests"]),
                     "attempted": sum(1 for r in records if r["outcome"] != "not_attempted"),
                     "outcomes": dict(sorted(Counter(r["outcome"] for r in records).items())),
                     "stopped": stopped},
        "ledger_counts": counts, "ledger_total": len(ledger),
        "recovered_plus_remaining": counts["recovered"] + sum(
            v for k, v in counts.items() if k != "recovered"),
        "review_flags": flags, "replay": replay,
        "research_db": {"rows": db_rows, "baseline_rows": len(base["raw"]["candles"]),
                        "recovered_rows": len(recovered), "schema": RESEARCH_SCHEMA,
                        "taker_buy": "NULL for baseline rows (unknown)"},
        "scope_checks": {"membership_unchanged": True, "decision_times_unchanged": True,
                         "participation_unchanged": True, "config_unchanged": True,
                         "baseline_candles_preserved": True, "baseline_replacements": 0,
                         "recovered_within_missing_inventory": True},
        "limitations": LIMITATIONS,
        "manifest_self_hash": "not recorded; an independent review may hash manifest.json"}
    with open(fdir / "report.md", "x") as f:
        f.write(render_report(manifest, ledger))
    manifest["files_sha256"] = {str(q.relative_to(fdir)): sha256_bytes(q.read_bytes())
                                for q in sorted(fdir.rglob("*")) if q.is_file()}
    with open(fdir / "manifest.json", "x") as f:
        f.write(_dump_json(manifest))
    if not preserved:
        raise AuditError(f"baseline not preserved (original after: "
                         f"{original_after['status']}); evidence kept in {fdir}")
    return {"fetch_dir": str(fdir), "manifest": manifest, "ledger": ledger}


def render_report(m: dict, ledger: list) -> str:
    r = m["replay"]
    lines = ["# Cognition coverage audit — fetch report", "",
             "Sensitivity to missing data only. No tradability, availability, significance, "
             "edge or profitability claim.", "",
             "## Source", "", f"- venue `{m['source']['venue']}`, endpoint `{m['source']['url']}`",
             f"- mapping: {m['source']['mapping_rule']}; source tag `{m['source_tag']}`",
             f"- requests planned {m['requests']['planned']}, attempted "
             f"{m['requests']['attempted']}, outcomes {m['requests']['outcomes']}, stopped "
             f"`{m['requests']['stopped']}`", "",
             "## Ledger (every missing key exactly once)", "",
             f"- total {m['ledger_total']}: {m['ledger_counts']}",
             f"- recovered + remaining = {m['recovered_plus_remaining']}", "",
             "| symbol | status | keys |", "|---|---|---|"]
    per = Counter((x["symbol"], x["status"]) for x in ledger)
    lines += [f"| {s} | {st} | {n} |" for (s, st), n in sorted(per.items())]
    lines += ["", "## Overlap review flags", ""]
    lines += [f"- {f['kind']} {f['symbol']} {iso(f['open_ms'])} {f.get('differences', '')}"
              for f in m["review_flags"]] or ["- none"]
    lines += ["", "## Replay", "", f"- performed `{r['performed']}`; {r['label']}"]
    if r["withheld_reasons"]:
        lines.append(f"- WITHHELD: {r['withheld_reasons']}")
    lines += ["", "## Preservation", "",
              f"- baseline preserved `{m['baseline']['preserved']}`; original after: "
              f"`{m['baseline']['original_after']['status']}`", "",
              "## Limitations", ""] + [f"- {x}" for x in m["limitations"]] + [""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.cognition_coverage_audit",
                                 description="Fixed-window cognition coverage repair audit.")
    sub = ap.add_subparsers(dest="stage", required=True)
    pp = sub.add_parser("plan", help="network-free verification and frozen fetch plan")
    pp.add_argument("--original-dir", required=True, type=Path)
    pp.add_argument("--output-dir", required=True, type=Path)
    fp = sub.add_parser("fetch", help="EXPLICIT public-network fetch of the frozen plan")
    fp.add_argument("--audit-dir", required=True, type=Path)
    args = ap.parse_args(argv)
    command = "./venv/bin/python -m scripts.cognition_coverage_audit " + shlex.join(
        sys.argv[1:] if argv is None else argv)
    try:
        if args.stage == "plan":
            res = plan(args.original_dir, args.output_dir)
            c = res["plan"]["counts"]
            print(f"missing={c['missing_total']} symbols={c['affected_symbols']} "
                  f"requests={len(res['plan']['requests'])} -> {res['output_dir']}")
        else:
            res = fetch(args.audit_dir, command=command)
            m = res["manifest"]
            print(f"ledger={m['ledger_counts']} flags={len(m['review_flags'])} "
                  f"replay={m['replay']['performed']} -> {res['fetch_dir']}")
    except (AuditError, OSError, sqlite3.Error) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
