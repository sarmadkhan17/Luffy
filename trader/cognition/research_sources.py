"""Internal research source registry — research-source-registry.v1.

A frozen, versioned catalog of the internal evidence sources the offline
strategy-decay research chain already reads (SDD 12.4 Source Registry,
internal sources only). It describes sources; it reads none of them, and it
adds no source: no external source, network, search, LLM, Attention, Risk,
Execution or trading authority.

Entries, ordered by ``source_id``:

- ``internal.research_questions``         — research-question.v1 rows;
- ``internal.strategy_health_observations`` — strategy-health-observation.v1
  records in ``journal.brain_events``;
- ``internal.strategy_signal_decisions``  — raw ``journal.decisions`` rows;
- ``internal.worldmodel_regime_state``    — UNAVAILABLE: it exists only to
  state that no truthful WorldModel regime source exists. It has no store
  and no reader, and asking for its descriptor fails.

Plan descriptor. Each AVAILABLE entry carries the exact
``{store, reader, record_schema}`` descriptor research-plan.v1 embeds in its
evidence items, so plans built through the registry are byte-identical to
plans built before it existed.

SDD 12.4 metadata. Every field is a fact ``{state, value, reason}``.
``RECORDED`` carries a value backed by repository evidence and no reason;
``NOT_ASSESSED``, ``NOT_MEASURED`` and ``UNAVAILABLE`` carry no value and an
exact reason. Credibility class, primary/secondary, cost, historical depth
and rate limits have no repository evidence for these sources, so v1 refuses
any RECORDED value for them rather than inventing one. Known weaknesses are
recorded only as verbatim statements with their provenance.

Immutability. The definition is validated and frozen into one canonical
JSON text at import, and its SHA-256 must equal the pinned
``REGISTRY_SHA256``: any change to the definition without a deliberate new
pin fails import. Every accessor re-checks that pin and returns fresh
copies, so callers cannot mutate the registry through a returned value.
Unknown source IDs fail closed.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_question as rq
from trader.strategy import health_observation as ho

SCHEMA = "research-source-registry.v1"

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
STATUSES = (AVAILABLE, UNAVAILABLE)

RECORDED = "RECORDED"
NOT_ASSESSED = "NOT_ASSESSED"
NOT_MEASURED = "NOT_MEASURED"
FACT_STATES = (RECORDED, NOT_ASSESSED, NOT_MEASURED, UNAVAILABLE)

INTERNAL_STORE = "internal_journal_table"

HEALTH_SOURCE_ID = "internal.strategy_health_observations"
QUESTION_SOURCE_ID = "internal.research_questions"
DECISIONS_SOURCE_ID = "internal.strategy_signal_decisions"
WORLDMODEL_REGIME_SOURCE_ID = "internal.worldmodel_regime_state"

NO_TRUTHFUL_WORLDMODEL_SOURCE = "no_truthful_worldmodel_source"
REGIME_UNAVAILABLE_DETAIL = (
    "no truthful WorldModel regime source: the live Kernel supplies no "
    "WorldModel and no point-in-time regime state is persisted for strategy "
    "health sweeps; Analyst regime_filter/regime_fitness are backtest filter "
    "settings, not an observed regime state")

SEMANTICS = ("catalog of internal evidence sources already read by the "
             "offline strategy-decay research chain; describes sources and "
             "reads none; no credibility, cost, budget, historical depth or "
             "rate limit is asserted without repository evidence; no "
             "external source, network, search, LLM, Attention, Risk, "
             "Execution or trading authority")

# ── contract ─────────────────────────────────────────────────────────────
_RECORD_KEYS = ("schema", "sources", "semantics")
_ENTRY_KEYS = ("source_id", "status", "plan_descriptor", "unavailable",
               "metadata")
_DESCRIPTOR_KEYS = ("store", "reader", "record_schema")
_UNAVAILABLE_KEYS = ("reason", "detail")
_FACT_KEYS = ("state", "value", "reason")
_WEAKNESS_KEYS = ("text", "provenance")
#: SDD 12.4 fields (``source_id`` is the entry key)
METADATA_FIELDS = ("type", "domains", "primary_secondary",
                   "credibility_class", "cost", "access_method",
                   "historical_depth", "rate_limits", "known_weaknesses")
#: fields with no repository evidence for any v1 source: never RECORDED
UNSUPPORTED_FIELDS = ("primary_secondary", "credibility_class", "cost",
                      "historical_depth", "rate_limits")


class ResearchSourceError(ValueError):
    """A registry definition or source lookup failed the contract."""


def _fail(why: str):
    raise ResearchSourceError(why)


def canonical(payload) -> str:
    return rq.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ── definition ───────────────────────────────────────────────────────────
def _fact(state, value=None, reason=None) -> dict:
    return {"state": state, "value": value, "reason": reason}


_NO_CREDIBILITY = ("no credibility-class policy exists for internal "
                   "research sources")
_NO_PRIMARY = ("no primary/secondary classification policy exists for "
               "internal research sources")
_NO_COST = ("no per-source cost is measured; research-run.v1 telemetry is "
            "run-wide and is not attributed to a source")
_NO_DEPTH = "historical depth of this store is not measured by the registry"
_NO_RATE_LIMIT = ("no rate-limit policy is declared for reads of this "
                  "internal store")
_NO_WEAKNESS = "no known weakness of this source is recorded in the repository"


def _internal(source_id, descriptor, weaknesses) -> dict:
    known = (_fact(RECORDED, [{"text": t, "provenance": p}
                              for t, p in weaknesses])
             if weaknesses else _fact(NOT_ASSESSED, reason=_NO_WEAKNESS))
    return {"source_id": source_id, "status": AVAILABLE,
            "plan_descriptor": descriptor, "unavailable": None,
            "metadata": {
                "type": _fact(RECORDED, INTERNAL_STORE),
                "domains": _fact(RECORDED, [rq.QUESTION_KIND]),
                "primary_secondary": _fact(NOT_ASSESSED, reason=_NO_PRIMARY),
                "credibility_class": _fact(NOT_ASSESSED,
                                           reason=_NO_CREDIBILITY),
                "cost": _fact(NOT_MEASURED, reason=_NO_COST),
                "access_method": _fact(RECORDED, descriptor["reader"]),
                "historical_depth": _fact(NOT_MEASURED, reason=_NO_DEPTH),
                "rate_limits": _fact(NOT_ASSESSED, reason=_NO_RATE_LIMIT),
                "known_weaknesses": known}}


_DEFINITION = {
    "schema": SCHEMA,
    "semantics": SEMANTICS,
    "sources": [
        _internal(QUESTION_SOURCE_ID,
                  {"store": "journal.research_questions",
                   "reader": "Journal.research_questions",
                   "record_schema": rq.SCHEMA}, ()),
        _internal(HEALTH_SOURCE_ID,
                  {"store": "journal.brain_events",
                   "reader": "Journal.strategy_health_rows",
                   "record_schema": ho.SCHEMA},
                  (("decayed is the health sweep's lenient retirement "
                    "trigger on a recent close-fill backtest, not a proven "
                    "loss of edge",
                    "trader/cognition/research_question.py SEMANTICS"),)),
        # raw decision columns: no record schema, these are journal rows,
        # not a derived summary
        _internal(DECISIONS_SOURCE_ID,
                  {"store": "journal.decisions",
                   "reader": "Journal.decision_observation_rows",
                   "record_schema": None},
                  (("Decision evidence is a declared selection, not "
                    "independent opportunity evidence.",
                    "STATE.yaml completed_strategy_decay_research_plan_v1 "
                    "limitations"),
                   ("The decision route is a declared selection, not bound "
                    "content.",
                    "trader/cognition/research_plan.py module docstring"))),
        {"source_id": WORLDMODEL_REGIME_SOURCE_ID, "status": UNAVAILABLE,
         "plan_descriptor": None,
         "unavailable": {"reason": NO_TRUTHFUL_WORLDMODEL_SOURCE,
                         "detail": REGIME_UNAVAILABLE_DETAIL},
         "metadata": {f: _fact(UNAVAILABLE,
                               reason=NO_TRUTHFUL_WORLDMODEL_SOURCE)
                      for f in METADATA_FIELDS}},
    ],
}


# ── validation ───────────────────────────────────────────────────────────
def _keys(d, keys, what):
    if not isinstance(d, dict) or set(d) != set(keys):
        _fail(f"{what}_keys")


def _text(v) -> bool:
    return type(v) is str and v.strip() != ""


def _check_fact(field, fact, sid):
    what = f"fact:{sid}:{field}"
    _keys(fact, _FACT_KEYS, what)
    state, value, reason = fact["state"], fact["value"], fact["reason"]
    if state not in FACT_STATES:
        _fail(f"{what}:state")
    if state != RECORDED:
        if value is not None or not _text(reason):
            _fail(f"{what}:unrecorded_shape")
        return
    if reason is not None:
        _fail(f"{what}:recorded_shape")
    if field in UNSUPPORTED_FIELDS:
        _fail(f"{what}:unsupported_value")
    if field in ("type", "access_method"):
        if not _text(value):
            _fail(f"{what}:value")
    elif field == "domains":
        if (type(value) is not list or not value
                or not all(_text(x) for x in value)
                or value != sorted(set(value))):
            _fail(f"{what}:value")
    elif field == "known_weaknesses":
        if type(value) is not list or not value:
            _fail(f"{what}:value")
        for w in value:
            _keys(w, _WEAKNESS_KEYS, f"{what}:weakness")
            if not (_text(w["text"]) and _text(w["provenance"])):
                _fail(f"{what}:weakness")


def _check_entry(e):
    _keys(e, _ENTRY_KEYS, "entry")
    sid = e["source_id"]
    if not _text(sid):
        _fail("entry:source_id")
    status, desc, unav = e["status"], e["plan_descriptor"], e["unavailable"]
    if status == AVAILABLE:
        if unav is not None:
            _fail(f"entry:{sid}:available_shape")
        _keys(desc, _DESCRIPTOR_KEYS, f"descriptor:{sid}")
        if not (_text(desc["store"]) and _text(desc["reader"])
                and (desc["record_schema"] is None
                     or _text(desc["record_schema"]))):
            _fail(f"descriptor:{sid}:value")
    elif status == UNAVAILABLE:
        if desc is not None:
            _fail(f"entry:{sid}:unavailable_shape")
        _keys(unav, _UNAVAILABLE_KEYS, f"unavailable:{sid}")
        if not (_text(unav["reason"]) and _text(unav["detail"])):
            _fail(f"unavailable:{sid}:value")
    else:
        _fail(f"entry:{sid}:status")
    _keys(e["metadata"], METADATA_FIELDS, f"metadata:{sid}")
    for f in METADATA_FIELDS:
        _check_fact(f, e["metadata"][f], sid)
    meta = e["metadata"]
    if status == AVAILABLE:
        if (meta["access_method"]["state"] == RECORDED
                and meta["access_method"]["value"] != desc["reader"]):
            _fail(f"metadata:{sid}:access_method")
    elif any(meta[f]["state"] == RECORDED for f in METADATA_FIELDS):
        # an unavailable source has nothing to record
        _fail(f"metadata:{sid}:unavailable_recorded")


def validate(definition) -> str:
    """Canonical JSON of a contract-valid registry definition, or
    ResearchSourceError: exact nested keys and types, sources strictly
    ordered by unique source_id, unique AVAILABLE stores, no RECORDED value
    for an unsupported field. Deterministic."""
    _keys(definition, _RECORD_KEYS, "registry")
    if definition["schema"] != SCHEMA:
        _fail("registry:schema")
    if not _text(definition["semantics"]):
        _fail("registry:semantics")
    sources = definition["sources"]
    if type(sources) is not list or not sources:
        _fail("registry:sources")
    for e in sources:
        _check_entry(e)
    ids = [e["source_id"] for e in sources]
    if len(set(ids)) != len(ids):
        _fail("registry:duplicate_source_id")
    if ids != sorted(ids):
        _fail("registry:order")
    stores = [e["plan_descriptor"]["store"] for e in sources
              if e["status"] == AVAILABLE]
    if len(set(stores)) != len(stores):
        _fail("registry:duplicate_store")
    try:
        return canonical(definition)
    except (TypeError, ValueError) as e:
        raise ResearchSourceError("registry:not_serializable") from e


_FROZEN = validate(_DEFINITION)
del _DEFINITION
#: pinned identity of research-source-registry.v1; a definition change
#: without a deliberate new pin fails import
REGISTRY_SHA256 = (
    "17b68ba56d2e8d925d1448910527a7d7a7b9f5460601743fe88eb549e25e928e")
if _sha(_FROZEN) != REGISTRY_SHA256:
    raise ResearchSourceError("registry:pin_mismatch")


# ── accessors ────────────────────────────────────────────────────────────
def _registry() -> dict:
    if _sha(_FROZEN) != REGISTRY_SHA256:
        _fail("registry:pin_mismatch")
    return json.loads(_FROZEN)


def registry() -> dict:
    """A fresh copy of the frozen registry."""
    return _registry()


def canonical_registry() -> str:
    """The frozen registry's canonical JSON text."""
    _registry()
    return _FROZEN


