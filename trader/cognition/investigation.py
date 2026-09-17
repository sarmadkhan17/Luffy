"""Pure, forward-only investigation prototype. No trading/admission authority.

Catalog v2 is separate from frozen cognition v1. All three observable alternatives
are issued together, without a favorite or probability. A matching path does not
identify a cause. Times are UTC milliseconds; target bars are open-labelled.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import statistics

from trader.cognition.contracts import Candle, is_timestamp, stable_id

SCHEMA = "investigation.v2"
TF = 14_400_000
N, H = 20, 5
GUARD_MS, MAX_RUNTIME_MS = 30_000, 20_000
GRACE_MS = 86_400_000
FAMILIES = ("volume_anomaly", "volatility_transition", "relative_return_divergence")
CATALOG = {
    "schema": SCHEMA, "timeframe": "4h", "baseline_bars": N, "horizon": H,
    "guard_ms": GUARD_MS, "max_runtime_ms": MAX_RUNTIME_MS, "grace_ms": GRACE_MS,
    "volume": "(mean(log1p(volume) over all H targets)-frozen prior-N mean)/frozen prior-N sd",
    "volatility": "log(sd(H intrabar log(close/open) returns)/frozen N successive-close sd)/sqrt(1/(2*(H-1))+1/(2*(N-1)))",
    "relative": "(sum(H intrabar log(close/open) asset returns)-median of same over full frozen cohort))/(frozen asset sigma*sqrt(H))",
    "thresholds": {"volume_anomaly": 2.0, "volatility_transition": 2.0,
                   "relative_return_divergence": 1.0},
    "target": "H consecutive bars opening strictly after registration plus guard; no pre-publication return",
    "zero_future_volatility": "not_testable; log ratio undefined",
    "alternatives": ["same_direction", "normalization", "opposite_direction"],
    "probability": None, "authority": "research_only",
}
CATALOG_ID = stable_id("catalog", CATALOG)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class InputBar:
    version_id: str
    candle: Candle

    def __post_init__(self):
        c = self.candle
        values = (c.open, c.high, c.low, c.close, c.volume)
        if (not self.version_id or not c.symbol or not all(is_timestamp(t) for t in (c.open_ms, c.close_ms, c.available_ms))
                or c.open_ms % TF or c.close_ms != c.open_ms + TF or c.available_ms < c.close_ms
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)
                or min(values[:4]) <= 0 or c.volume < 0 or c.low > min(c.open, c.close)
                or c.high < max(c.open, c.close)):
            raise ValueError("invalid_input_bar")


@dataclass(frozen=True)
class Dimension:
    name: str
    value: float | None
    status: str
    evidence_id: str | None
    rule: str


@dataclass(frozen=True)
class StateSnapshot:
    state_id: str
    scan_id: str
    symbol: str
    as_of_ms: int
    observed_ms: int
    available_ms: int
    cohort: tuple[str, ...]
    membership_json: str
    evidence_ids: tuple[str, ...]
    input_versions: tuple[str, ...]
    dimensions: tuple[Dimension, ...]
    transitions: tuple[str, ...]
    contradictions: tuple[str, ...]
    missing: tuple[str, ...]
    code_json: str
    config_id: str
    schema_version: str = SCHEMA


@dataclass(frozen=True)
class Alternative:
    name: str
    prediction: str
    invalidator: str
    support: tuple[str, ...]
    contrary: tuple[str, ...]
    needed: tuple[str, ...]
    status: str = "indistinguishable"
    probability: None = None


@dataclass(frozen=True)
class Measurement:
    family: str
    sign: int
    threshold: float
    target_keys: tuple[tuple[str, int], ...]
    baseline_mean: float | None
    baseline_scale: float | None
    baseline_versions: tuple[str, ...]
    deadline_ms: int
    expires_ms: int
    reason: str | None
    catalog_id: str = CATALOG_ID


@dataclass(frozen=True)
class Investigation:
    investigation_id: str
    episode_id: str
    state: StateSnapshot
    registered_ms: int
    primary_trigger: str
    secondary_questions: tuple[str, ...]
    question: str
    alternatives: tuple[Alternative, ...]
    measurement: Measurement
    causal_unknowns: tuple[str, ...]
    schema_version: str = SCHEMA


@dataclass(frozen=True)
class OutcomeEvidence:
    evidence_id: str
    investigation_id: str
    as_of_ms: int
    observed_ms: int
    available_ms: int | None
    status: str
    score: float | None
    target_versions: tuple[tuple[str, int, str], ...]
    missing_keys: tuple[tuple[str, int], ...]
    reason: str
    explanatory_uncertainty: str = "Cause and participant identity remain unknown; this measurement is not P&L."
    schema_version: str = SCHEMA


@dataclass(frozen=True)
class NextAction:
    kind: str
    reason: str
    test: str
    earliest_ms: int | None


@dataclass(frozen=True)
class InvestigationUpdate:
    event_id: str
    investigation_id: str
    previous_event_id: str | None
    as_of_ms: int
    observed_ms: int
    previous_assessment: tuple[tuple[str, str], ...]
    assessment: tuple[tuple[str, str], ...]
    input_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    rationale: str
    next_action: NextAction
    evidence: OutcomeEvidence
    schema_version: str = SCHEMA


QUESTIONS = {
    "volume_anomaly": "Does unusual volume persist, return toward baseline, or reverse?",
    "volatility_transition": "Does the volatility transition persist, normalize, or reverse?",
    "relative_return_divergence": "Does relative divergence continue in the same direction, converge, or reverse?",
}
UNKNOWNS = ("participation", "liquidity", "sector", "catalyst", "cross_market")


def make_state(scan, result, symbol, bars, observed_ms):
    """Called only after the adapter validates/recomputes the coherent prefix."""
    row = result["rows"][symbol]
    dims = tuple(Dimension(o.kind, o.value, o.status, o.obs_id,
                           "attention:" + scan["config_id"])
                 for o in result["observations"]
                 if o.symbol in (symbol, "*") and o.kind in (*FAMILIES, "market_breadth"))
    dims += tuple(Dimension(k, None, "missing", None, "not_captured") for k in UNKNOWNS)
    components, market = row.get("components", {}), result["market"]
    contradictions = []
    div = components.get("relative_return_divergence")
    if market.get("broad") and div is not None and abs(div) >= scan["config"]["divergence_z"]:
        contradictions.append("broad_market_move_with_asset_divergence")
    transitions = tuple(f"{k}:{'positive' if v > 0 else 'negative' if v < 0 else 'flat'}"
                        for k, v in sorted(components.items()) if v is not None)
    versions = tuple(sorted(b.version_id for b in bars))
    fields = dict(scan_id=scan["scan_id"], symbol=symbol, as_of_ms=scan["as_of_ms"],
                  observed_ms=observed_ms, available_ms=scan["as_of_ms"],
                  cohort=tuple(result["cohort"]), membership_json=encode(scan["membership"]),
                  evidence_ids=tuple(sorted({d.evidence_id for d in dims if d.evidence_id})),
                  input_versions=versions, dimensions=dims, transitions=transitions,
                  contradictions=tuple(contradictions),
                  missing=tuple(d.name for d in dims if d.status != "ok"),
                  code_json=encode(scan["code_manifest"]), config_id=scan["config_id"])
    return StateSnapshot(state_id=stable_id("state", fields), **fields)


def open_investigation(state, family, bars, registered_ms):
    if family not in FAMILIES or not is_timestamp(registered_ms) or registered_ms < state.observed_ms:
        raise ValueError("invalid_registration")
    # Guard is part of the frozen publication protocol, not a later adjustment.
    start = (registered_ms // TF + 1) * TF
    if start - registered_ms < GUARD_MS:
        raise ValueError("registration_boundary_guard")
    value = next((d.value for d in state.dimensions if d.name == family and d.status == "ok"), None)
    sign = 0 if value is None or value == 0 else (1 if value > 0 else -1)
    by_key = {(b.candle.symbol, b.candle.open_ms): b for b in bars}
    if len(by_key) != len(bars) or any(b.candle.available_ms > state.as_of_ms or b.candle.close_ms > state.as_of_ms for b in bars):
        raise ValueError("invalid_baseline_prefix")
    anchor = state.as_of_ms // TF * TF - TF
    history = [by_key.get((state.symbol, anchor - i * TF)) for i in reversed(range(N + 6))]
    mean = scale = None
    reason = None
    if not sign:
        reason = "zero_or_missing_initial_component"
    elif any(b is None for b in history):
        reason = "missing_baseline_window"
    else:
        cs = [b.candle for b in history]
        if family == "volume_anomaly":
            hist = [math.log1p(c.volume) for c in cs[-N-1:-1]]
            mean, scale = statistics.fmean(hist), statistics.stdev(hist)
        else:
            scale = statistics.stdev([math.log(b.close / a.close) for a, b in zip(cs, cs[1:])][:N])
        if scale is None or not math.isfinite(scale) or scale <= 0:
            reason = "unusable_baseline_scale"
    cohort = state.cohort if family == "relative_return_divergence" else (state.symbol,)
    if state.symbol not in cohort or not cohort:
        reason = "missing_frozen_cohort"
    targets = tuple((s, start + i * TF) for s in cohort for i in range(H))
    deadline = start + H * TF
    measurement = Measurement(family, sign, CATALOG["thresholds"][family], targets,
                              mean, scale, tuple(b.version_id for b in history if b),
                              deadline, deadline + GRACE_MS, reason)
    episode_id = stable_id("episode", CATALOG_ID, state.symbol, family, anchor)
    iid = stable_id("investigation", episode_id, state.state_id, registered_ms)
    alternatives = tuple(Alternative(name, pred, f"Invalidated when {invalid}",
                         state.evidence_ids, state.contradictions,
                         ("complete exact target window",),
                         "not_testable" if reason else "indistinguishable")
        for name, pred, invalid in (
            ("same_direction", "signed score >= frozen threshold", "signed score < threshold"),
            ("normalization", "-threshold < signed score < threshold", "absolute score >= threshold"),
            ("opposite_direction", "signed score <= -frozen threshold", "signed score > -threshold")))
    return Investigation(iid, episode_id, state, registered_ms, family,
                         tuple(QUESTIONS[k] for k in FAMILIES if k != family),
                         QUESTIONS[family], alternatives, measurement, UNKNOWNS)


def measure(inv, bars, as_of_ms, observed_ms):
    """Latest available exact revision only. Partial windows never receive a score."""
    if not all(is_timestamp(t) for t in (as_of_ms, observed_ms)) or not inv.registered_ms <= as_of_ms <= observed_ms:
        raise ValueError("invalid_observation_time")
    m = inv.measurement
    best = {}
    for b in bars:
        c = b.candle
        key = (c.symbol, c.open_ms)
        if key not in m.target_keys or c.available_ms > as_of_ms or c.close_ms > as_of_ms:
            continue
        prior = best.get(key)
        if prior and prior.candle.available_ms == c.available_ms and prior != b:
            raise ValueError("conflicting_revision")
        if prior is None or prior.candle.available_ms < c.available_ms:
            best[key] = b
    missing = tuple(k for k in m.target_keys if k not in best)
    status, score, reason = "unresolved", None, "target_window_pending"
    if m.reason:
        status, reason = "not_testable", m.reason
    elif observed_ms > m.expires_ms:
        status, reason = "not_testable", "missing_data_expired"
    elif missing or as_of_ms < m.deadline_ms:
        reason = "exact_target_keys_missing" if as_of_ms >= m.deadline_ms else reason
    else:
        cs = [best[k].candle for k in m.target_keys]
        if m.family == "volume_anomaly":
            score = (statistics.fmean(math.log1p(c.volume) for c in cs) - m.baseline_mean) / m.baseline_scale
        elif m.family == "volatility_transition":
            sd = statistics.stdev(math.log(c.close / c.open) for c in cs)
            if sd > 0:
                score = math.log(sd / m.baseline_scale) / math.sqrt(1/(2*(H-1)) + 1/(2*(N-1)))
        else:
            sums = {s: math.fsum(math.log(best[(s, t)].candle.close / best[(s, t)].candle.open)
                                for sym, t in m.target_keys if sym == s) for s in inv.state.cohort}
            score = (sums[inv.state.symbol] - statistics.median(sums.values())) / (m.baseline_scale * math.sqrt(H))
        if score is not None and math.isfinite(score):
            status, reason = "measured", "complete_exact_window"
        else:
            status, score, reason = "not_testable", None, "unusable_future_scale"
    versions = tuple((s, t, b.version_id) for (s, t), b in sorted(best.items()))
    available = max((b.candle.available_ms for b in best.values()), default=None)
    return OutcomeEvidence(stable_id("evidence", inv.investigation_id, versions, status, reason),
                           inv.investigation_id, as_of_ms, observed_ms, available, status,
                           score, versions, missing, reason)


def advance(inv, evidence, previous=None):
    """Append-only proposal; duplicate/irrelevant inputs and terminal revisions do nothing."""
    if evidence.investigation_id != inv.investigation_id:
        raise ValueError("wrong_investigation")
    if previous:
        if previous.investigation_id != inv.investigation_id or evidence.observed_ms < previous.observed_ms:
            raise ValueError("invalid_previous_update")
        if previous.evidence.status != "unresolved" or evidence.evidence_id == previous.evidence.evidence_id:
            return None
    names = tuple(a.name for a in inv.alternatives)
    old = previous.assessment if previous else tuple((n, "indistinguishable") for n in names)
    if evidence.status == "measured":
        signed = evidence.score * inv.measurement.sign
        winner = ("same_direction" if signed >= inv.measurement.threshold else
                  "opposite_direction" if signed <= -inv.measurement.threshold else "normalization")
        assessment = tuple((n, "compatible" if n == winner else "contradicted") for n in names)
        action = NextAction("RESEARCH", "observable_path_measured_cause_unknown",
                            f"Freeze a development test: after {inv.primary_trigger} with sign {inv.measurement.sign}, "
                            "compare all three registered paths against an unconditional same-window baseline, "
                            "using untouched future cases; include costs before any strategy claim.", None)
        if inv.primary_trigger == "volume_anomaly":
            action = NextAction("ACQUIRE", "participant_cause_unassessed",
                                "Request timestamped Binance production taker-buy and total volume for the exact target bars, "
                                "with actual arrival/version times, to distinguish buyer-led from seller-led turnover. "
                                "Neither participant explanation is currently assessed; this is a recommendation only.", None)
    elif evidence.status == "not_testable":
        assessment = tuple((n, "not_testable") for n in names)
        action = NextAction("UNASSESSABLE", evidence.reason, "No valid discriminator within this frozen measurement contract.", None)
    else:
        assessment = old
        action = NextAction("WAIT", evidence.reason,
                            "Observe every exact target bar; do not grade a partial window.",
                            max(evidence.as_of_ms, inv.measurement.deadline_ms))
    reason = (evidence.reason,)
    if previous and evidence.target_versions != previous.evidence.target_versions:
        old_keys = {(s, t): v for s, t, v in previous.evidence.target_versions}
        reason += ("input_revision" if any((s, t) in old_keys and old_keys[(s, t)] != v
                                          for s, t, v in evidence.target_versions) else "new_input",)
    prior_id = previous.event_id if previous else None
    return InvestigationUpdate(stable_id("update", inv.investigation_id, prior_id, evidence.evidence_id),
                               inv.investigation_id, prior_id, evidence.as_of_ms, evidence.observed_ms,
                               old, assessment, tuple(v for _, _, v in evidence.target_versions), reason,
                               "All alternatives retained. Observable compatibility does not establish cause.", action, evidence)


def investigation_from_dict(d):
    s = d["state"]
    s = dict(s, dimensions=tuple(Dimension(**v) for v in s["dimensions"]))
    for key in ("cohort", "evidence_ids", "input_versions", "transitions", "contradictions", "missing"):
        s[key] = tuple(s[key])
    m = dict(d["measurement"])
    m["target_keys"] = tuple(tuple(k) for k in m["target_keys"])
    m["baseline_versions"] = tuple(m["baseline_versions"])
    alternatives = tuple(Alternative(**dict(a, **{k: tuple(a[k]) for k in ("support", "contrary", "needed")})) for a in d["alternatives"])
    return Investigation(**dict(d, state=StateSnapshot(**s), measurement=Measurement(**m),
                                alternatives=alternatives, secondary_questions=tuple(d["secondary_questions"]),
                                causal_unknowns=tuple(d["causal_unknowns"])))


def update_from_dict(d):
    e = dict(d["evidence"])
    for key in ("target_versions", "missing_keys"):
        e[key] = tuple(tuple(v) for v in e[key])
    return InvestigationUpdate(**dict(d, evidence=OutcomeEvidence(**e), next_action=NextAction(**d["next_action"]),
        assessment=tuple(tuple(v) for v in d["assessment"]), previous_assessment=tuple(tuple(v) for v in d["previous_assessment"]),
        input_ids=tuple(d["input_ids"]), reason_codes=tuple(d["reason_codes"])))
