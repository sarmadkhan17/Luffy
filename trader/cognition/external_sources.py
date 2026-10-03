"""External extension of the research source abstraction; no source is truth.

The internal research-source-registry.v1 and its pinned bytes stay unchanged.
This catalog reuses its fact states and SDD metadata fields. Selection is
free-only; this package has no paid-source adoption/approval issuer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

from trader.cognition import research_question as Q, research_sources as S

SCHEMA = "external-research-source-registry.v1"
ROUTER = "question-source-router.v1"
DOC = "https://www.crossref.org/documentation/retrieve-metadata/rest-api/"


def digest(value):
    return hashlib.sha256(Q.canonical(value).encode()).hexdigest()


def fact(value=None, *, reason=None, state=S.RECORDED):
    return {"state": state, "value": value, "reason": reason}


@dataclass(frozen=True)
class ExternalSource:
    source_id: str
    source_class: str
    status: str
    metadata_json: str
    retriever: str

    @property
    def metadata(self):
        return json.loads(self.metadata_json)

    def record(self):
        return {**asdict(self), "metadata": self.metadata}


_METADATA = {
    "type": fact("scholarly_metadata"),
    "domains": fact(["api.crossref.org", "doi.org"]),
    "primary_secondary": fact("secondary_index_of_publisher_deposits"),
    "credibility_class": fact(None, state=S.NOT_ASSESSED,
                              reason="bibliographic_index_is_not_claim_validation"),
    "cost": fact({"paid_request_usd": 0, "access": "public_no_signup", "provenance": DOC}),
    "access_method": fact({"method": "HTTPS_GET_JSON", "endpoint": "https://api.crossref.org/works",
                           "provenance": DOC}),
    "historical_depth": fact(None, state=S.NOT_MEASURED, reason="coverage_not_measured"),
    "rate_limits": fact(None, state=S.NOT_ASSESSED,
                        reason="provider_limits_are_dynamic; local_adapter_spacing_s=1"),
    "known_weaknesses": fact([{"text": "Metadata can be incomplete; abstracts may be absent. No full-text or validity claim.",
                               "provenance": DOC}]),
}
assert set(_METADATA) == set(S.METADATA_FIELDS)
_SOURCE = ExternalSource("external.crossref.public", "academic_factors", S.AVAILABLE,
                         Q.canonical(_METADATA), "crossref_public.v1")
REGISTRY_JSON = Q.canonical({"schema": SCHEMA, "sources": [_SOURCE.record()]})
REGISTRY_SHA256 = hashlib.sha256(REGISTRY_JSON.encode()).hexdigest()


def source(source_id):
    if source_id != _SOURCE.source_id:
        raise S.ResearchSourceError("unsupported_external_source")
    # Frozen JSON is the authority; fresh dataclass/copies on every read.
    rec = json.loads(REGISTRY_JSON)["sources"][0]
    return ExternalSource(**{k: rec[k] for k in ExternalSource.__dataclass_fields__})


def select(source_class):
    if source_class != "academic_factors":
        raise S.ResearchSourceError("unsupported_source_class")
    src = source(_SOURCE.source_id)
    if src.status != S.AVAILABLE or src.metadata["cost"]["value"]["paid_request_usd"] != 0:
        raise S.ResearchSourceError("paid_or_unavailable_source_not_authorized")
    return (src,)


def route(question):
    """Only registered question contracts/scopes are supported, never prose guessing."""
    if (question.get("schema"), question.get("question_kind"), question.get("scope", {}).get("kind")) != (
            Q.SCHEMA, Q.QUESTION_KIND, "strategy"):
        raise S.ResearchSourceError("unsupported_question_scope")
    Q.from_json(Q.canonical(question))
    classes = ["academic_factors"]
    return {"schema": ROUTER, "question_id": question["question_id"],
            "question_sha256": digest(question), "scope": question["scope"],
            "source_classes": classes, "registry_sha256": REGISTRY_SHA256,
            "sources": [s.record() for c in classes for s in select(c)],
            "queries": ["trading strategy performance decay market regime",
                        "financial market trading strategy persistence"],
            "authority": "RESEARCH_ONLY", "truth_assessment": "NOT_ASSESSED"}
