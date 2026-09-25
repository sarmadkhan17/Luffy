"""Strategy-decay research questions — research-question.v1.

A deterministic research question is registered when the TESTED
strategy-health-observation.v1 history shows a deployed strategy

- first truthfully observed as ``decayed`` (``first_truthful_decayed``), or
- moving from a truthful non-decayed verdict (``idle``/``still_working``)
  to ``decayed`` (``transition_to_decayed``).

Truthful verdicts are the health record's own branch verdicts on complete
coverage (``idle``, ``still_working``, ``decayed``). ``compile_failed`` and
``evaluation_failed`` say the branch could not be read: they create no
question and neither start nor end a decayed run, so
decayed -> evaluation_failed -> decayed is one run and one question.

The question is strategy-scoped: it names a spec, never an asset, and
carries no ranking, salience, urgency, usefulness, probability, score or
threshold of its own. ``decayed`` keeps its health-observation meaning —
a lenient retirement trigger on a recent close-fill backtest, not a proven
loss of edge. Nothing consumes the question: there is no Research Planner,
Source Router or Attention trigger behind it, and it has no network, LLM,
Risk, Execution or trading authority.

History is read fail-closed. A candidate is refused, not guessed, when the
history between its baseline (the latest earlier truthful observation whose
own sweep evidence is complete) and itself is malformed, missing,
unverifiable, contradictory or ambiguously ordered; see ``derive``. A later
complete non-decayed observation re-establishes a baseline, so an old gap
does not suppress later, independently established transitions.

`derive` is pure. `record_from_journal` is the only writer: it derives from
``Journal.strategy_health_rows()`` and inserts each question through
``Journal.record_research_question`` (insert / duplicate / conflict), so
retry and restart are idempotent. Nothing calls it from the live loop.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from trader.strategy import health_observation as ho

SCHEMA = "research-question.v1"
QUESTION_KIND = "strategy_decay"
TEMPLATE_ID = "strategy-decay-question-text.v1"
SOURCE_SCHEMA = ho.SCHEMA
SOURCE_KIND = ho.KIND_SPEC

FIRST_TRUTHFUL_DECAYED = "first_truthful_decayed"
TRANSITION_TO_DECAYED = "transition_to_decayed"
TRIGGERS = (FIRST_TRUTHFUL_DECAYED, TRANSITION_TO_DECAYED)

TRUTHFUL = ho.BRANCHES                        # idle, still_working, decayed
UNREADABLE = (ho.COMPILE_FAILED, ho.EVALUATION_FAILED)

SEMANTICS = ("strategy-scoped research question registered from "
             "strategy-health-observation.v1; decayed is the health sweep's "
             "lenient retirement trigger on a recent close-fill backtest, not "
             "a proven loss of edge; no asset attribution, ranking, salience, "
             "urgency, usefulness, probability, score or threshold; not an "
             "Attention trigger; no Research Planner or Source Router consumer; "
             "no network, LLM, Risk, Execution or trading authority")

# ── refusal reasons ──────────────────────────────────────────────────────
MALFORMED_SWEEP_RECORD = "malformed_sweep_record"
UNATTRIBUTABLE_SPEC_RECORD = "unattributable_spec_record"
MALFORMED_SPEC_RECORD = "malformed_spec_record"
DUPLICATE_SWEEP_OBSERVATION = "duplicate_sweep_observation"
AMBIGUOUS_CHRONOLOGY = "ambiguous_chronology"
SWEEP_RECORD_MISSING = "sweep_record_missing"
SWEEP_RECORD_MISMATCH = "sweep_record_mismatch"
OBSERVATION_MISSING = "observation_missing"
OBSERVATION_FAILED = "observation_failed"
INCONSISTENT_VERDICT = "inconsistent_verdict"
SOURCE_FINGERPRINT_MISSING = "source_fingerprint_missing"
SWEEP_RECORD_INCONSISTENT = "sweep_record_inconsistent"
VERDICT_OUTCOME_MISMATCH = "verdict_outcome_mismatch"

_SWEEP_LISTS = ("intended_spec_ids", "evaluation_attempted_spec_ids",
                "evaluation_completed_spec_ids", "compile_failed_spec_ids",
                "observation_recorded_spec_ids", "observation_failed_spec_ids")

_RECORD_KEYS = ("schema", "question_id", "question_kind", "trigger", "scope",
                "question", "source", "prior", "changes_since_prior",
                "unreadable_since_prior_event_ids", "semantics")


class ResearchQuestionError(ValueError):
    """A stored research-question.v1 failed contract verification."""


def canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def question_id(identity: dict) -> str:
    return _sha(canonical(identity))


def _identity(rec: dict) -> dict:
    src = rec["source"]
    return {"schema": rec["schema"], "question_kind": rec["question_kind"],
            "trigger": rec["trigger"], "spec_id": rec["scope"]["spec_id"],
            "source_schema": src["schema"], "source_kind": src["kind"],
            "source_event_id": src["event_id"], "source_sweep_id": src["sweep_id"],
            "source_record_sha256": src["record_sha256"]}


def question_text(trigger, spec_id, sweep_id, prior_verdict, prior_sweep_id) -> str:
    if trigger == FIRST_TRUTHFUL_DECAYED:
        return (f"What explains strategy {spec_id} being first truthfully "
                f"observed with health verdict decayed in decay sweep "
                f"{sweep_id}?")
    return (f"What explains strategy {spec_id} moving from health verdict "
            f"{prior_verdict} in decay sweep {prior_sweep_id} to decayed in "
            f"decay sweep {sweep_id}?")


def _ms(iso):
    if not isinstance(iso, str):
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return int(dt.timestamp() * 1000)


def _nonempty(v) -> bool:
    return isinstance(v, str) and bool(v)


def _str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


@dataclass(frozen=True)
class Refusal:
    spec_id: str | None          # None: the whole history is refused
    reason: str
    event_id: int | None = None
    sweep_id: str | None = None


@dataclass
class Derivation:
    questions: list = field(default_factory=list)   # canonical dicts
    refusals: list = field(default_factory=list)    # Refusal


# ── parsing ──────────────────────────────────────────────────────────────
def _sweep(rec: dict):
    """Validated sweep facts or None."""
    if not _nonempty(rec.get("sweep_id")) or _ms(rec.get("sweep_started_at")) is None:
        return None
    if any(not _str_list(rec.get(k)) for k in _SWEEP_LISTS):
        return None
    aborted = rec.get("aborted_at_spec_id")
    if aborted is not None and not _nonempty(aborted):
        return None
    return {"sweep_id": rec["sweep_id"], "aborted_at_spec_id": aborted,
            "started_ms": _ms(rec["sweep_started_at"]),
            "sweep_started_at": rec["sweep_started_at"],
            **{k: set(rec[k]) for k in _SWEEP_LISTS}}


def _observation(row: dict, rec: dict):
    """(observation, None) or (None, reason) for a decoded spec record."""
    eid = row.get("id")
    if not isinstance(eid, int) or isinstance(eid, bool):
        return None, MALFORMED_SPEC_RECORD
    if not _nonempty(rec.get("sweep_id")):
        return None, MALFORMED_SPEC_RECORD
    started = _ms(rec.get("sweep_started_at"))
    if started is None:
        return None, MALFORMED_SPEC_RECORD
    verdict, branch = rec["verdict"], rec.get("lifecycle_branch")
    cov = rec.get("coverage")
    if verdict in TRUTHFUL:
        # the health record passes a branch through only on complete coverage
        if (branch != verdict or not isinstance(cov, dict)
                or cov.get("coverage_complete") is not True
                or cov.get("coverage_faults") != []
                or rec.get("verdict_reason") is not None):
            return None, INCONSISTENT_VERDICT
    sim = rec.get("simulation_settings")
    return {
        "event_id": eid, "event_ts": row.get("ts"),
        "record_sha256": _sha(row["detail"]),
        "started_ms": started,
        "sweep_id": rec["sweep_id"],
        "sweep_started_at": rec["sweep_started_at"],
        "spec_id": rec["spec_id"],
        "spec_name": rec.get("spec_name"),
        "verdict": verdict,
        "verdict_reason": rec.get("verdict_reason"),
        "lifecycle_branch": branch,
        "retirement_action_selected": rec.get("retirement_action_selected"),
        "health_fingerprint": rec.get("health_fingerprint"),
        "health_fingerprint_schema": rec.get("health_fingerprint_schema"),
        "spec_fingerprint": rec.get("spec_fingerprint"),
        "spec_fingerprint_schema": rec.get("spec_fingerprint_schema"),
        "spec_content_sha256": rec.get("spec_content_sha256"),
        "simulation_settings_sha256": (sim.get("sha256")
                                       if isinstance(sim, dict) else None),
        "thresholds": rec.get("thresholds"),
    }, None


def _source(o: dict) -> dict:
    return {"schema": SOURCE_SCHEMA, "kind": SOURCE_KIND,
            **{k: o[k] for k in _SOURCE_FIELDS}}


_SOURCE_FIELDS = ("event_id", "event_ts", "record_sha256", "sweep_id",
                  "sweep_started_at", "spec_id", "spec_name", "verdict",
                  "verdict_reason", "lifecycle_branch",
                  "retirement_action_selected", "health_fingerprint",
                  "health_fingerprint_schema", "spec_fingerprint",
                  "spec_fingerprint_schema", "spec_content_sha256",
                  "simulation_settings_sha256", "thresholds")
_PRIOR_FIELDS = ("event_id", "record_sha256", "sweep_id", "sweep_started_at",
                 "verdict", "health_fingerprint", "simulation_settings_sha256",
                 "thresholds")
_CHANGE_FIELDS = ("spec_version_changed", "simulation_settings_changed",
                  "thresholds_changed")


def _prior(o: dict) -> dict:
    return {k: o[k] for k in _PRIOR_FIELDS}


def _changes(prior: dict, source: dict) -> dict:
    return {"spec_version_changed": (prior["health_fingerprint"]
                                     != source["health_fingerprint"]),
            "simulation_settings_changed": (
                prior["simulation_settings_sha256"]
                != source["simulation_settings_sha256"]),
            "thresholds_changed": prior["thresholds"] != source["thresholds"]}


def build(trigger, source_obs, prior_obs, unreadable_ids) -> dict:
    """The canonical research-question.v1 dict. Pure and deterministic."""
    spec_id = source_obs["spec_id"]
    rec = {
        "schema": SCHEMA,
        "question_kind": QUESTION_KIND,
        "trigger": trigger,
        "scope": {"kind": "strategy", "spec_id": spec_id},
        "question": {
            "template_id": TEMPLATE_ID,
            "text": question_text(trigger, spec_id, source_obs["sweep_id"],
                                  prior_obs and prior_obs["verdict"],
                                  prior_obs and prior_obs["sweep_id"])},
        "source": _source(source_obs),
        "prior": _prior(prior_obs) if prior_obs else None,
        "changes_since_prior": (None if prior_obs is None
                                else _changes(prior_obs, source_obs)),
        "unreadable_since_prior_event_ids": list(unreadable_ids),
        "semantics": SEMANTICS,
    }
    rec["question_id"] = question_id(_identity(rec))
    # round-trip so the returned dict is exactly what canonical() stores
    return json.loads(canonical(rec))


# ── derivation ───────────────────────────────────────────────────────────
def derive(rows) -> Derivation:
    """Decay research questions implied by raw health rows. Pure; the
    result is independent of row order.

    Fail-closed rules:

    - an undecodable/invalid sweep record, or a spec record whose subject
      is not a spec id, refuses the whole history;
    - an undecodable/invalid spec record refuses every candidate of its
      spec (its position in the history cannot be trusted);
    - two observations of one spec in one sweep, a shared sweep start
      between two sweeps, or sweep order disagreeing with write (event id)
      order refuses every candidate of that spec (ambiguous chronology);
    - a gap is: an observation whose sweep record is absent or disagrees
      with it; a sweep whose lists contradict each other for the spec; an
      observation verdict its sweep's outcome lists contradict (a truthful
      or evaluation_failed-on-coverage verdict needs completed evaluation,
      compile_failed needs the compile-failed list, a raised
      evaluation_failed needs aborted_at); a sweep that lists the spec as
      evaluated/compile-failed/recorded but has no observation of it; or a
      sweep listing the spec in observation_failed_spec_ids;
    - a candidate is refused when any gap lies after its baseline and at or
      before its own sweep. The baseline is the latest earlier truthful
      observation whose own sweep has no gap (none: the start of history).
      A gap-free non-decayed observation after a gap is a new baseline;
      a decayed run that crosses a gap is refused, never assumed;
    - a decayed source without a health fingerprint is refused.
    """
    out = Derivation()
    rows = [r for r in rows if isinstance(r, dict)
            and r.get("kind") in (ho.KIND_SPEC, ho.KIND_SWEEP)]
    sweeps, spec_rows = {}, []
    global_bad = []
    spec_bad: dict[str, str] = {}
    for row in rows:
        rec, bad = ho._decode(row)
        if row["kind"] == ho.KIND_SWEEP:
            sw = _sweep(rec) if rec else None
            if sw is None or sw["sweep_id"] in sweeps:
                global_bad.append((row.get("id"), MALFORMED_SWEEP_RECORD))
                continue
            sweeps[sw["sweep_id"]] = sw
            continue
        subject = row.get("subject")
        if not _nonempty(subject):
            global_bad.append((row.get("id"), UNATTRIBUTABLE_SPEC_RECORD))
            continue
        if bad:
            spec_bad.setdefault(subject, MALFORMED_SPEC_RECORD)
            continue
        spec_rows.append((row, rec))
    if global_bad:
        eid, reason = min(global_bad, key=lambda x: (str(x[0]), x[1]))
        out.refusals.append(Refusal(None, reason, eid if isinstance(eid, int) else None))
        return out

    by_spec: dict[str, list] = {}
    for row, rec in spec_rows:
        o, bad = _observation(row, rec)
        if bad:
            spec_bad.setdefault(rec["spec_id"], bad)
            continue
        by_spec.setdefault(o["spec_id"], []).append(o)

    starts = {}
    for sw in sweeps.values():
        starts.setdefault(sw["started_ms"], set()).add(sw["sweep_id"])
    shared_start = {sid for ids in starts.values() if len(ids) > 1 for sid in ids}

    for spec_id in sorted(set(by_spec) | set(spec_bad)):
        if spec_id in spec_bad:
            out.refusals.append(Refusal(spec_id, spec_bad[spec_id]))
            continue
        obs = by_spec[spec_id]
        refusal = _chronology(spec_id, obs, shared_start)
        if refusal:
            out.refusals.append(refusal)
            continue
        obs.sort(key=lambda o: o["event_id"])
        gaps = _gaps(spec_id, obs, sweeps)
        _emit(spec_id, obs, gaps, out)
    # specs whose only history is a sweep-level gap produce no candidate and
    # need no refusal: nothing was observed for them.
    out.questions.sort(key=lambda q: (q["scope"]["spec_id"],
                                      q["source"]["event_id"]))
    return out


def _chronology(spec_id, obs, shared_start):
    seen = set()
    for o in obs:
        if o["sweep_id"] in seen:
            return Refusal(spec_id, DUPLICATE_SWEEP_OBSERVATION,
                           o["event_id"], o["sweep_id"])
        seen.add(o["sweep_id"])
        if o["sweep_id"] in shared_start:
            return Refusal(spec_id, AMBIGUOUS_CHRONOLOGY, o["event_id"],
                           o["sweep_id"])
    by_id = [o["sweep_id"] for o in sorted(obs, key=lambda o: o["event_id"])]
    by_start = [o["sweep_id"] for o in sorted(obs, key=lambda o: o["started_ms"])]
    starts = [o["started_ms"] for o in obs]
    if by_id != by_start or len(set(starts)) != len(starts):
        return Refusal(spec_id, AMBIGUOUS_CHRONOLOGY)
    return None


def _sweep_conflict(sw, spec_id) -> bool:
    """The sweep's own lists contradict each other for this spec (the
    Analyst attempts every spec it completes or fails to compile, never
    both; it records or fails to record only what it built)."""
    done = spec_id in sw["evaluation_completed_spec_ids"]
    cf = spec_id in sw["compile_failed_spec_ids"]
    att = spec_id in sw["evaluation_attempted_spec_ids"]
    rec = spec_id in sw["observation_recorded_spec_ids"]
    fail = spec_id in sw["observation_failed_spec_ids"]
    raised = sw["aborted_at_spec_id"] == spec_id
    return ((done and cf) or (raised and (done or cf))
            or ((done or cf or raised) and not att)
            or (att and spec_id not in sw["intended_spec_ids"])
            or (rec and fail)
            or ((rec or fail) and not (done or cf or raised)))


def _outcome_ok(sw, o) -> bool:
    """The observation's verdict agrees with the sweep's outcome lists."""
    sid, v = o["spec_id"], o["verdict"]
    done = sid in sw["evaluation_completed_spec_ids"]
    if v in TRUTHFUL:
        return done
    if v == ho.COMPILE_FAILED:
        return sid in sw["compile_failed_spec_ids"]
    if o["verdict_reason"] == ho.EVALUATION_EXCEPTION:
        return sw["aborted_at_spec_id"] == sid
    return done


def _gaps(spec_id, obs, sweeps) -> list:
    """[(sweep_started_ms, reason, sweep_id)] history gaps for one spec."""
    gaps = []
    by_sweep = {o["sweep_id"]: o for o in obs}
    for o in obs:
        sw = sweeps.get(o["sweep_id"])
        if sw is None:
            gaps.append((o["started_ms"], SWEEP_RECORD_MISSING, o["sweep_id"]))
        elif (sw["started_ms"] != o["started_ms"]
              or spec_id not in sw["observation_recorded_spec_ids"]):
            gaps.append((o["started_ms"], SWEEP_RECORD_MISMATCH, o["sweep_id"]))
        elif not _outcome_ok(sw, o):
            gaps.append((o["started_ms"], VERDICT_OUTCOME_MISMATCH, o["sweep_id"]))
    for sw in sweeps.values():
        if _sweep_conflict(sw, spec_id):
            gaps.append((sw["started_ms"], SWEEP_RECORD_INCONSISTENT,
                         sw["sweep_id"]))
        elif spec_id in sw["observation_failed_spec_ids"]:
            gaps.append((sw["started_ms"], OBSERVATION_FAILED, sw["sweep_id"]))
        elif ((spec_id in sw["evaluation_completed_spec_ids"]
               or spec_id in sw["compile_failed_spec_ids"]
               or spec_id in sw["observation_recorded_spec_ids"])
              and sw["sweep_id"] not in by_sweep):
            gaps.append((sw["started_ms"], OBSERVATION_MISSING, sw["sweep_id"]))
    return sorted(gaps)


def _emit(spec_id, obs, gaps, out: Derivation) -> None:
    prior, unreadable = None, []
    base_ms = None                    # latest gap-free truthful baseline
    for o in obs:
        v = o["verdict"]
        if v in UNREADABLE:
            unreadable.append(o["event_id"])
            continue
        span = [g for g in gaps if (base_ms is None or g[0] > base_ms)
                and g[0] <= o["started_ms"]]
        # a gap since the baseline may hide a non-decayed verdict, so a
        # decayed run that crosses it is a candidate too — and refused
        if v == ho.DECAYED and (prior is None or prior["verdict"] != ho.DECAYED
                                or span):
            trigger = (FIRST_TRUTHFUL_DECAYED if prior is None
                       else TRANSITION_TO_DECAYED)
            if span:
                out.refusals.append(Refusal(spec_id, span[0][1], o["event_id"],
                                            span[0][2]))
            elif not _nonempty(o["health_fingerprint"]):
                out.refusals.append(Refusal(spec_id, SOURCE_FINGERPRINT_MISSING,
                                            o["event_id"], o["sweep_id"]))
            else:
                out.questions.append(build(trigger, o, prior, unreadable))
        prior, unreadable = o, []
        if not any(g[2] == o["sweep_id"] for g in gaps):
            base_ms = o["started_ms"]


# ── contract verification ────────────────────────────────────────────────
_HEX = frozenset("0123456789abcdef")


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _hex64(v) -> bool:
    return isinstance(v, str) and len(v) == 64 and set(v) <= _HEX


def _opt_str(v) -> bool:
    return v is None or isinstance(v, str)


def _thresholds_ok(v) -> bool:
    return isinstance(v, dict) and all(
        isinstance(k, str) and (_int(x) or isinstance(x, float))
        for k, x in v.items())


def _fail(code):
    raise ResearchQuestionError(code)


def _check_source(src):
    if not isinstance(src, dict) or set(src) != {"schema", "kind", *_SOURCE_FIELDS}:
        _fail("source_keys")
    if not (src["schema"] == SOURCE_SCHEMA and src["kind"] == SOURCE_KIND
            and _int(src["event_id"]) and src["event_id"] > 0
            and isinstance(src["event_ts"], str)
            and _hex64(src["record_sha256"])
            and _nonempty(src["sweep_id"])
            and _ms(src["sweep_started_at"]) is not None
            and _nonempty(src["spec_id"]) and _opt_str(src["spec_name"])
            and src["verdict"] == ho.DECAYED and src["verdict_reason"] is None
            and src["lifecycle_branch"] == ho.DECAYED
            and isinstance(src["retirement_action_selected"], bool)
            and _nonempty(src["health_fingerprint"])
            and all(_opt_str(src[k]) for k in (
                "health_fingerprint_schema", "spec_fingerprint",
                "spec_fingerprint_schema", "spec_content_sha256",
                "simulation_settings_sha256"))
            and _thresholds_ok(src["thresholds"])):
        _fail("source")


def _check_prior(prior, src):
    if not isinstance(prior, dict) or set(prior) != set(_PRIOR_FIELDS):
        _fail("prior_keys")
    if not (_int(prior["event_id"]) and 0 < prior["event_id"] < src["event_id"]
            and _hex64(prior["record_sha256"])
            and _nonempty(prior["sweep_id"])
            and prior["sweep_id"] != src["sweep_id"]
            and _ms(prior["sweep_started_at"]) is not None
            and _ms(prior["sweep_started_at"]) < _ms(src["sweep_started_at"])
            and prior["verdict"] in (ho.IDLE, ho.STILL_WORKING)
            and _opt_str(prior["health_fingerprint"])
            and _opt_str(prior["simulation_settings_sha256"])
            and _thresholds_ok(prior["thresholds"])):
        _fail("prior")


def from_json(text: str) -> dict:
    """Parse and verify a stored research-question.v1 on its own: exact keys
    and types at every level, canonical form, and every derived field
    (question_id, text, changes_since_prior) recomputed. It does NOT prove
    the bound evidence exists; `verify_evidence` does. Raises
    ResearchQuestionError."""
    try:
        rec = json.loads(text)
    except (TypeError, ValueError) as e:
        raise ResearchQuestionError("undecodable") from e
    if not isinstance(rec, dict) or set(rec) != set(_RECORD_KEYS):
        _fail("keys")
    if (rec["schema"] != SCHEMA or rec["question_kind"] != QUESTION_KIND
            or rec["trigger"] not in TRIGGERS or rec["semantics"] != SEMANTICS):
        _fail("contract")
    src, prior = rec["source"], rec["prior"]
    _check_source(src)
    if rec["scope"] != {"kind": "strategy", "spec_id": src["spec_id"]}:
        _fail("scope")
    if (rec["trigger"] == FIRST_TRUTHFUL_DECAYED) != (prior is None):
        _fail("trigger_prior")
    if prior is not None:
        _check_prior(prior, src)
    changes = rec["changes_since_prior"]
    if prior is None:
        if changes is not None:
            _fail("changes_since_prior")
    elif (not isinstance(changes, dict) or set(changes) != set(_CHANGE_FIELDS)
          # 0 == False in Python: require real booleans before comparing
          or any(type(v) is not bool for v in changes.values())
          or changes != _changes(prior, src)):
        _fail("changes_since_prior")
    unread = rec["unreadable_since_prior_event_ids"]
    lo = prior["event_id"] if prior else 0
    if (not isinstance(unread, list) or not all(_int(x) for x in unread)
            or unread != sorted(set(unread))
            or any(not lo < x < src["event_id"] for x in unread)):
        _fail("unreadable_since_prior_event_ids")
    if canonical(rec) != text:
        _fail("not_canonical")
    if rec["question_id"] != question_id(_identity(rec)):
        _fail("question_id")
    txt = question_text(rec["trigger"], src["spec_id"], src["sweep_id"],
                        prior and prior["verdict"], prior and prior["sweep_id"])
    if rec["question"] != {"template_id": TEMPLATE_ID, "text": txt}:
        _fail("question_text")
    return rec


def _bounded(spec_id, src, rows) -> list:
    """The health history a question at `src` was derived from: this spec's
    observation rows up to the source event, and every sweep record started
    no later than the source sweep. A sweep record that cannot be parsed has
    no trustworthy start, so it is placed by write order: included when
    written no later than the source sweep's own record. Unattributable spec
    rows (no usable subject) cannot be classified as another spec's
    evidence, so they are kept on the same write-order bound, as are rows
    of this spec without a usable event id; derive then refuses them.
    Later history and identifiable other specs' rows are excluded."""
    src_ms = _ms(src["sweep_started_at"])
    parsed, own_ids = [], []
    for r in rows:
        if not isinstance(r, dict) or r.get("kind") != ho.KIND_SWEEP:
            continue
        rec, bad = ho._decode(r)
        sw = None if bad else _sweep(rec)
        parsed.append((r, sw))
        if sw is not None and sw["sweep_id"] == src["sweep_id"] and _int(r.get("id")):
            own_ids.append(r["id"])
    if not own_ids:
        _fail("source_sweep_missing")
    own = max(own_ids)
    out = []
    for r in rows:
        if not isinstance(r, dict) or r.get("kind") != ho.KIND_SPEC:
            continue
        placed = _int(r.get("id"))
        if r.get("subject") == spec_id:
            if not placed or r["id"] <= src["event_id"]:
                out.append(r)
        elif not _nonempty(r.get("subject")):
            # cannot be classified as another spec's evidence: kept (and
            # refused by derive) unless written after the source sweep record
            if not placed or r["id"] <= own:
                out.append(r)
    for r, sw in parsed:
        if sw is not None:
            if sw["started_ms"] <= src_ms:
                out.append(r)
        elif not _int(r.get("id")) or r["id"] <= own:
            out.append(r)
    return out


