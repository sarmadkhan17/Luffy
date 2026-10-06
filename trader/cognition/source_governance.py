"""Offline discovery/adoption ledger, never an acquisition or spend capability.

Discovery records unverified descriptors in the existing brain event journal.
Catalog adoption permits research planning only. An adapter still requires an
explicit engineering registration; neither a catalog entry nor a claim can
extend external_sources' retrieval allowlist. Paid adoption has no approval
issuer here and always stops for owner approval under SDD 2.7/12.4.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

SCHEMA = "research-source-discovery.v1"
DISCOVERY = SCHEMA
DECISION = "research-source-adoption.v1"
SCOPE = "strategy_decay_research"
SOURCE_CLASS = "academic_factors"
FIELDS = ("type", "domains", "primary_secondary", "credibility_class",
          "cost", "access_method", "historical_depth", "rate_limits",
          "known_weaknesses")
ACCESS = ("PUBLIC_FREE", "PAID", "CREDENTIAL_REQUIRED")
JUSTIFICATION = ("capability_or_hypothesis", "existing_sources_insufficient",
                 "expected_value", "ongoing_cost")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _text(value):
    return type(value) is str and bool(value.strip())


def validate(candidate):
    """Exact descriptive contract: credentials/approval/authority are not inputs."""
    keys = {"schema", "source_id", "source_class", "access_class", "scope",
            "metadata", "discovery_provenance"}
    if type(candidate) is not dict or set(candidate) != keys:
        raise ValueError("source_descriptor_keys")
    if candidate["schema"] != SCHEMA:
        raise ValueError("source_descriptor_schema")
    if (type(candidate["source_id"]) is not str
            or not re.fullmatch(r"external\.[a-z0-9_.-]{1,128}", candidate["source_id"])):
        raise ValueError("source_id")
    if not all(_text(candidate[k]) for k in
               ("source_class", "scope", "discovery_provenance")):
        raise ValueError("source_descriptor_text")
    if candidate["access_class"] not in ACCESS:
        raise ValueError("source_access_class")
    meta = candidate["metadata"]
    if type(meta) is not dict or set(meta) != set(FIELDS):
        raise ValueError("source_metadata_fields")
    for field, fact in meta.items():
        if type(fact) is not dict or set(fact) != {"state", "value", "reason"}:
            raise ValueError("source_metadata_fact:" + field)
        if fact["state"] == "RECORDED":
            if fact["value"] is None or fact["reason"] is not None:
                raise ValueError("source_metadata_recorded:" + field)
        elif fact["state"] in ("NOT_ASSESSED", "NOT_MEASURED", "UNAVAILABLE"):
            if fact["value"] is not None or not _text(fact["reason"]):
                raise ValueError("source_metadata_unknown:" + field)
        else:
            raise ValueError("source_metadata_state:" + field)
    cost = meta["cost"]
    if cost["state"] == "RECORDED":
        value = cost["value"]
        amount = value.get("paid_request_usd") if type(value) is dict else None
        if (type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0
                or not _text(value.get("provenance"))):
            raise ValueError("source_cost")
    text = canonical(candidate)
    if len(text) > 65536:
        raise ValueError("source_descriptor_bound")
    return json.loads(text)


def assessment(candidate, requested_scope):
    """Deterministic catalog-only policy; no external approval is accepted."""
    c = validate(candidate)
    reason = "catalog_adoption_allowed"
    if c["scope"] != requested_scope or requested_scope != SCOPE:
        reason = "source_scope_not_authorized"
    elif c["source_class"] != SOURCE_CLASS:
        reason = "source_class_not_authorized"
    elif c["metadata"]["cost"]["state"] != "RECORDED":
        reason = "source_cost_unknown"
    elif (c["access_class"] == "PAID"
          or c["metadata"]["cost"]["value"]["paid_request_usd"] > 0):
        reason = "owner_paid_access_approval_required"
    elif c["access_class"] == "CREDENTIAL_REQUIRED":
        reason = "future_sec01_live_access_required"
    return {"schema": DECISION, "candidate_sha256": digest(c),
            "requested_scope": requested_scope, "reason": reason,
            "status": "ADOPTED_CATALOG_ONLY" if reason == "catalog_adoption_allowed" else "REFUSED",
            "authority": "RESEARCH_ONLY", "trading_authority": False,
            "spending_authority": False, "acquisition_authority": False,
            "paid_approval_requirements": {"issuer": "OWNER",
                "justification_fields": list(JUSTIFICATION),
                "approval_issuer_implemented": False},
            "limitations": ["discovery_metadata_is_unverified",
                            "catalog_adoption_does_not_register_a_retrieval_adapter",
                            "claims_and_credentials_are_not_authority"]}


def _read(journal, kind, identity):
    rows = journal.query("SELECT detail FROM brain_events WHERE kind=? AND subject=? ORDER BY id",
                         (kind, identity))
    if not rows:
        return None
    values = [json.loads(r["detail"]) for r in rows]
    if any(v != values[0] for v in values):
        raise ValueError("source_ledger_conflict")
    return values[0]


def discover(journal, candidate):
    """Record a bounded descriptor from local input, without fetching the source."""
    c = validate(candidate)
    identity = digest(c)
    previous = _read(journal, DISCOVERY, identity)
    if previous is not None and previous != c:
        raise ValueError("source_discovery_identity")
    if previous is None:
        journal.log_brain_event(DISCOVERY, identity, c)
    return identity


def adopt(journal, candidate_sha256, requested_scope):
    """Reopen discovery, verify bytes, then retain an idempotent policy decision."""
    c = _read(journal, DISCOVERY, candidate_sha256)
    if c is None:
        raise ValueError("source_not_discovered")
    if digest(validate(c)) != candidate_sha256:
        raise ValueError("source_discovery_identity")
    decision = assessment(c, requested_scope)
    identity = digest({"candidate_sha256": candidate_sha256, "requested_scope": requested_scope,
                       "policy": DECISION})
    previous = _read(journal, DECISION, identity)
    if previous is not None and previous != decision:
        raise ValueError("source_adoption_identity")
    if previous is None:
        journal.log_brain_event(DECISION, identity, decision)
    return decision


def catalog(journal, requested_scope=SCOPE):
    """Read-only bounded registry view; discovery is never implicit adoption."""
    rows = journal.query("SELECT DISTINCT subject FROM brain_events WHERE kind=? ORDER BY subject LIMIT 1025",
                         (DISCOVERY,))
    if len(rows) > 1024:
        raise ValueError("source_catalog_capacity")
    result = []
    for row in rows:
        identity = row["subject"]
        c = validate(_read(journal, DISCOVERY, identity))
        if digest(c) != identity:
            raise ValueError("source_discovery_identity")
        key = digest({"candidate_sha256": identity, "requested_scope": requested_scope,
                      "policy": DECISION})
        decision = _read(journal, DECISION, key)
        if decision is not None and decision != assessment(c, requested_scope):
            raise ValueError("source_adoption_identity")
        result.append({"candidate_sha256": identity, "descriptor": c,
                       "adoption": decision, "authority": "RESEARCH_ONLY"})
    return result
