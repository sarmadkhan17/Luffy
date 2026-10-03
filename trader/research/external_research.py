"""Opt-in question-driven external evidence worker, independent of Kernel.

python -m trader.research.external_research --enable --once --journal WORKER_DB \
    --question-shadow-db SHADOW_DB --question-source-db SOURCE_DB

Reads existing registered research-question.v1, uses the source router/catalog,
and files an external-evidence extension in the existing Research Bank table.
Network work runs in the existing low-priority, deadline-killed child runner.
No LLM, venue, strategy creation, predictive validation, or activation caller.
Checkpoints use the existing journal event store. A crash after a request claim
leaves INTERRUPTED evidence on recovery: no automatic retry of uncertain work.
Negative/empty attempts are retained per question; independent questions search
again unless registered scheduling policy defers them. Positive retained content
can be reused with exact new-search metadata binding and original timestamps.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from typing import Callable, Mapping
import fcntl
import hashlib
import json
import math
import re
import sqlite3
import time
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl

from trader.cognition import external_sources as S, research_question as Q
from trader.core.child import run_child
from trader.core.journal import Journal

BANK_SCHEMA = "external-evidence-bank-object.v1"
BUILDER = "question-driven-external-evidence.v1"
START, DONE = "external_research_request_started.v1", "external_research_request_done.v1"
MAX_CHECKPOINTS, MAX_HISTORY = 8192, 512


@dataclass(frozen=True)
class RetryEligibility:
    allowed: bool
    reason: str

    def __post_init__(self):
        if type(self.allowed) is not bool or not isinstance(self.reason, str) or not self.reason:
            raise ValueError("invalid_retry_eligibility")


@dataclass(frozen=True)
class ReconsiderationPolicy:
    """Registered future scheduling authority; never rewrites attempt history."""
    policy_id: str
    version: str
    evaluate: Callable[[dict, tuple, int], RetryEligibility]


# Deliberately empty: historical failure alone grants no suppression authority.
RECONSIDERATION_POLICIES: Mapping[str, ReconsiderationPolicy] = {}


def retry_eligibility(journal, q, registry, policy_id):
    if policy_id is None:
        return RetryEligibility(True, "no_registered_policy_independent_question_allowed")
    policy = registry.get(policy_id)
    if not isinstance(policy, ReconsiderationPolicy) or policy.policy_id != policy_id or not policy.version:
        raise ValueError("unregistered_reconsideration_policy")
    rows = journal.query("SELECT * FROM research_bank_objects WHERE schema=? ORDER BY rowid LIMIT 1025", (BANK_SCHEMA,))
    if len(rows) > 1024:
        raise ValueError("external_bank_capacity")
    history = tuple(verify_bank(journal, r) for r in rows)
    decision = policy.evaluate(json.loads(canonical(q)), history, int(time.time()*1000))
    if not isinstance(decision, RetryEligibility):
        raise ValueError("invalid_retry_eligibility")
    return decision


@dataclass(frozen=True)
class Bounds:
    max_queries: int = 1
    max_pages: int = 3
    max_metadata: int = 10
    max_response_bytes: int = 131072
    max_passage_chars: int = 2000
    max_seconds: float = 30
    max_cost_usd: float = 0

    def __post_init__(self):
        for name, lo, hi in (("max_queries", 1, 2), ("max_pages", 1, 8),
                             ("max_metadata", 1, 20), ("max_response_bytes", 1024, 262144),
                             ("max_passage_chars", 128, 4000)):
            v = getattr(self, name)
            if type(v) is not int or not lo <= v <= hi:
                raise ValueError("bound:" + name)
        if (type(self.max_seconds) not in (int, float) or not math.isfinite(self.max_seconds)
                or not .2 <= self.max_seconds <= 60):
            raise ValueError("bound:max_seconds")
        if type(self.max_cost_usd) not in (int, float) or self.max_cost_usd != 0:
            raise ValueError("paid_spend_not_authorized")


def canonical(value):
    return Q.canonical(value)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def normalize_url(url):
    """Keep meaningful query parameters; drop fragments/tracking only."""
    p = urlsplit(url)
    if p.scheme.lower() != "https" or p.username or p.password or p.port not in (None, 443):
        raise ValueError("unsupported_url")
    if p.hostname not in ("doi.org", "api.crossref.org"):
        raise ValueError("source_domain_mismatch")
    params = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")]
    return urlunsplit(("https", p.hostname, p.path.rstrip("/"), urlencode(sorted(params)), ""))


def request_url(operation, arguments):
    if operation == "search":
        return "https://api.crossref.org/works?" + urlencode({
            "query.bibliographic": arguments["query"], "rows": arguments["limit"]})
    if operation == "retrieve" and re.fullmatch(r"10\.\d{4,9}/\S{1,200}", arguments["doi"]):
        return "https://api.crossref.org/works/" + quote(arguments["doi"], safe="")
    raise ValueError("unsupported_retrieval")


def _metadata(item):
    if not isinstance(item, dict):
        raise ValueError("malformed_metadata")
    doi = item.get("DOI", "")
    titles = item.get("title", [])
    if not isinstance(doi, str) or not re.fullmatch(r"10\.\d{4,9}/\S{1,200}", doi):
        raise ValueError("invalid_doi")
    if not isinstance(titles, list) or not titles or not isinstance(titles[0], str):
        raise ValueError("missing_title")
    abstract = item.get("abstract")
    return {"doi": doi.lower(), "url": "https://doi.org/" + doi.lower(),
            "title": titles[0][:1024], "type": str(item.get("type", "UNKNOWN"))[:64],
            "abstract": abstract[:32000] if isinstance(abstract, str) else None}


def crossref_request(operation, arguments, max_bytes, timeout_s):
    """Only public Crossref endpoints, streamed bytes cap, no redirects/auth/retries."""
    import requests
    url = request_url(operation, arguments)
    data, received, status = bytearray(), 0, None
    try:
        with requests.Session() as session:
            session.trust_env = False  # no ambient credentials, proxies or netrc
            with session.get(url, headers={"User-Agent": "LUFFY-Research/1.0", "Accept": "application/json"},
                             stream=True, allow_redirects=False, timeout=min(timeout_s, 10)) as response:
                status = response.status_code
                if status != 200:
                    return dict(status="UNAVAILABLE", reason=f"http_status:{status}", bytes=0,
                                url=url, http_status=status, content_sha256=None)
                for chunk in response.iter_content(8192):
                    received += len(chunk)
                    if received > max_bytes:
                        return dict(status="UNAVAILABLE", reason="response_byte_bound", bytes=received,
                                    url=url, http_status=status, content_sha256=None)
                    data.extend(chunk)
        content_hash = hashlib.sha256(data).hexdigest()
        body = json.loads(data)
        message = body["message"]
        if operation == "search":
            items = message["items"]
            if not isinstance(items, list):
                raise ValueError("malformed_search")
            metadata, rejected = [], 0
            for item in items[:arguments["limit"]]:
                try:
                    metadata.append(_metadata(item))
                except ValueError:
                    rejected += 1
            # Entirely malformed results are not a quiet empty search.
            state = "UNAVAILABLE" if items and not metadata else ("OK" if metadata else "EMPTY")
            return dict(status=state, reason="malformed_metadata" if state == "UNAVAILABLE" else None,
                        metadata=metadata, rejected=rejected, bytes=received, content_sha256=content_hash,
                        url=url, http_status=status)
        item = _metadata(message)
        if item["doi"] != arguments["doi"]:
            raise ValueError("retrieval_identity_mismatch")
        return dict(status="OK", reason=None, metadata=item, bytes=received,
                    content_sha256=content_hash, url=url, http_status=status)
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        return dict(status="UNAVAILABLE", reason=type(exc).__name__, bytes=received,
                    url=url, http_status=status, content_sha256=None)


def _checkpoint(journal, kind, key):
    rows = journal.query("SELECT detail FROM brain_events WHERE kind=? AND subject=? ORDER BY id LIMIT 2",
                         (kind, key))
    if not rows:
        return None
    if len(rows) != 1 or len(rows[0]["detail"]) > 1048576:
        raise ValueError("checkpoint_conflict")
    rec = json.loads(rows[0]["detail"])
    body = {k: v for k, v in rec.items() if k != "sha256"}
    if rec["sha256"] != sha(canonical(body)) or rec["key"] != key or rec["schema"] != kind:
        raise ValueError("checkpoint_integrity")
    if sha(canonical(body["identity"])) != key:
        raise ValueError("checkpoint_identity")
    return body


def _write(journal, kind, key, body):
    count = journal.query("SELECT COUNT(*) n FROM brain_events WHERE kind IN (?,?)", (START, DONE))[0]["n"]
    if count >= MAX_CHECKPOINTS:
        raise ValueError("checkpoint_capacity")
    body = dict(body, schema=kind, key=key)
    rec = dict(body, sha256=sha(canonical(body)))
    if len(canonical(rec)) > 1048576:
        raise ValueError("checkpoint_payload_bound")
    journal.log_brain_event(kind, key, rec)


class SearchBroker:
    """Parent owns durable claims and deadlines; children perform only retrieval."""
    def __init__(self, journal, bounds, *, child=run_child, question_id=None):
        self.journal, self.bounds, self.child = journal, bounds, child
        self.question_id = question_id
        self.deadline = time.monotonic() + bounds.max_seconds
        self.attempts, self.bytes = 0, 0

    def request(self, source, operation, arguments):
        if source != S.source(source.source_id) or source.retriever != "crossref_public.v1":
            raise ValueError("source_definition_mismatch")
        url = request_url(operation, arguments)
        identity = dict(source_id=source.source_id, registry=S.REGISTRY_SHA256,
                        operation=operation, arguments=arguments,
                        response_byte_bound=self.bounds.max_response_bytes,
                        question_id=self.question_id, source_class=source.source_class,
                        retriever=source.retriever, registry_schema=S.SCHEMA,
                        normalized_query=" ".join(arguments.get("query", "").split()).casefold() or None)
        key = sha(canonical(identity))
        done = _checkpoint(self.journal, DONE, key)
        if done:
            if done["identity"] != identity:
                raise ValueError("checkpoint_identity")
            return {**done["result"], "cache": True, "request_id": key}
        started = _checkpoint(self.journal, START, key)
        if started:
            if started["identity"] != identity:
                raise ValueError("checkpoint_identity")
            result = dict(status="UNAVAILABLE", reason="INTERRUPTED_REQUEST_NOT_RETRIED", bytes=0,
                          url=url, http_status=None, content_sha256=None,
                          retrieved_at_ms=None, available_ms=started["started_ms"], extraction_status="NOT_EXTRACTED",
                          attempt_identity=identity, attempted_at_ms=started["started_ms"],
                          source_available_ms=None, result_set_sha256=None)
            _write(self.journal, DONE, key, dict(identity=identity, result=result))
            return {**result, "cache": True, "request_id": key}
        # Search result sets are mutable, so every independent question searches.
        # Retrieval reuse requires the exact metadata snapshot returned by that
        # new search, and an earlier positive result. Negatives never qualify.
        if operation == "retrieve" and "metadata_sha256" in arguments:
            rows = self.journal.query("SELECT subject FROM brain_events WHERE kind=? ORDER BY id LIMIT ?",
                                      (DONE, MAX_CHECKPOINTS + 1))
            if len(rows) > MAX_CHECKPOINTS:
                raise ValueError("checkpoint_capacity")
            comparable = {k: v for k, v in identity.items() if k != "question_id"}
            for row in rows:
                if time.monotonic() >= self.deadline:
                    break
                prior = _checkpoint(self.journal, DONE, row["subject"])
                if ({k: v for k, v in prior["identity"].items() if k != "question_id"} == comparable
                        and prior["result"]["status"] == "OK"
                        and sha(canonical(prior["result"].get("metadata"))) == arguments["metadata_sha256"]
                        and prior["result"].get("content_sha256")):
                    return {**prior["result"], "cache": True, "request_id": row["subject"]}
        remaining = self.deadline - time.monotonic()
        # One-second local spacing, also across normal passes/restarts.
        last = self.journal.query("SELECT detail FROM brain_events WHERE kind=? ORDER BY id DESC LIMIT 1", (START,))
        wait = max(0, 1 - (time.time() - json.loads(last[0]["detail"])["started_ms"] / 1000)) if last else 0
        if remaining <= wait or self.attempts >= self.bounds.max_queries + self.bounds.max_pages:
            return dict(status="UNAVAILABLE", reason="request_time_or_count_bound", bytes=0, url=url,
                        http_status=None, content_sha256=None, retrieved_at_ms=None,
                        available_ms=int(time.time()*1000), extraction_status="NOT_EXTRACTED", request_id=key,
                        attempt_identity=identity, attempted_at_ms=None, source_available_ms=None,
                        result_set_sha256=None)
        if wait:
            time.sleep(wait)
        # Reserve both claim and result slots before starting network work.
        count = self.journal.query("SELECT COUNT(*) n FROM brain_events WHERE kind IN (?,?)", (START, DONE))[0]["n"]
        if count > MAX_CHECKPOINTS - 2:
            raise ValueError("checkpoint_capacity")
        started_ms = int(time.time()*1000)
        _write(self.journal, START, key, dict(identity=identity, started_ms=started_ms))
        self.attempts += 1
        remaining = max(.01, self.deadline - time.monotonic())
        child = self.child(crossref_request, operation, arguments, self.bounds.max_response_bytes,
                           remaining, timeout_s=remaining)
        if child.ok:
            result = child.value
            if len(canonical(result)) > 1040000:
                raise ValueError("child_payload_bound")
        else:
            result = dict(status="UNAVAILABLE", reason="retrieval_timeout" if child.timed_out else "retrieval_child_failed",
                          bytes=0, url=url, http_status=None, content_sha256=None)
        self.bytes += result["bytes"]
        if result["status"] == "EMPTY" and result.get("reason") is None:
            result = dict(result, reason="empty_result_set")
        result = dict(result, retrieved_at_ms=int(time.time()*1000), available_ms=int(time.time()*1000),
                      attempted_at_ms=started_ms, attempt_identity=identity,
                      source_available_ms=result.get("source_available_ms"),
                      result_set_sha256=sha(canonical(result["metadata"])) if "metadata" in result else None)
        _write(self.journal, DONE, key, dict(identity=identity, result=result))
        return {**result, "cache": False, "request_id": key}


def filter_metadata(items):
    """Cheap relevance and URL/title dedup before page retrieval/extraction."""
    selected, seen_urls, seen_content = [], set(), set()
    rejected = 0
    for item in items:
        url = normalize_url(item["url"])
        title = re.sub(r"\s+", " ", item["title"].lower()).strip()
        relevant = (re.search(r"\b(trad\w*|financ\w*|market\w*)\b", title)
                    and re.search(r"\b(strateg\w*|decay|regime|persist\w*|performance)\b", title))
        if not relevant or url in seen_urls or title in seen_content:
            rejected += 1
            continue
        seen_urls.add(url)
        seen_content.add(title)
        selected.append(dict(item, url=url))
    return selected, rejected


def registered_question(journal, question_id):
    row = journal.research_question_by_id(question_id)
    if row is None or len(row["canonical_json"]) > 65536:
        raise ValueError("registered_question_missing_or_oversized")
    q = Q.from_json(row["canonical_json"])
    if Q.row_for(q) != {k: row[k] for k in Q.row_for(q)}:
        raise ValueError("question_projection")
    reg = journal.research_registration(Q.SCHEMA, question_id)
    expected = journal.registration_envelope(Q.SCHEMA, question_id, sha(row["canonical_json"]), row["recorded_at_ms"])
    if (not reg or reg["envelope_json"] != expected or reg["envelope_sha256"] != sha(expected)
            or reg["canonical_sha256"] != sha(row["canonical_json"])
            or row["recorded_at_ms"] > int(time.time()*1000)):
        raise ValueError("question_registration")
    # Bound verification reads before invoking the existing derivation contract.
    rows = journal.query("SELECT id,ts,kind,subject,detail FROM brain_events WHERE kind=? OR (kind=? AND subject=?) "
                         "ORDER BY id LIMIT ?", (Q.ho.KIND_SWEEP, Q.ho.KIND_SPEC, q["scope"]["spec_id"], MAX_HISTORY + 1))
    if len(rows) > MAX_HISTORY or any(len(r["detail"]) > 65536 for r in rows):
        raise ValueError("question_history_bound")
    Q.verify_evidence(q, rows)
    return q


def _row(bank):
    text = canonical(bank)
    return dict(bank_object_id=bank["bank_object_id"], schema=BANK_SCHEMA,
                bank_kind="external_evidence", builder_id=BUILDER,
                run_id=bank["plan_id"], result_id=bank["result_id"], evidence_id=bank["evidence_id"],
                plan_id=bank["plan_id"], question_id=bank["question"]["question_id"],
                scope_kind="strategy", scope_id=bank["question"]["scope"]["spec_id"],
                canonical_sha256=sha(text), canonical_json=text)


def verify_bank(journal, row):
    bank = json.loads(row["canonical_json"])
    body = {k: v for k, v in bank.items() if k != "bank_object_id"}
    if bank["bank_object_id"] != sha(canonical(body)) or _row(bank) != {k: row[k] for k in _row(bank)}:
        raise ValueError("external_bank_integrity")
    if "question_source" in bank["plan"]:
        from trader.research.generated_question_source import verify
        original_question = verify(bank["plan"]["question_source"])
    else:
        original_question = registered_question(journal, bank["question"]["question_id"])
    if bank["question"] != original_question:
        raise ValueError("external_bank_question_binding")
    if bank["authority"] != "RESEARCH_ONLY" or bank["result_status"] != "INCONCLUSIVE":
        raise ValueError("external_bank_authority")
    plan = bank["plan"]
    bounds = Bounds(**plan["bounds"])
    expected_plan = dict(schema=BUILDER, route=S.route(bank["question"]), bounds=asdict(bounds))
    if "question_source" in plan:
        expected_plan["question_source"] = plan["question_source"]
    if (plan != expected_plan
            or bank["plan_id"] != sha(canonical(plan))
            or bank["sources"] != plan["route"]["sources"]):
        raise ValueError("external_bank_plan_binding")
    evidence_id = sha(canonical(dict(searches=bank["searches"], evidence=bank["evidence"])))
    result_id = sha(canonical(dict(plan_id=bank["plan_id"], evidence_id=evidence_id,
                                   status=bank["collection_status"])))
    if bank["evidence_id"] != evidence_id or bank["result_id"] != result_id:
        raise ValueError("external_bank_result_binding")
    if (len(bank["searches"]) > bounds.max_queries or len(bank["evidence"]) > bounds.max_pages
            or bank["cost"]["paid_cost_usd"] != 0 or bank["cost"]["llm_calls"] != 0
            or bank["cost"]["network_attempts"] > bounds.max_queries + bounds.max_pages):
        raise ValueError("external_bank_resource_binding")
    reg = journal.research_registration(BANK_SCHEMA, bank["bank_object_id"])
    expected = journal.registration_envelope(BANK_SCHEMA, bank["bank_object_id"], sha(row["canonical_json"]), row["recorded_at_ms"])
    if (not reg or reg["canonical_sha256"] != sha(row["canonical_json"])
            or reg["envelope_json"] != expected or reg["envelope_sha256"] != sha(expected)):
        raise ValueError("external_bank_registration")
    for receipt in bank["searches"] + bank["evidence"]:
        checkpoint = _checkpoint(journal, DONE, receipt["request_id"])
        if checkpoint:
            if any(receipt.get(k) != v for k, v in checkpoint["result"].items()):
                raise ValueError("external_bank_request_binding")
        elif receipt["reason"] != "request_time_or_count_bound":
            raise ValueError("external_bank_request_missing")
    for evidence in bank["evidence"]:
        source = S.source(evidence["source_id"])
        if (evidence["question_id"] != bank["question"]["question_id"]
                or evidence["source_sha256"] != S.digest(source.record())
                or not evidence["query_provenance"]):
            raise ValueError("external_bank_source_binding")
        if evidence.get("extraction_status") == "EXTRACTED_UNVERIFIED":
            if (sha(evidence["passage"]) != evidence["passage_sha256"]
                    or len(evidence["passage"]) > bounds.max_passage_chars):
                raise ValueError("external_passage_hash")
    return bank


def _collect(journal, q, bounds, child, question_source=None):
    route = S.route(q)
    plan = dict(schema=BUILDER, route=route, bounds=asdict(bounds))
    if question_source is not None:
        plan["question_source"] = question_source
    plan_id = sha(canonical(plan))
    old = journal.query("SELECT * FROM research_bank_objects WHERE schema=? AND plan_id=? LIMIT 2", (BANK_SCHEMA, plan_id))
    if old:
        if len(old) != 1:
            raise ValueError("external_bank_conflict")
        return dict(status="DUPLICATE", bank=verify_bank(journal, old[0]))
    if journal.query("SELECT COUNT(*) n FROM research_bank_objects WHERE schema=?", (BANK_SCHEMA,))[0]["n"] >= 1024:
        raise ValueError("external_bank_capacity")
    broker, searches, metadata = SearchBroker(journal, bounds, child=child, question_id=q["question_id"]), [], []
    src = S.select(route["source_classes"][0])[0]
    for query in route["queries"][:bounds.max_queries]:
        found = broker.request(src, "search", dict(query=query, limit=bounds.max_metadata))
        searches.append(dict(found, query=query, normalized_query=" ".join(query.split()).casefold(),
                             question_id=q["question_id"], source_id=src.source_id))
        metadata.extend(found.get("metadata", []))
    selected, rejected = filter_metadata(metadata)
    evidence, hashes = [], {}
    # Content is extracted only after metadata relevance, URL and title dedup.
    from trader.brain.crawler import html_to_text
    for item in selected[:bounds.max_pages]:
        page = broker.request(src, "retrieve", dict(doi=item["doi"], metadata_sha256=sha(canonical(item))))
        entry = dict(page, question_id=q["question_id"], source_id=src.source_id,
                     source_sha256=S.digest(src.record()), source_url=item["url"],
                     query_provenance=[s["request_id"] for s in searches if any(
                         m["doi"] == item["doi"] for m in s.get("metadata", []))],
                     extraction_status="NOT_EXTRACTED", passage=None, passage_sha256=None)
        if page["status"] == "OK":
            data = page["metadata"]
            abstract = data.get("abstract")
            if not abstract:
                entry["extraction_status"] = "METADATA_ONLY_NO_ABSTRACT"
            else:
                content_key = sha(re.sub(r"\s+", " ", abstract).strip())
                if content_key in hashes:
                    entry.update(extraction_status="DUPLICATE_CONTENT", duplicate_of=hashes[content_key])
                else:
                    passage = html_to_text(abstract, cap=bounds.max_passage_chars)[:bounds.max_passage_chars]
                    entry.update(extraction_status="EXTRACTED_UNVERIFIED" if passage.strip() else "EMPTY_EXTRACTION",
                                 passage=passage, passage_sha256=sha(passage))
                    hashes[content_key] = page["request_id"]
        evidence.append(entry)
    good = any(e["status"] == "OK" for e in evidence)
    collection_status = "COLLECTED" if good else ("UNAVAILABLE" if any(
        s["status"] == "UNAVAILABLE" for s in searches) or any(e["status"] == "UNAVAILABLE" for e in evidence)
        else "EMPTY")
    evidence_id = sha(canonical(dict(searches=searches, evidence=evidence)))
    result_id = sha(canonical(dict(plan_id=plan_id, evidence_id=evidence_id, status=collection_status)))
    bank = dict(schema=BANK_SCHEMA, builder_id=BUILDER, bank_kind="external_evidence", authority="RESEARCH_ONLY",
                question=q, plan_id=plan_id, plan=plan, sources=route["sources"],
                searches=searches, evidence=evidence, evidence_id=evidence_id, result_id=result_id,
                extracted_claims={"status": "NOT_AVAILABLE", "reason": "no_registered_claim_extractor"},
                supporting_evidence={"status": "NOT_CLASSIFIED"}, contradictory_evidence={"status": "NOT_CLASSIFIED"},
                experiments=[], result_status="INCONCLUSIVE", collection_status=collection_status,
                limitations=["External metadata/passages are unverified and establish no predictive edge.",
                             "Bounded selection is not exhaustive; cached availability is retained, not refreshed.",
                             "No full text, registered falsifier or causal attribution is claimed."],
                next_questions=[{"text": "What registered quantitative test could falsify any extracted claim?",
                                 "status": "REQUIRES_REGISTERED_PROTOCOL"}],
                cost=dict(scope="current_pass", network_attempts=broker.attempts, received_bytes=broker.bytes, paid_cost_usd=0,
                          llm_calls=0, total_operating_cost="NOT_MEASURED"),
                filtering=dict(metadata_seen=len(metadata), metadata_rejected=rejected,
                               selected=len(selected), pages_omitted=max(0,len(selected)-bounds.max_pages)))
    bank["bank_object_id"] = sha(canonical(bank))
    outcome = journal.record_research_bank_object(_row(bank), recorded_at_ms=int(time.time()*1000))
    if outcome == "conflict":
        raise ValueError("external_bank_conflict")
    return dict(status=outcome.upper(), bank=bank)


def research_pass(journal, *, max_questions=1, bounds=None, child=run_child,
                  question_source=None, policy_id=None, policies=None):
    """Normal bounded registered-question consumer; hold a cross-process single writer lock."""
    if type(max_questions) is not int or not 1 <= max_questions <= 4:
        raise ValueError("bound:max_questions")
    bounds = bounds or Bounds()
    with open(str(journal.db_path) + ".external-research.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "BUSY", "results": []}
        if question_source is not None:
            question_source.validate_worker_path(journal.db_path)
            completed = journal.query("SELECT question_id,canonical_json FROM research_bank_objects WHERE schema=? LIMIT 1025", (BANK_SCHEMA,))
            if len(completed) > 1024:
                raise ValueError("external_bank_capacity")
            for row in completed:
                binding = json.loads(row["canonical_json"])["plan"].get("question_source")
                if (not binding or binding["question_id"] != row["question_id"]
                        or binding["shadow_db"] != question_source.shadow_path
                        or binding["source_db"] != question_source.source_path):
                    raise ValueError("generated_question_bank_provenance_conflict")
            rows = question_source.candidates(max_questions, {r["question_id"] for r in completed})
        else:
            rows = journal.query("SELECT question_id FROM research_questions WHERE NOT EXISTS "
                             "(SELECT 1 FROM research_bank_objects b WHERE b.schema=? AND b.question_id=research_questions.question_id) "
                             "ORDER BY source_event_id,question_id LIMIT ?", (BANK_SCHEMA, max_questions))
        results = []
        for row in rows:
            try:
                q, provenance = (question_source.read(row["question_id"]) if question_source else
                                 (registered_question(journal, row["question_id"]), None))
                eligibility = retry_eligibility(journal, q, RECONSIDERATION_POLICIES if policies is None else policies, policy_id)
                if not eligibility.allowed:
                    results.append(dict(status="POLICY_BLOCKED", question_id=q["question_id"],
                                        policy_id=policy_id, reason=eligibility.reason))
                    continue
                results.append(_collect(journal, q, bounds, child, provenance))
            except (ValueError, KeyError, TypeError, RuntimeError, sqlite3.DatabaseError) as exc:
                results.append(dict(status="REFUSED", question_id=row["question_id"], reason=str(exc)[:160]))
        return dict(status="COMPLETED", results=results)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--enable", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--journal", required=True)
    ap.add_argument("--question-shadow-db")
    ap.add_argument("--question-source-db")
    ap.add_argument("--max-questions", type=int, default=1)
    ap.add_argument("--max-queries", type=int, default=1)
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--max-seconds", type=float, default=30)
    ap.add_argument("--interval-seconds", type=float, default=60)
    args = ap.parse_args(argv)
    if not args.enable:
        print(canonical(dict(status="DISABLED", network_calls=0)))
        return 0
    if not math.isfinite(args.interval_seconds) or args.interval_seconds < 30:
        ap.error("interval must be finite and at least 30 seconds")
    bounds = Bounds(max_queries=args.max_queries, max_pages=args.max_pages, max_seconds=args.max_seconds)
    if not args.question_shadow_db or not args.question_source_db:
        ap.error("enabled worker requires both generated-question database paths; manual APIs remain available to tools")
    from trader.research.generated_question_source import GeneratedQuestionSource
    source = GeneratedQuestionSource(args.question_shadow_db, args.question_source_db)
    try:
        source.validate_worker_path(args.journal)
        journal = Journal(args.journal)
        while True:
            print(canonical(research_pass(journal, max_questions=args.max_questions, bounds=bounds,
                                         question_source=source)), flush=True)
            if args.once:
                return 0
            time.sleep(args.interval_seconds)
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
