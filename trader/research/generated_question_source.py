"""Read-only adapter for questions committed by the existing shadow producer.

No question is imported into the worker Journal. Original registration, source
events and producer snapshot are verified before exposing the exact object.
"""
from pathlib import Path

from trader.cognition import research_shadow as shadow
from trader.cognition import research_shadow_contract as contract
from trader.cognition import research_shadow_store as store


class GeneratedQuestionSource:
    def __init__(self, shadow_db, source_db):
        self.shadow_path = str(Path(shadow_db).resolve())
        self.source_path = str(Path(source_db).resolve())
        self.journal = store.open_shadow(self.shadow_path, self.source_path, readonly=True)
        if shadow._binding(self.journal._conn()) != self.source_path:
            self.close()
            raise ValueError("generated_question_source_binding")

    def close(self):
        self.journal.close()

    def validate_worker_path(self, worker_db):
        path = Path(worker_db).resolve()
        if any(str(path) == original or store.same_file(path, Path(original))
               for original in (self.shadow_path, self.source_path)):
            raise ValueError("worker_journal_is_question_source")

    def candidates(self, limit, completed):
        # Capacity refusal, rather than silently skipping questions beyond a cap.
        rows = self.journal.query("SELECT question_id FROM research_questions "
                                  "ORDER BY source_event_id,question_id LIMIT 1025")
        if len(rows) > 1024:
            raise ValueError("generated_question_capacity")
        return [r for r in rows if r["question_id"] not in completed][:limit]

    def read(self, question_id):
        from trader.research.external_research import registered_question, sha
        rows = self.journal.query("SELECT invocation_id,length(canonical_json) n "
                                  "FROM research_shadow_invocations ORDER BY rowid LIMIT 513")
        if len(rows) > 512 or any(r["n"] > 1048576 for r in rows):
            raise ValueError("generated_question_receipt_bound")
        matches = []
        for row in rows:
            receipt, digest = shadow.load_receipt(self.journal._conn(), row["invocation_id"])
            if receipt["outcome"] == "OK" and any(
                    s["question_id"] == question_id for s in receipt["sources"]):
                matches.append((receipt, digest))
        if not matches:
            raise ValueError("generated_question_producer_receipt_missing")
        # Repeated invocations may name the same object; its original registration
        # and earliest committed provenance remain stable across worker restarts.
        receipt, digest = matches[0]
        view = receipt["source_snapshot"]["health_view"]
        if receipt["source_db"]["path"] != self.source_path or receipt["shadow_db"]["path"] != self.shadow_path:
            raise ValueError("generated_question_receipt_paths")
        self.journal.set_bound(view["spec_max_event_id"], view["sweep_max_event_id"])
        health = self.journal.query("SELECT id,ts,kind,subject,detail FROM brain_events ORDER BY id LIMIT 513")
        if (len(health) > 512 or any(len(r["detail"]) > 65536 for r in health)
                or len(health) != view["row_count"]
                or contract.health_rows_sha256(health) != view["rows_sha256"]):
            raise ValueError("generated_question_source_snapshot")
        q = registered_question(self.journal, question_id)
        row = self.journal.research_question_by_id(question_id)
        if not any(s["question_id"] == question_id and s["source_event_id"] == row["source_event_id"]
                   for s in receipt["sources"]):
            raise ValueError("generated_question_event_binding")
        provenance = dict(schema="generated-question-source.v1", shadow_db=self.shadow_path,
                          source_db=self.source_path, question_id=question_id,
                          canonical_sha256=sha(row["canonical_json"]),
                          recorded_at_ms=row["recorded_at_ms"],
                          registration=self.journal.research_registration(q["schema"], question_id),
                          invocation_id=receipt["invocation_id"], receipt_sha256=digest,
                          health_view=view)
        return q, provenance


def verify(provenance):
    reader = GeneratedQuestionSource(provenance["shadow_db"], provenance["source_db"])
    try:
        q, actual = reader.read(provenance["question_id"])
        if actual != provenance:
            raise ValueError("generated_question_provenance_conflict")
        return q
    finally:
        reader.close()