def source_ids() -> tuple:
    return tuple(e["source_id"] for e in _registry()["sources"])


def get(source_id: str) -> dict:
    """A fresh copy of one entry; an unknown source_id fails closed."""
    for e in _registry()["sources"]:
        if e["source_id"] == source_id:
            return e
    _fail(f"unknown_source:{source_id!r}")


def descriptor(source_id: str) -> dict:
    """The exact plan-facing ``{store, reader, record_schema}`` descriptor
    of an AVAILABLE source. An unknown source fails closed; an UNAVAILABLE
    source has no store or reader and fails with its recorded reason."""
    e = get(source_id)
    if e["status"] != AVAILABLE:
        _fail(f"source_unavailable:{source_id}:{e['unavailable']['reason']}")
    return e["plan_descriptor"]


def unavailable(source_id: str) -> dict:
    """``{reason, detail}`` of an UNAVAILABLE source; anything else fails."""
    e = get(source_id)
    if e["status"] != UNAVAILABLE:
        _fail(f"source_not_unavailable:{source_id}")
    return e["unavailable"]


def _descriptor_ok(desc) -> bool:
    """Exact descriptor keys and value types, checked before any
    serialization so a malformed value (set, NaN, ...) cannot raise
    anything but ResearchSourceError."""
    return (type(desc) is dict and set(desc) == set(_DESCRIPTOR_KEYS)
            and _text(desc["store"]) and _text(desc["reader"])
            and (desc["record_schema"] is None
                 or _text(desc["record_schema"])))


def resolve(desc) -> str:
    """The source_id of the AVAILABLE entry whose descriptor equals
    ``desc`` exactly (keys, values and types); fails closed otherwise."""
    if _descriptor_ok(desc):
        text = canonical(desc)
        for e in _registry()["sources"]:
            if (e["status"] == AVAILABLE
                    and canonical(e["plan_descriptor"]) == text):
                return e["source_id"]
    _fail("unregistered_descriptor")


def require(source_id: str, desc) -> None:
    """Fails closed unless ``desc`` is exactly the descriptor of the
    AVAILABLE entry ``source_id`` — membership alone is not enough: another
    registered descriptor in its place is a mismatch."""
    expected = descriptor(source_id)
    if not _descriptor_ok(desc) or canonical(desc) != canonical(expected):
        _fail(f"descriptor_mismatch:{source_id}")
