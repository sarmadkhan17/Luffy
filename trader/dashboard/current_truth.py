"""Current-truth readers for the owner surfaces (owner API, GraphQL, legacy).

Each reader returns ``(value, error)`` from records the kernel wrote, judged
against the record's own source times (core.truth): a missing, malformed,
contradictory, future-dated or stale record is reported as such and never as
a current good value. Any source time later than the reader's clock — by any
amount — is invalid. Nothing here writes, gates or calls the venue.

- account:    state_kv.account_observation (kernel _risk_step) and, for the
              equity series, equity_provenance (one record per equity row)
- news_guard: state_kv.news_guard_state (NewsGuard record, kernel-published)
- risk:       state_kv.risk_assessment (kernel cycle) + risk_state baseline +
              configured limits (engine.risk.policy_from_config)
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path

from ..core import truth

ACCOUNT_STALE_S = 300.0            # equity is observed every kernel cycle
RISK_STALE_S = 300.0               # the assessment is written every kernel cycle
NEWS_MAX_STALE_S = 7200.0          # bound on a recorded stale_after_s
ACCOUNT_STATUSES = ("FRESH", "VENUE_FALLBACK", "JOURNAL_FALLBACK", "UNAVAILABLE")
RISK_STATUSES = ("PASS", "BLOCK", "DEGRADED", "UNAVAILABLE")
RISK_CONSTRAINTS = ("risk_baseline", "halt_drawdown", "daily_loss_breaker",
                    "max_open_positions", "portfolio_heat", "total_margin",
                    "per_symbol_risk_cap", "per_position_margin")
RISK_RESULTS = ("pass", "block", "not_evaluated", "applies_at_entry")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _load(raw):
    if raw is None or raw == "":
        return None, "missing"
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return None, "malformed"
    return (v, None) if isinstance(v, dict) else (None, "malformed")


def _t(v):
    return truth.parse_time(v)[0]


def _after(a, b) -> bool:
    """True when time a is strictly later than time b (no skew)."""
    return truth.is_after(_t(a), _t(b))


def _count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _pos(v) -> bool:
    return truth.finite(v) is not None and not isinstance(v, bool) and float(v) > 0


# ── account ─────────────────────────────────────────────────────────────────
ACCOUNT_SOURCE = ("journal state_kv.account_observation (kernel Risk-step equity input; "
                  "BINANCE_DEMO account)")


def _account_problems(rec: dict) -> list[str]:
    p = []
    status = rec.get("status")
    if status not in ACCOUNT_STATUSES:
        return ["status_unrecognized"]
    live = status in ("FRESH", "VENUE_FALLBACK")
    if rec.get("authoritative") is not live:
        p.append("authoritative_contradicts_status")
    value = rec.get("value")
    if status == "UNAVAILABLE":
        if value is not None:
            p.append("unavailable_with_value")
    elif not _pos(value):
        p.append("value_malformed")
    times = {}
    for k in ("attempted_at", "recorded_at"):
        t, err = truth.parse_time(rec.get(k))
        if t is None:
            p.append(f"{k}_{(err or 'invalid').replace('source_time_', '')}")
        times[k] = t
    for k in ("observed_at", "successful_read_at"):
        if rec.get(k) is not None:
            t = _t(rec.get(k))
            if t is None:
                p.append(f"{k}_malformed")
            times[k] = t
        else:
            times[k] = None
    if p:
        return p
    att, rec_at, obs, succ = (times["attempted_at"], times["recorded_at"],
                              times["observed_at"], times["successful_read_at"])
    if att > rec_at:
        p.append("attempt_after_record")
    rel = rec.get("completion_relation")
    if live:
        # an impossible completion time (before the attempt / after the
        # record) is an invalid clock, judged in read_account — not repaired
        # and not a structural fault; the times must still agree
        if rec.get("observed_at") != rec.get("successful_read_at"):
            p.append("fresh_read_times_inconsistent")
        if obs is None and rel not in ("completion_missing", "completion_malformed"):
            p.append("fresh_read_without_completion_reason")
        if obs is not None and rel is not None and rel != _completion_relation(obs, att, rec_at):
            p.append("completion_relation_inconsistent")
    else:
        for k, t in (("observed_at", obs), ("successful_read_at", succ)):
            if t is not None and t > rec_at:
                p.append(f"{k}_after_record")
        if status == "UNAVAILABLE" and obs is not None:
            p.append("unavailable_with_source_time")
        if obs is not None and obs > att:
            p.append("observed_at_after_attempt")
        if succ is not None and succ > att:
            p.append("successful_read_at_after_attempt")
        if status == "JOURNAL_FALLBACK" and not (
                isinstance(rec.get("fallback"), dict)
                and rec["fallback"].get("row_written_at")):
            p.append("fallback_row_missing")
    return p


def _completion_relation(obs, att, rec_at) -> str:
    return ("completion_before_attempt" if obs < att else
            "completion_after_record" if obs > rec_at else "ok")


def _completion_clock(rec: dict) -> tuple[str | None, list[str]]:
    """(freshness override, reasons) for a live read's completion time:
    missing → unavailable; malformed or impossible → invalid; ok → None."""
    if rec.get("status") not in ("FRESH", "VENUE_FALLBACK"):
        return None, []
    obs = _t(rec.get("observed_at"))
    if obs is None:
        rel = rec.get("completion_relation") or "completion_missing"
        return (truth.UNAVAILABLE if rel == "completion_missing" else truth.INVALID), [rel]
    rel = _completion_relation(obs, _t(rec["attempted_at"]), _t(rec["recorded_at"]))
    return (None, []) if rel == "ok" else (truth.INVALID, [rel])


def has_provenance_table(journal) -> bool:
    """False on a pre-migration journal (read-only owners never migrate it)."""
    return bool(journal.query("SELECT 1 FROM sqlite_master WHERE type='table' "
                              "AND name='equity_provenance'"))


def equity_rows_sql(where: str = "", order: str = "e.ts DESC", limit: bool = True,
                    *, provenance: bool = True) -> str:
    """Equity rows with their provenance record, or NULL provenance when the
    table does not exist (old history stays readable, as unknown origin)."""
    join = ("e.*, p.provenance provenance FROM equity e LEFT JOIN equity_provenance p "
            "ON p.ts = e.ts") if provenance else "e.*, NULL provenance FROM equity e"
    return (f"SELECT {join} {where} ORDER BY {order}" + (" LIMIT ?" if limit else ""))


def _row_provenance(journal) -> tuple[dict | None, str | None]:
    rows = journal.query(equity_rows_sql(provenance=has_provenance_table(journal)), (1,))
    if not rows:
        return None, "no_equity_records"
    return rows[0], None


def current_account_status(source_status: str | None, freshness: str) -> str:
    """The account's CURRENT status from the producer's status and the value's
    source-time freshness. Only a fresh source time keeps the producer status
    (FRESH / VENUE_FALLBACK / JOURNAL_FALLBACK — a fallback keeps its original
    source age); a stale source is STALE; an invalid, missing or unproven
    source time — or no value — is UNAVAILABLE. Never FRESH unless fresh."""
    if source_status == "UNAVAILABLE" or freshness in (truth.INVALID, truth.UNAVAILABLE):
        return "UNAVAILABLE"
    if freshness == truth.STALE:
        return "STALE"
    if freshness == truth.FRESH and source_status in ("FRESH", "VENUE_FALLBACK",
                                                      "JOURNAL_FALLBACK"):
        return source_status
    return "UNAVAILABLE"                     # unknown producer status is not current


def read_account(journal, now: datetime):
    """Account equity with provenance. `status` is the CURRENT status
    (current_account_status); the producer's own status is `source_status`
    and, whenever it is not current, also `last_reported`. `authoritative` is
    true only while current and venue-sourced; `source_authoritative` keeps
    the producer's flag. Evidence values (equity, times, reasons) are kept."""
    value, err = _read_account_record(journal, now)
    if value is None:
        return value, err
    src_status, src_auth = value["status"], value.get("authoritative") is True
    cur = current_account_status(src_status, value["freshness"])
    value.update(status=cur, source_status=src_status,
                 last_reported=None if cur == src_status else src_status,
                 source_authoritative=src_auth,
                 authoritative=src_auth and cur in ("FRESH", "VENUE_FALLBACK"))
    return value, err