def verify_evidence(q: dict, rows) -> None:
    """Check a from_json-verified question against the exact journal health
    rows: the source and prior rows exist under their event IDs with the
    bound raw-record hashes and re-derive exactly the stored source/prior
    content; every observation of the spec between prior (or the start of
    history) and source is exactly the recorded unreadable list; and
    `derive` over the supporting history bounded through the source sweep
    (see `_bounded`) produces exactly this question — so deleted or
    contradictory supporting sweeps are rejected, while later unrelated
    history is not consulted. Raises ResearchQuestionError."""
    spec_id = q["scope"]["spec_id"]
    by_id = {r.get("id"): r for r in rows
             if isinstance(r, dict) and r.get("kind") == ho.KIND_SPEC
             and r.get("subject") == spec_id}

    def obs_at(eid):
        row = by_id.get(eid)
        if row is None:
            _fail("evidence_missing")
        rec, bad = ho._decode(row)
        if bad:
            _fail("evidence_malformed")
        o, bad = _observation(row, rec)
        if bad:
            _fail("evidence_malformed")
        return o

    src, prior = q["source"], q["prior"]
    if _source(obs_at(src["event_id"])) != src:
        _fail("source_evidence_mismatch")
    if prior is not None and _prior(obs_at(prior["event_id"])) != prior:
        _fail("prior_evidence_mismatch")
    lo = prior["event_id"] if prior else 0
    between = sorted(e for e in by_id
                     if _int(e) and lo < e < src["event_id"])
    if between != q["unreadable_since_prior_event_ids"]:
        _fail("unreadable_evidence_mismatch")
    for e in between:
        if obs_at(e)["verdict"] not in UNREADABLE:
            _fail("unreadable_evidence_mismatch")
    d = derive(_bounded(spec_id, src, rows))
    if q not in d.questions:
        why = next((r.reason for r in d.refusals
                    if r.spec_id in (None, spec_id)), "not_derived")
        _fail(f"derivation_mismatch:{why}")


