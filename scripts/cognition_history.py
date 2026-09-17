"""Retrospective historical replay of the offline cognition loop.

    ./venv/bin/python -m scripts.cognition_history --db PATH --timeframe 4h \\
        --start 2026-08-31T00:00:00Z --stop 2026-09-14T00:00:00Z \\
        --resolve-until 2026-09-15T00:00:00Z --output-dir /tmp/luffy-history-20260915

Reads a local candle store (read-only, one snapshot), exports a
`cognition.input.v1` fixture, runs `trader.cognition.replay.run` on it with the
frozen default `CognitionConfig`, and writes input.json, trace.json,
report.json, manifest.json and report.md into an empty output directory.

This is a RETROSPECTIVE RECONSTRUCTION, not a verified point-in-time record.
The store keeps no arrival times, revisions, eligibility history or per-row
venue, so every candle is exported with available_ms = close_ms (an assumed
zero arrival lag), and membership is an assumed static research cohort chosen
only from data closed by `start`. Nothing here trades, and no significance,
predictive, causal, edge or profitability claim is made.

Stdlib plus `trader.cognition` only. It lives outside `trader/cognition` so
that package keeps its no-sqlite import isolation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sqlite3
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from trader.cognition.attention import CognitionConfig
from trader.cognition.contracts import INPUT_SCHEMA, TF_MS, load_input
from trader.cognition.replay import dump, run

MANIFEST_SCHEMA = "cognition.history.manifest.v1"
REPORT_SCHEMA = "cognition.history.report.v1"
MAX_DECISIONS = 500
MAX_SYMBOLS = 100
MAX_ROWS = 100_000
REQUIRED_COLUMNS = ("symbol", "tf", "ts", "open", "high", "low", "close", "volume")
TS_FORMAT = "YYYY-MM-DDTHH:MM:SSZ"
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
OUTPUT_FILES = ("input.json", "trace.json", "report.json", "manifest.json", "report.md")

UNIVERSE_RULE = (
    "All distinct stored symbol keys for the timeframe with at least one candle whose "
    "open ts is in [start - (window+short+1)*tf, start), i.e. fully closed by start. "
    "Chosen only from pre-start rows, sorted, frozen from start through resolve_until. "
    "No survival, coverage or forward-return filter. Assumed static research cohort, "
    "NOT historical tradability or index membership.")
ASSUMPTIONS = [
    "available_ms = close_ms for every candle: an assumed zero arrival lag; the store "
    "records no arrival time.",
    "The store records no revisions: each (symbol, tf, ts) has one row as it is now.",
    "Membership is an assumed static research cohort, not historical tradability.",
    "Venue/source per row is unknown; candles.db is a local store of mixed or "
    "unknown provenance.",
    "Symbol keys are preserved as stored; possible aliases are disclosed, not merged.",
    "Rows may reflect storage selection or survivorship (what was ever fetched/kept).",
    "Missing bars are missing: no interpolation, resampling or fill-in.",
    "No participation series are exported (taker_buy/OI not used).",
]
NO_TRADE_SCOPE = ("Offline research artefact only. Nothing here places orders, admits "
                  "strategies, writes to production stores or changes runtime state. "
                  "No significance, predictive, causal, edge, PnL or profitability claim.")


class HistoryError(ValueError):
    """A clear, user-facing refusal (bad bounds, schema, caps, output dir)."""


def parse_utc(text: str, name: str) -> int:
    """Exact `YYYY-MM-DDTHH:MM:SSZ` only; naive or offset forms are refused."""
    if not isinstance(text, str) or not _TS_RE.fullmatch(text):
        raise HistoryError(f"{name} must be UTC in the exact form {TS_FORMAT}, got {text!r}")
    try:
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise HistoryError(f"{name}: {e}") from None
    return int(dt.timestamp()) * 1000


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_json(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def connect_readonly(db: Path) -> sqlite3.Connection:
    """Read-only URI connection with query_only on. Never creates a missing file
    and never uses immutable=1 (the store may be live)."""
    path = Path(db).resolve()
    if not path.is_file():
        raise HistoryError(f"database not found: {path}")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def plan_window(timeframe: str, start_ms: int, stop_ms: int, resolve_ms: int,
                cfg: CognitionConfig, now_ms: int, allow_incomplete: bool,
                max_decisions: int) -> dict:
    if timeframe not in TF_MS:
        raise HistoryError(f"timeframe must be one of {sorted(TF_MS)}")
    tf = TF_MS[timeframe]
    for name, v in (("start", start_ms), ("stop", stop_ms)):
        if v % tf:
            raise HistoryError(f"{name} {iso(v)} is not aligned to the {timeframe} grid")
    if stop_ms <= start_ms:
        raise HistoryError("stop must be after start")
    if resolve_ms < stop_ms:
        raise HistoryError("resolve_until must be >= stop")
    if resolve_ms > now_ms:
        raise HistoryError("resolve_until is in the future; its bars cannot be closed")
    decisions = list(range(start_ms, stop_ms, tf))
    if len(decisions) > max_decisions:
        raise HistoryError(f"{len(decisions)} decisions exceeds max_decisions={max_decisions}")
    last_deadline = decisions[-1] + cfg.horizon * tf
    if resolve_ms < last_deadline and not allow_incomplete:
        raise HistoryError(f"resolve_until {iso(resolve_ms)} precedes the last decision "
                           f"deadline {iso(last_deadline)}; pass --allow-incomplete to "
                           "accept unresolved outcomes")
    warmup = cfg.window + cfg.short + 1
    return {"timeframe": timeframe, "tf_ms": tf, "start_ms": start_ms, "stop_ms": stop_ms,
            "resolve_until_ms": resolve_ms, "decision_times": decisions,
            "warmup_bars": warmup, "warmup_start_ms": start_ms - warmup * tf,
            "last_decision_deadline_ms": last_deadline,
            "complete_horizon": resolve_ms >= last_deadline}


def read_store(db: Path, plan: dict, max_symbols: int, max_rows: int) -> dict:
    """One read transaction: schema check, universe, bounded row fetch. The
    connection is closed before anything is replayed."""
    conn = connect_readonly(db)
    try:
        conn.execute("BEGIN")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(candles)")}
        if not cols:
            raise HistoryError("table 'candles' not found")
        missing = [c for c in REQUIRED_COLUMNS if c not in cols]
        if missing:
            raise HistoryError(f"table 'candles' lacks columns {missing}")
        tf, tf_ms = plan["timeframe"], plan["tf_ms"]
        w0, start, resolve = plan["warmup_start_ms"], plan["start_ms"], plan["resolve_until_ms"]
        universe_sql = ("SELECT DISTINCT symbol FROM candles WHERE tf = ? AND ts >= ? "
                        "AND ts < ? AND typeof(symbol) = 'text' AND symbol <> '' "
                        "ORDER BY symbol")
        universe = [r[0] for r in conn.execute(universe_sql, (tf, w0, start))]
        if not universe:
            raise HistoryError(f"no stored {tf} history in the warmup interval "
                               f"[{iso(w0)}, {iso(start)})")
        if len(universe) > max_symbols:
            raise HistoryError(f"{len(universe)} symbols exceeds max_symbols={max_symbols}")
        # Last row must close by resolve_until: ts + tf <= resolve.
        where = (f"tf = ? AND symbol IN ({','.join('?' * len(universe))}) "
                 "AND ts >= ? AND ts <= ?")
        params = (tf, *universe, w0, resolve - tf_ms)
        n_rows = conn.execute(f"SELECT COUNT(*) FROM candles WHERE {where}", params).fetchone()[0]
        if n_rows > max_rows:
            raise HistoryError(f"{n_rows} rows exceeds max_rows={max_rows}")
        rows_sql = (f"SELECT symbol, ts, open, high, low, close, volume FROM candles "
                    f"WHERE {where} ORDER BY symbol, ts")
        rows = conn.execute(rows_sql, params).fetchall()
        later_sql = ("SELECT COUNT(DISTINCT symbol) FROM candles WHERE tf = ? AND ts >= ? "
                     "AND ts <= ? AND symbol NOT IN (SELECT symbol FROM candles "
                     "WHERE tf = ? AND ts >= ? AND ts < ? AND symbol IS NOT NULL)")
        later = conn.execute(later_sql, (tf, start, resolve - tf_ms, tf, w0, start)).fetchone()[0]
        schema_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                                  "AND name='candles'").fetchone()[0]
        conn.rollback()
    finally:
        conn.close()
    return {"universe": universe, "rows": rows, "schema_sql": schema_sql,
            "excluded_post_start_symbols": later,
            "queries": {"universe": universe_sql, "rows": rows_sql, "count": where,
                        "post_start_symbols": later_sql,
                        "params": {"tf": tf, "warmup_start_ms": w0, "start_ms": start,
                                   "last_open_ms": resolve - tf_ms}}}


def _json_safe(v):
    """Keep a stored value for load_input to judge; only values JSON cannot
    carry (inf, bytes) are refused here, with a reason."""
    if isinstance(v, float) and v != v or v in (float("inf"), float("-inf")):
        return False
    return not isinstance(v, bytes)


def possible_aliases(symbols: list) -> list:
    groups: dict = {}
    for s in symbols:
        key = re.sub(r"[^A-Z0-9]", "", str(s).split(":")[0].upper())
        groups.setdefault(key, []).append(s)
    return [sorted(g) for _, g in sorted(groups.items()) if len(g) > 1]


def build_input(store: dict, plan: dict, source: str) -> tuple:
    tf = plan["tf_ms"]
    candles, export_rejected = [], []
    for sym, ts, o, h, lo, c, v in store["rows"]:
        if not all(_json_safe(x) for x in (sym, ts, o, h, lo, c, v)):
            export_rejected.append({"symbol": repr(sym), "ts": repr(ts),
                                    "reason": "not_json_representable"})
            continue
        row = {"symbol": sym, "open_ms": ts, "open": o, "high": h, "low": lo,
               "close": c, "volume": v, "closed": True, "source": source}
        if isinstance(ts, int) and not isinstance(ts, bool):
            row["available_ms"] = ts + tf          # assumed zero arrival lag
        candles.append(row)
    membership = [{"symbol": s, "from_ms": plan["start_ms"], "to_ms": None,
                   "available_ms": plan["start_ms"],
                   "source": "assumed_static_research_cohort"} for s in store["universe"]]
    raw = {"schema": INPUT_SCHEMA, "timeframe": plan["timeframe"],
           "decision_times": plan["decision_times"], "membership": membership,
           "candles": candles, "participation": [],
           "metadata": {"kind": "retrospective_reconstruction",
                        "universe_rule": UNIVERSE_RULE, "assumptions": ASSUMPTIONS,
                        "candle_source": source}}
    return raw, export_rejected


def coverage(raw: dict, plan: dict, universe: list, rejected: list) -> dict:
    tf, w0, resolve = plan["tf_ms"], plan["warmup_start_ms"], plan["resolve_until_ms"]
    expected = (resolve - w0) // tf
    bad = Counter()
    for r in rejected:
        if r["section"] == "candles":
            bad[raw["candles"][r["index"]]["symbol"]] += 1
    good: dict = {s: set() for s in universe}
    idx = {r["index"] for r in rejected if r["section"] == "candles"}
    for i, c in enumerate(raw["candles"]):
        if i not in idx:
            good.setdefault(c["symbol"], set()).add(c["open_ms"])
    per = {}
    for s in universe:
        opens = sorted(good[s])
        per[s] = {"expected_bars": expected, "valid_bars": len(opens),
                  "missing_bars": expected - len(opens), "rejected_rows": bad[s],
                  "first_open_ms": opens[0] if opens else None,
                  "last_open_ms": opens[-1] if opens else None}
    total_valid = sum(p["valid_bars"] for p in per.values())
    return {"expected_bars_per_symbol": expected,
            "expected_bars_total": expected * len(universe),
            "valid_bars_total": total_valid,
            "missing_bars_total": expected * len(universe) - total_valid,
            "symbols_with_missing_bars": sum(1 for p in per.values() if p["missing_bars"]),
            "rejected_rows_total": sum(bad.values()), "per_symbol": per}


def _describe(values: list) -> dict:
    vals = [v for v in values if v is not None]
    return {"n": len(vals), "mean": statistics.fmean(vals) if vals else None,
            "median": statistics.median(vals) if vals else None}


def summarize(trace: dict) -> dict:
    decisions = trace["decisions"]
    counts = Counter()
    reasons, statuses = Counter(), Counter()
    per_decision_selected = []
    sample_selected, hyp_template = {}, {}
    framing, framing_reason = Counter(), Counter()
    for d in decisions:
        for row in d["universe"]:
            counts["universe_rows"] += 1
            statuses[row["status"]] += 1
            counts["eligible" if row["eligible"] else "ineligible"] += 1
            if row.get("selected"):
                counts["selected"] += 1
            else:
                reasons[row["reason"]] += 1
                if row["eligible"]:
                    counts["eligible_unselected"] += 1
        per_decision_selected.append(len(d["selected"]))
        counts["decisions_selecting_nothing"] += not d["selected"]
        counts["cohort_ok_decisions"] += d["cohort_ok"]
        for s in d["baseline_samples"]:
            sample_selected[s["sample_id"]] = s["selected"]
        for ep in d["episodes"]:
            framing[ep["framing"]] += 1
            framing_reason[ep["framing_reason"]] += 1
            for h in ep["hypotheses"]:
                hyp_template[h["hyp_id"]] = h["template"]

    hyp_status: dict = {}
    buckets: dict = {}
    for o in trace["outcomes"]:
        m = o["measurement"]
        if o["subject_kind"] == "hypothesis":
            t = hyp_template[o["subject_id"]]
            hyp_status.setdefault(t, Counter())[o["status"]] += 1
            continue
        kind = o["subject_kind"].split("_")[1]
        sel = "selected" if sample_selected[o["subject_id"].rsplit(":", 1)[0]] else "unselected"
        b = buckets.setdefault((kind, sel), {"status": Counter(), "unresolved_reason": Counter(),
                                             "a": [], "b": []})
        b["status"][o["status"]] += 1
        if o["status"] == "unresolved":
            b["unresolved_reason"][m.get("reason")] += 1
        if o["status"] != "measured":
            continue
        if kind == "raw":
            b["a"].append(m["abs_forward_z"])
            b["b"].append(abs(m["forward_log_return"]))
        else:
            b["a"].append(None if m["rel_forward_z"] is None else abs(m["rel_forward_z"]))
            b["b"].append(abs(m["forward_log_return"] - m["cohort_median_forward"]))
    baselines = {}
    for kind, a_name, b_name in (("raw", "abs_forward_z", "abs_forward_log_return"),
                                 ("relative", "abs_rel_forward_z",
                                  "abs_forward_log_return_minus_cohort_median")):
        for sel in ("selected", "unselected"):
            b = buckets.get((kind, sel), {"status": Counter(), "unresolved_reason": Counter(),
                                          "a": [], "b": []})
            n = sum(b["status"].values())
            baselines[f"{kind}:{sel}"] = {
                "samples": n, "status": dict(sorted(b["status"].items())),
                "unresolved_reason": dict(sorted(b["unresolved_reason"].items())),
                "unresolved_fraction": (b["status"]["unresolved"] / n) if n else None,
                a_name: _describe(b["a"]), b_name: _describe(b["b"])}
    outcome_status = Counter(o["status"] for o in trace["outcomes"])
    return {
        "schema": REPORT_SCHEMA, "decisions": len(decisions),
        "universe_rows": counts["universe_rows"], "eligible": counts["eligible"],
        "ineligible": counts["ineligible"], "selected": counts["selected"],
        "eligible_unselected": counts["eligible_unselected"],
        "decisions_selecting_nothing": counts["decisions_selecting_nothing"],
        "cohort_ok_decisions": counts["cohort_ok_decisions"],
        "selected_per_decision": dict(sorted(Counter(per_decision_selected).items())),
        "row_status": dict(sorted(statuses.items())),
        "omission_reason": dict(sorted(reasons.items())),
        "episodes": sum(framing.values()), "framing": dict(sorted(framing.items())),
        "framing_reason": dict(sorted(framing_reason.items())),
        "hypothesis_status": {t: dict(sorted(c.items())) for t, c in sorted(hyp_status.items())},
        "outcomes": len(trace["outcomes"]), "outcome_status": dict(sorted(outcome_status.items())),
        "baselines": baselines, "rejected_inputs": len(trace["rejected_inputs"]),
        "descriptive_only": "Means/medians are descriptive over resolved samples. "
                            "No significance, predictive, causal, edge or PnL claim.",
        "no_trade_scope": NO_TRADE_SCOPE}


def _fmt(v):
    return "—" if v is None else f"{v:.4g}" if isinstance(v, float) else str(v)


def render_markdown(manifest: dict, report: dict) -> str:
    p, cov = manifest["window"], manifest["coverage"]
    lines = [
        "# Cognition historical replay — generated report", "",
        "Retrospective reconstruction from a local candle store. **Not** a verified "
        "point-in-time record. " + NO_TRADE_SCOPE, "",
        "## Command", "", "```bash", manifest["command"], "```", "",
        "## Identity", "",
        f"- input sha256 (canonical): `{manifest['hashes']['input_sha256']}`",
        f"- config sha256 (canonical): `{manifest['hashes']['config_sha256']}`",
        f"- trace sha256 (canonical): `{manifest['hashes']['trace_sha256']}`",
        f"- config_id: `{manifest['config_id']}`; config: frozen `CognitionConfig()` defaults",
        f"- rerun of exported input.json through replay.run identical: "
        f"`{manifest['rerun_identical']}`",
        f"- source: `{manifest['source']['path']}` (table `candles`)", "",
        "## Window", "",
        f"- timeframe {p['timeframe']}; decisions [{iso(p['start_ms'])}, {iso(p['stop_ms'])}) "
        f"= {len(p['decision_times'])}",
        f"- warmup from {iso(p['warmup_start_ms'])} ({p['warmup_bars']} bars); "
        f"resolve_until {iso(p['resolve_until_ms'])}; last deadline "
        f"{iso(p['last_decision_deadline_ms'])}; complete horizon `{p['complete_horizon']}`", "",
        "## Universe and coverage", "",
        f"- symbols: {len(manifest['universe'])} (rule: {UNIVERSE_RULE})",
        f"- symbols first stored after start (excluded): "
        f"{manifest['excluded_post_start_symbols']}",
        f"- possible aliases (not merged): {manifest['possible_aliases'] or 'none detected'}",
        f"- bars expected {cov['expected_bars_total']}, valid {cov['valid_bars_total']}, "
        f"missing {cov['missing_bars_total']} "
        f"({cov['symbols_with_missing_bars']} symbols with gaps), rejected rows "
        f"{cov['rejected_rows_total']}, export-refused rows {len(manifest['export_rejected'])}",
        "", "| symbol | valid | missing | rejected |", "|---|---|---|---|"]
    gappy = [(s, c) for s, c in cov["per_symbol"].items()
             if c["missing_bars"] or c["rejected_rows"]]
    lines += [f"| {s} | {c['valid_bars']} | {c['missing_bars']} | {c['rejected_rows']} |"
              for s, c in gappy] or ["| (none) | | | |"]
    r = report
    lines += [
        "", "## Decisions", "",
        f"- decisions {r['decisions']}; universe rows {r['universe_rows']}; eligible "
        f"{r['eligible']}; ineligible {r['ineligible']}; selected {r['selected']}; "
        f"eligible-unselected {r['eligible_unselected']}",
        f"- decisions selecting nothing {r['decisions_selecting_nothing']}; cohort ok "
        f"{r['cohort_ok_decisions']}; selected per decision {r['selected_per_decision']}",
        f"- row status {r['row_status']}",
        f"- omission reasons {r['omission_reason']}",
        f"- episodes {r['episodes']}; framing {r['framing']}",
        f"- framing reasons {r['framing_reason']}",
        f"- hypothesis outcome status {r['hypothesis_status']}",
        f"- outcomes {r['outcomes']}: {r['outcome_status']}; rejected inputs "
        f"{r['rejected_inputs']}", "",
        "## Baselines (descriptive only)", "",
        "| bucket | samples | status | unresolved frac | abs z: n / mean / median "
        "| abs log-return: n / mean / median |", "|---|---|---|---|---|---|"]
    for key, b in r["baselines"].items():
        z = b.get("abs_forward_z") or b.get("abs_rel_forward_z")
        lr = b.get("abs_forward_log_return") or b.get("abs_forward_log_return_minus_cohort_median")
        lines.append(f"| {key} | {b['samples']} | {b['status']} | {_fmt(b['unresolved_fraction'])} "
                     f"| {z['n']} / {_fmt(z['mean'])} / {_fmt(z['median'])} "
                     f"| {lr['n']} / {_fmt(lr['mean'])} / {_fmt(lr['median'])} |")
    lines += ["", "Relative log-return is the asset's forward log return minus the frozen "
              "cohort median.", "", "## Assumptions and limitations", ""]
    lines += [f"- {a}" for a in ASSUMPTIONS]
    lines += ["- Thresholds are the uncalibrated defaults; no tuning was done.",
              "- Selected vs unselected differences are not tested for significance and "
              "are not evidence of edge.", ""]
    return "\n".join(lines)


def prepare_output(out: Path, db: Path) -> Path:
    out = Path(out).resolve()
    if out == Path(db).resolve() or Path(db).resolve().is_relative_to(out):
        raise HistoryError("output dir must not contain the source database")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise HistoryError(f"output dir exists and is not empty: {out}")
    return out


def execute(db: Path, timeframe: str, start: str, stop: str, resolve_until: str,
            output_dir: Path, *, allow_incomplete=False, max_decisions=MAX_DECISIONS,
            max_symbols=MAX_SYMBOLS, max_rows=MAX_ROWS, now_ms=None, command="") -> dict:
    for name, v, cap in (("max_decisions", max_decisions, MAX_DECISIONS),
                         ("max_symbols", max_symbols, MAX_SYMBOLS),
                         ("max_rows", max_rows, MAX_ROWS)):
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= cap:
            raise HistoryError(f"{name} must be an int in [1, {cap}]")
    cfg = CognitionConfig()
    plan = plan_window(timeframe, parse_utc(start, "start"), parse_utc(stop, "stop"),
                       parse_utc(resolve_until, "resolve_until"), cfg,
                       int(time.time() * 1000) if now_ms is None else now_ms,
                       allow_incomplete, max_decisions)
    out = prepare_output(output_dir, db)
    db_path = Path(db).resolve()
    store = read_store(db_path, plan, max_symbols, max_rows)       # connection closed here
    source = f"local_candle_store:{db_path.name}:unknown_venue_provenance"
    raw, export_rejected = build_input(store, plan, source)
    input_text = json.dumps(raw, indent=1, sort_keys=True, allow_nan=False)

    trace = run(json.loads(input_text), cfg, plan["resolve_until_ms"])
    rerun = run(json.loads(input_text), cfg, plan["resolve_until_ms"])
    trace_text = dump(trace)
    report = summarize(trace)
    ds = load_input(raw)
    manifest = {
        "schema": MANIFEST_SCHEMA, "kind": "retrospective_reconstruction",
        "command": command,
        "source": {"path": str(db_path), "table": "candles", "schema_sql": store["schema_sql"],
                   "candle_source_label": source, "access": "sqlite mode=ro, query_only=ON, "
                   "single read transaction, closed before replay"},
        "queries": store["queries"], "window": plan, "universe_rule": UNIVERSE_RULE,
        "universe": store["universe"],
        "excluded_post_start_symbols": store["excluded_post_start_symbols"],
        "possible_aliases": possible_aliases(store["universe"]),
        "assumptions": ASSUMPTIONS, "participation": "none exported",
        "rows_read": len(store["rows"]), "export_rejected": export_rejected,
        "load_rejected": ds.rejected,
        "coverage": coverage(raw, plan, store["universe"], ds.rejected),
        "config": asdict(cfg), "config_id": trace["config_id"],
        "end_ms": plan["resolve_until_ms"],
        "hashes": {"input_sha256": sha256_json(raw), "config_sha256": sha256_json(asdict(cfg)),
                   "trace_sha256": sha256_json(trace)},
        "rerun_identical": dump(rerun) == trace_text,
        "no_trade_scope": NO_TRADE_SCOPE}
    texts = {"input.json": input_text, "trace.json": trace_text,
             "report.json": json.dumps(report, indent=2, sort_keys=True, allow_nan=False)}
    manifest["files_sha256"] = {n: hashlib.sha256(t.encode()).hexdigest()
                                for n, t in texts.items()}
    texts["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False)
    texts["report.md"] = render_markdown(manifest, report)
    out.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_FILES:
        with open(out / name, "x") as f:                       # never overwrite
            f.write(texts[name])
    return {"output_dir": str(out), "manifest": manifest, "report": report}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.cognition_history",
                                 description="Retrospective cognition replay from a "
                                             "local candle store (read-only).")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--timeframe", required=True)
    ap.add_argument("--start", required=True, help=f"first decision, {TS_FORMAT}")
    ap.add_argument("--stop", required=True, help=f"exclusive decision bound, {TS_FORMAT}")
    ap.add_argument("--resolve-until", required=True, help=f"final evaluation, {TS_FORMAT}")
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--allow-incomplete", action="store_true")
    ap.add_argument("--max-decisions", type=int, default=MAX_DECISIONS)
    ap.add_argument("--max-symbols", type=int, default=MAX_SYMBOLS)
    ap.add_argument("--max-rows", type=int, default=MAX_ROWS)
    args = ap.parse_args(argv)
    command = "./venv/bin/python -m scripts.cognition_history " + shlex.join(
        sys.argv[1:] if argv is None else argv)
    try:
        res = execute(args.db, args.timeframe, args.start, args.stop, args.resolve_until,
                      args.output_dir, allow_incomplete=args.allow_incomplete,
                      max_decisions=args.max_decisions, max_symbols=args.max_symbols,
                      max_rows=args.max_rows, command=command)
    except (HistoryError, sqlite3.Error) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    r, m = res["report"], res["manifest"]
    print(f"decisions={r['decisions']} symbols={len(m['universe'])} selected={r['selected']} "
          f"episodes={r['episodes']} outcomes={r['outcomes']} "
          f"trace_sha256={m['hashes']['trace_sha256']} -> {res['output_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