def _read_account_record(journal, now: datetime):
    rec, err = _load(journal.kv_get("account_observation"))
    if err == "malformed":
        return None, "account_observation_malformed"
    if rec is None:
        row, err = _row_provenance(journal)
        if row is None:
            return None, err
        value = truth.finite(row.get("equity"))
        return {"equity": value, "balance": value, "currency": "USDT",
                "status": "PROVENANCE_UNRECORDED", "basis": None,
                "observed_at": None, "age_s": None, "freshness": truth.UNAVAILABLE,
                "stale_after_s": ACCOUNT_STALE_S, "row_written_at": row.get("ts"),
                "reasons": ["account_observation_missing"],
                "source": "journal equity table (row write time is not a venue read "
                          "time; no current account observation)"}, None
    problems = _account_problems(rec)
    if problems:
        return None, "account_observation_malformed:" + ",".join(problems)
    status = rec["status"]
    src = truth.freshness_of(rec.get("observed_at"), now, ACCOUNT_STALE_S)
    clock, clock_reasons = _completion_clock(rec)
    recorded = truth.freshness_of(rec["recorded_at"], now, ACCOUNT_STALE_S)
    reasons = [r for r in str(rec.get("reason") or "").split(";") if r]
    freshness = src["freshness"]
    if recorded["freshness"] == truth.INVALID:
        freshness = truth.INVALID
        reasons.append("recorded_at_in_future")
    elif recorded["freshness"] == truth.STALE:
        reasons.append("no_recent_kernel_observation")
    if src["reason"] and src["reason"] != "source_time_missing":
        reasons.append(src["reason"])
    if status in ("JOURNAL_FALLBACK", "UNAVAILABLE") and rec.get("observed_at") is None:
        reasons.append("value_source_time_unproven")
    if clock is not None:
        # never fresh on an impossible or unknown read-completion time
        freshness = truth.INVALID if truth.INVALID in (clock, freshness) else clock
        reasons += ["invalid_clock:" + r if clock == truth.INVALID else r
                    for r in clock_reasons]
    num = truth.finite(rec.get("value"))
    return {"equity": num, "balance": num,
            "currency": "USDT", "status": status, "basis": rec.get("basis"),
            "authoritative": rec["authoritative"],
            "observed_at": src["observed_at"], "age_s": src["age_s"],
            "freshness": freshness, "stale_after_s": ACCOUNT_STALE_S,
            "attempted_at": rec["attempted_at"], "recorded_at": recorded["observed_at"],
            "recorded_age_s": recorded["age_s"],
            "successful_read_at": rec.get("successful_read_at"),
            "successful_value": truth.finite(rec.get("successful_value")),
            "consecutive_failures": rec.get("consecutive_failures"),
            "fallback": rec.get("fallback") if isinstance(rec.get("fallback"), dict) else None,
            "attempt_errors": [str(x) for x in (rec.get("attempt_errors") or [])][:5],
            "reasons": list(dict.fromkeys(reasons)), "source": ACCOUNT_SOURCE}, None