# ── durable record ───────────────────────────────────────────────────────
def row_for(q: dict) -> dict:
    return {"question_id": q["question_id"], "schema": q["schema"],
            "question_kind": q["question_kind"], "trigger": q["trigger"],
            "scope_kind": q["scope"]["kind"], "scope_id": q["scope"]["spec_id"],
            "source_kind": q["source"]["kind"],
            "source_event_id": q["source"]["event_id"],
            "canonical_json": canonical(q)}


def record_from_journal(journal, now_ms: int) -> dict:
    """Derive from the journal's health rows and persist each question.
    Returns {"inserted"|"duplicate"|"conflict": [question_id], "refusals":
    [Refusal]}. Not wired into any live path."""
    d = derive(journal.strategy_health_rows())
    res = {"inserted": [], "duplicate": [], "conflict": [],
           "refusals": list(d.refusals)}
    for q in d.questions:
        status = journal.record_research_question(row_for(q),
                                                  recorded_at_ms=now_ms)
        res[status].append(q["question_id"])
    return res


def load(journal, spec_id: str | None = None) -> list:
    """Stored questions, each verified by from_json, its row projection and
    verify_evidence against the journal's current health rows. Raises
    ResearchQuestionError on the first question that fails."""
    rows = journal.strategy_health_rows()
    out = []
    for r in journal.research_questions(scope_id=spec_id):
        q = from_json(r["canonical_json"])
        if row_for(q) != {k: r[k] for k in row_for(q)}:
            _fail("row_projection")
        verify_evidence(q, rows)
        out.append(q)
    return out