def row_provenance(raw, row_equity) -> dict:
    """Provenance view of one equity row: the row's ts is a write time; the
    value's source time is provenance.observed_at when recorded for exactly
    this row's value, else unknown."""
    prov, _ = _load(raw)
    if not prov or prov.get("value") != row_equity:
        return {"row_kind": "unknown", "basis": None, "source_observed_at": None,
                "source_status": None}
    rel = prov.get("completion_relation")
    bad_clock = prov.get("row_kind") == "venue_observation" and rel != "ok"
    return {"row_kind": str(prov.get("row_kind") or "unknown"), "basis": prov.get("basis"),
            # an impossible/unknown read-completion time is not a source time
            "source_observed_at": prov.get("observed_at")
            if _t(prov.get("observed_at")) and not bad_clock else None,
            "source_status": prov.get("status"),
            "source_clock": (rel or "completion_missing") if bad_clock else "ok"}


# ── News Guard ──────────────────────────────────────────────────────────────
NEWS_SOURCE = ("journal state_kv.news_guard_state (NewsGuard check record, published by "
               "the kernel each cycle; times are the check's own)")
_NEWS_COUNTS = ("hits", "severe", "dated_hits", "dated_severe", "items_total",
                "items_considered", "dated_in_window", "undated_items", "malformed_dates",
                "future_dated_items")
_FETCHED_TRUTHS = ("ARMED", "UNCERTAIN", "QUIET", "FEED_STALE", "EMPTY_FEED")


def _news_problems(rec: dict) -> list[str]:
    from ..agents import news_guard as ng
    p = []
    tr = rec.get("truth")
    active = rec.get("active")
    if tr not in ng.TRUTHS:
        p.append("truth_unrecognized")
    if not isinstance(active, bool):
        p.append("active_not_bool")
    if not isinstance(rec.get("why"), str):
        p.append("why_not_text")
    for k in _NEWS_COUNTS:
        if not _count(rec.get(k)):
            p.append(f"{k}_malformed")
    mh = rec.get("min_headlines")
    if not (_count(mh) and mh >= 1):
        p.append("min_headlines_malformed")
    codes = rec.get("failure_codes")
    if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
        p.append("failure_codes_malformed")
    d = rec.get("dampening")
    if not isinstance(d, dict) or d.get("threshold_add") != ng.THRESHOLD_ADD \
            or d.get("score_mult") != ng.SCORE_MULT or d.get("applied") is not active:
        p.append("dampening_record_inconsistent")
    sa = truth.finite(rec.get("stale_after_s"))
    if sa is None or not 0 < sa <= NEWS_MAX_STALE_S:
        p.append("stale_after_malformed")
    for k in ("attempted_at", "assessed_at"):
        if _t(rec.get(k)) is None:
            p.append(f"{k}_malformed")
    for k in ("fetched_at", "succeeded_at", "published_at", "newest_publication_at",
              "oldest_publication_at"):
        if rec.get(k) is not None and _t(rec.get(k)) is None:
            p.append(f"{k}_malformed")
    if p:
        return p
    n = {k: rec[k] for k in _NEWS_COUNTS}
    # counts nest: severe ≤ hits ≤ considered ≤ total, dated subsets inside
    if not (n["severe"] <= n["hits"] <= n["items_considered"] <= n["items_total"]):
        p.append("counts_not_nested")
    if not (n["dated_severe"] <= n["dated_hits"] <= n["hits"]
            and n["dated_severe"] <= n["severe"]
            and n["dated_hits"] <= n["dated_in_window"] <= n["items_considered"]):
        p.append("dated_counts_not_nested")
    if n["undated_items"] + n["malformed_dates"] + n["future_dated_items"] > n["items_total"]:
        p.append("uncertain_counts_exceed_items")
    rule = n["hits"] >= mh or n["severe"] >= 1                # the orchestrator's rule
    certain = n["dated_hits"] >= mh or n["dated_severe"] >= 1
    uncertain = n["undated_items"] + n["malformed_dates"] + n["future_dated_items"]
    if tr != "ASSESSMENT_FAILED" and tr != "DISABLED" and active != rule:
        p.append("active_contradicts_counts")
    if tr == "ARMED" and not (active and certain):
        p.append("armed_without_dated_evidence")
    if tr == "UNCERTAIN" and not ((active and not certain) or (not active and uncertain)):
        p.append("uncertain_without_cause")
    if tr in ("QUIET", "FEED_STALE", "EMPTY_FEED", "FETCH_FAILED", "PARSE_FAILED",
              "ASSESSMENT_FAILED", "DISABLED") and active:
        p.append("active_contradicts_truth")
    if tr in ("QUIET", "FEED_STALE", "EMPTY_FEED") and (codes or uncertain):
        p.append("quiet_with_failure_or_uncertain_items")
    if tr == "QUIET" and (n["items_total"] == 0 or n["dated_in_window"] == 0):
        p.append("quiet_without_current_dated_items")
    if tr == "FEED_STALE" and (n["items_total"] == 0 or n["dated_in_window"] != 0):
        p.append("feed_stale_inconsistent")
    if tr == "EMPTY_FEED" and n["items_total"] != 0:
        p.append("empty_feed_with_items")
    if tr in ("FETCH_FAILED", "PARSE_FAILED") and not codes:
        p.append("failure_without_code")
    if tr == "PARSE_FAILED" and not codes[0].startswith("parse"):
        p.append("parse_failure_code_mismatch")
    if tr == "ASSESSMENT_FAILED" and "assessment_exception" not in codes:
        p.append("assessment_failure_without_code")
    if tr in _FETCHED_TRUTHS and (rec.get("fetched_at") is None
                                  or rec.get("succeeded_at") is None):
        p.append("assessment_without_successful_fetch")
    # time order: attempted ≤ fetched ≤ assessed ≤ published; nothing after publication
    if _after(rec.get("attempted_at"), rec.get("fetched_at")):
        p.append("fetch_before_attempt")
    if _after(rec.get("fetched_at"), rec.get("assessed_at")):
        p.append("fetch_after_assessment")
    if _after(rec.get("attempted_at"), rec.get("assessed_at")):
        p.append("attempt_after_assessment")
    if _after(rec.get("succeeded_at"), rec.get("assessed_at")):
        p.append("success_after_assessment")
    if rec.get("published_at") is not None and _after(rec.get("assessed_at"),
                                                      rec.get("published_at")):
        p.append("assessment_after_publication")
    # a publication after the fetch finished is impossible unless recorded as such
    if _after(rec.get("newest_publication_at"), rec.get("fetched_at")) \
            and not n["future_dated_items"]:
        p.append("future_publication_not_recorded")
    return p


def read_news_guard(journal, now: datetime):
    """status: the record's truth when fresh | STALE (last_reported kept) |
    UNAVAILABLE (missing, malformed, contradictory or invalid clock). `clear`
    is true only for a fresh QUIET record; every other status means news risk
    is not known to be clear."""
    rec, err = _load(journal.kv_get("news_guard_state"))
    base = {"clear": False, "source": NEWS_SOURCE}
    if rec is None:
        return {**base, "status": "UNAVAILABLE", "reasons": [f"news_guard_state_{err}"]}, \
            f"news_guard_state_{err}"
    problems = _news_problems(rec)
    if problems:
        why = "news_guard_state_malformed:" + ",".join(problems[:6])
        return {**base, "status": "UNAVAILABLE", "reasons": problems}, why
    stale_after = float(rec["stale_after_s"])
    f = truth.freshness_of(rec["assessed_at"], now, stale_after)
    common = {**base, "truth": rec["truth"], "active": rec["active"], "why": rec["why"][:200],
              "assessed_at": f["observed_at"], "age_s": f["age_s"],
              "freshness": f["freshness"], "stale_after_s": stale_after,
              "attempted_at": rec.get("attempted_at"), "fetched_at": rec.get("fetched_at"),
              "succeeded_at": rec.get("succeeded_at"), "published_at": rec.get("published_at"),
              "newest_publication_at": rec.get("newest_publication_at"),
              "oldest_publication_at": rec.get("oldest_publication_at"),
              **{k: rec[k] for k in _NEWS_COUNTS},
              "headlines": [str(h)[:120] for h in (rec.get("headlines") or [])][:5],
              "failure_codes": rec["failure_codes"], "dampening": rec["dampening"]}
    for k in ("published_at",):
        if rec.get(k) and truth.freshness_of(rec[k], now, 1e12)["freshness"] == truth.INVALID:
            f = {**f, "freshness": truth.INVALID, "reason": "published_at_in_future"}
    if f["freshness"] == truth.INVALID:
        return {**common, "freshness": truth.INVALID, "status": "UNAVAILABLE",
                "last_reported": rec["truth"],
                "reasons": ["invalid_clock:" + (f["reason"] or "invalid")]}, None
    if f["freshness"] == truth.STALE:
        return {**common, "status": "STALE", "last_reported": rec["truth"],
                "reasons": ["news_check_stale"]}, None
    reasons = list(rec["failure_codes"])
    for k, code in (("future_dated_items", "future_dated_publication"),
                    ("undated_items", "undated_publication"),
                    ("malformed_dates", "malformed_publication_date")):
        if rec[k]:
            reasons.append(code)
    if rec["truth"] == "FEED_STALE":
        reasons.append("no_publication_inside_window")
    if rec["truth"] == "EMPTY_FEED":
        reasons.append("feed_has_no_entries")
    return {**common, "status": rec["truth"], "last_reported": None,
            "clear": rec["truth"] == "QUIET", "reasons": reasons}, None


# ── Risk ────────────────────────────────────────────────────────────────────
RISK_SOURCE = "journal state_kv.risk_assessment (kernel cycle Risk observation)"


def _policy_digest(effective) -> str | None:
    try:
        blob = json.dumps(effective, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(blob.encode()).hexdigest()


def configured_policy(cfg: dict, root: Path | None = None) -> tuple[dict | None, str | None]:
    """The Risk limits the dashboard's loaded config declares, with identity.
    Configuration only: not evidence of the running kernel's policy or of
    current safety."""
    from ..engine.risk import policy_from_config
    try:
        pol = policy_from_config(cfg or {})
    except (KeyError, TypeError, ValueError) as e:
        return None, f"risk_config_unreadable:{type(e).__name__}"
    file_sha = None
    if root is not None:
        try:
            file_sha = hashlib.sha256((Path(root) / "config.yaml").read_bytes()).hexdigest()
        except OSError:
            file_sha = None
    return {"effective": pol["effective"], "limits": pol["limits"], "digest": pol["digest"],
            "config_file_sha256_now": file_sha,
            "source": "config.yaml risk section as loaded by the dashboard process "
                      "(configuration, not current safety)"}, None


def _risk_problems(rec: dict) -> list[str]:
    p = []
    status = rec.get("status")
    if status not in RISK_STATUSES:
        p.append("status_unrecognized")
    cons = rec.get("constraints")
    if not isinstance(cons, list) or not all(isinstance(c, dict) for c in cons):
        return p + ["constraints_malformed"]
    names = [c.get("name") for c in cons]
    if sorted(n for n in names if isinstance(n, str)) != sorted(RISK_CONSTRAINTS) \
            or len(names) != len(RISK_CONSTRAINTS):
        p.append("constraints_incomplete_or_duplicated")
    for c in cons:
        if c.get("result") not in RISK_RESULTS:
            p.append(f"constraint_result_malformed:{c.get('name')}")
        elif c.get("result") in ("pass", "block") and c.get("name") != "risk_baseline" and (
                truth.finite(c.get("limit")) is None or truth.finite(c.get("observed")) is None):
            p.append(f"constraint_values_malformed:{c.get('name')}")
    pol = rec.get("policy")
    if not isinstance(pol, dict) or not isinstance(pol.get("digest"), str) \
            or not _HEX64.match(pol["digest"]) or not isinstance(pol.get("effective"), dict):
        p.append("policy_identity_missing")
    elif _policy_digest(pol["effective"]) != pol["digest"]:
        p.append("policy_digest_mismatch")
    ctl = rec.get("control")
    if not isinstance(ctl, dict) or not isinstance(ctl.get("state"), str):
        p.append("control_missing")
    elif ctl.get("entries_permitted_by_control") is not (ctl["state"] == "ACTIVE"):
        p.append("control_applicability_inconsistent")
    if not isinstance(rec.get("reasons"), list):
        p.append("reasons_malformed")
    eq = rec.get("equity")
    if not isinstance(eq, dict):
        p.append("equity_missing")
    if _t(rec.get("assessed_at")) is None:
        p.append("assessed_at_malformed")
    if p:
        return p
    results = {c["name"]: c["result"] for c in cons}
    blocks = [n for n, r in results.items() if r == "block"]
    if status == "BLOCK" and not blocks:
        p.append("block_without_blocking_constraint")
    if status in ("PASS", "DEGRADED") and blocks:
        p.append(f"{status.lower()}_with_blocking_constraint")
    if eq.get("observed_at") is not None:
        if _t(eq.get("observed_at")) is None:
            p.append("equity_observed_at_malformed")
        elif _after(eq["observed_at"], rec["assessed_at"]):
            p.append("equity_observed_after_assessment")
    if status == "PASS":
        if any(r == "not_evaluated" for r in results.values()):
            p.append("pass_with_unevaluated_constraint")
        if not (eq.get("authoritative") is True and eq.get("fresh_at_assessment") is True
                and _pos(eq.get("value")) and _t(eq.get("observed_at")) is not None):
            p.append("pass_without_fresh_authoritative_equity")
    # recompute the drawdown/daily figures from the recorded baseline
    if rec.get("risk_state") == "ok" and status != "UNAVAILABLE":
        b = rec.get("baseline") if isinstance(rec.get("baseline"), dict) else {}
        e, peak, day = eq.get("value"), b.get("peak_equity"), b.get("day_start_equity")
        if not (_pos(e) and _pos(peak)):
            p.append("baseline_or_equity_malformed")
        else:
            dd = round(max(0.0, (peak - e) / peak) * 100, 2)
            if truth.finite(rec.get("drawdown_pct")) is None or \
                    abs(dd - rec["drawdown_pct"]) > 0.011:
                p.append("drawdown_not_reproducible")
            if _pos(day):
                dp = round((e - day) / day * 100, 2)
                if truth.finite(rec.get("daily_pnl_pct")) is None or \
                        abs(dp - rec["daily_pnl_pct"]) > 0.011:
                    p.append("daily_pnl_not_reproducible")
    return p


def read_risk(journal, now: datetime, cfg: dict | None = None, root: Path | None = None):
    configured, cfg_err = configured_policy(cfg, root) if cfg is not None else (None, None)
    baseline, b_err = _load(journal.kv_get("risk_state"))
    out = {"configured": configured, "configured_error": cfg_err,
           "baseline": {"value": baseline, "error": b_err and f"risk_state_{b_err}",
                        "note": "Risk baseline (high-water mark and day start); "
                                "not a current assessment", "source": "state_kv.risk_state"},
           "source": RISK_SOURCE}
    rec, err = _load(journal.kv_get("risk_assessment"))
    if rec is None:
        return {**out, "current": {"status": "UNAVAILABLE",
                                   "reasons": [f"risk_assessment_{err}"]},
                "policy_match": "UNKNOWN"}, f"risk_assessment_{err}"
    problems = _risk_problems(rec)
    if problems:
        why = "risk_assessment_malformed:" + ",".join(problems[:6])
        return {**out, "current": {"status": "UNAVAILABLE", "reasons": problems},
                "policy_match": "UNKNOWN"}, why
    pol, eq = rec["policy"], rec["equity"]
    f = truth.freshness_of(rec["assessed_at"], now, RISK_STALE_S)
    src = truth.freshness_of(eq.get("observed_at"), now, ACCOUNT_STALE_S)
    cur = {"assessed_at": f["observed_at"], "age_s": f["age_s"], "freshness": f["freshness"],
           "stale_after_s": RISK_STALE_S, "reasons": list(rec["reasons"]),
           "constraints": rec["constraints"], "risk_state": rec.get("risk_state"),
           "drawdown_pct": rec.get("drawdown_pct"), "daily_pnl_pct": rec.get("daily_pnl_pct"),
           "halt_breached": rec.get("halt_breached"), "baseline": rec.get("baseline"),
           "equity": {**eq, "source_freshness_now": src["freshness"],
                      "source_age_s": src["age_s"]},
           "book": rec.get("book"), "control": rec["control"],
           "entry_gate": rec.get("entry_gate"),
           "running_policy": {"digest": pol["digest"], "effective": pol["effective"],
                              "limits": pol.get("limits"),
                              "risk_manager_identity": pol.get("risk_manager_identity")}}
    current = f["freshness"] == truth.FRESH
    if current and rec["status"] == "PASS" and src["freshness"] != truth.FRESH:
        current = False                     # a PASS needs its input fresh *now* too
        cur["reasons"] = cur["reasons"] + [f"equity_source_{src['freshness']}"]
    if current:
        cur.update(status=rec["status"], last_reported=None)
    else:
        bad = f["freshness"] if f["freshness"] != truth.FRESH else src["freshness"]
        cur.update(status="STALE" if bad == truth.STALE else "UNAVAILABLE",
                   last_reported=rec["status"])
        if f["freshness"] != truth.FRESH:
            cur["reasons"] = cur["reasons"] + ["risk_assessment_" + (
                "stale" if f["freshness"] == truth.STALE else
                "invalid_clock:" + (f["reason"] or "invalid"))]
        # a stale/invalid observation publishes no current numbers
        cur.update(drawdown_pct=None, daily_pnl_pct=None, halt_breached=None,
                   last_reported_values={"drawdown_pct": rec.get("drawdown_pct"),
                                         "daily_pnl_pct": rec.get("daily_pnl_pct")})
    match = "UNKNOWN"
    if configured is not None:
        match = "MATCH" if configured["digest"] == pol["digest"] else "MISMATCH"
    return {**out, "current": cur, "policy_match": match}, None
