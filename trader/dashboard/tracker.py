"""Read-only engineering ledger. No Journal, runtime control or provider access."""
from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

TRACKER_PATH = "docs/tracker/LUFFY_Product_Tracker_v1.yaml"
NEXT_PATH = "NEXT.yaml"
MAX_BYTES = 2 * 1024 * 1024
Status = Literal["EVIDENCE_TO_MAP", "OPEN", "IN_PROGRESS", "AWAITING_EVIDENCE",
                 "AWAITING_OWNER", "CLOSED", "DEFERRED", "BLOCKED"]
STATUSES = ("EVIDENCE_TO_MAP", "OPEN", "IN_PROGRESS", "AWAITING_EVIDENCE",
            "AWAITING_OWNER", "CLOSED", "DEFERRED", "BLOCKED")


class TrackerError(ValueError):
    """Bounded public reason; never expose file contents or private paths."""


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise TrackerError("YAML mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")


class TrackerRow(Contract):
    id: str = Field(pattern=r"^[A-Z]+-[0-9]{2}$")
    area: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    release_scope: str = Field(min_length=1)
    status: Status
    required_behavior: str = Field(min_length=1)
    why_needed: str = Field(min_length=1)
    closure_condition: str = Field(min_length=1)
    dependencies: list[str]
    parent_ids: list[str]
    related_items: list[str]
    latest_evidence: str = Field(min_length=1)
    next_proof: str = Field(min_length=1)
    owner_role: str = Field(min_length=1)


class StatusDefinition(Contract):
    status: Status
    meaning: str = Field(min_length=1)


class Gate(Contract):
    id: str
    title: str = Field(min_length=1)
    items: list[str]
    closure: str = Field(min_length=1)
    current: str = Field(min_length=1)
    limit: str = Field(min_length=1)


class Metadata(Contract):
    version: str = Field(min_length=1)
    row_count: int = Field(ge=1, le=2000)
    current_active_item: str
    status_counts: dict[str, int]
    baseline_commit: str = Field(pattern=r"^[a-f0-9]{40}$")


class Ledger(Contract):
    metadata: Metadata
    status_definitions: list[StatusDefinition]
    readiness_gates: list[Gate]
    items: list[TrackerRow] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def consistent(self):
        ids = [r.id for r in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate tracker IDs")
        if self.metadata.row_count != len(ids):
            raise ValueError("row count mismatch")
        counts = dict(Counter(r.status for r in self.items))
        if {k: v for k, v in self.metadata.status_counts.items() if v} != counts:
            raise ValueError("status count mismatch")
        if any(k not in STATUSES or v < 0 for k, v in self.metadata.status_counts.items()):
            raise ValueError("invalid status counts")
        definitions = [s.status for s in self.status_definitions]
        if len(definitions) != len(STATUSES) or set(definitions) != set(STATUSES):
            raise ValueError("status definitions incomplete or duplicated")
        gate_ids = [g.id for g in self.readiness_gates]
        if len(gate_ids) != 7 or set(gate_ids) != {f"M{i}" for i in range(7)}:
            raise ValueError("readiness gates must be M0 through M6")
        for row in self.items:
            if not set(row.dependencies + row.parent_ids + row.related_items).issubset(ids):
                raise ValueError("unknown row dependency")
        for gate in self.readiness_gates:
            if not gate.items or not set(gate.items).issubset(ids):
                raise ValueError("unknown or empty gate membership")
        return self


class Selection(Contract):
    id: str
    status: Literal["SELECTED_NOT_STARTED", "IN_PROGRESS", "CLOSED", "BLOCKED"]
    mode: Literal["DIAGNOSIS_ONLY", "ENGINEERING_ONLY"]
    objective: str = Field(min_length=1)


class NextDocument(Contract):
    work_package: Selection


def _read(root: Path, relative: str) -> bytes:
    path = root / relative
    try:
        # Only fixed server-owned files inside the configured repository.
        if not path.resolve().is_relative_to(root.resolve()):
            raise TrackerError(f"{relative}: source escapes repository")
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise TrackerError(f"{relative}: source missing or unreadable") from exc
    if len(data) > MAX_BYTES:
        raise TrackerError(f"{relative}: source exceeds size bound")
    return data


def _parse(data: bytes, relative: str) -> Any:
    try:
        value = yaml.load(data.decode("utf-8"), Loader=UniqueLoader)
        json.dumps(value, allow_nan=False)  # Reject recursive/non-JSON YAML values.
        return value
    except (UnicodeError, yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        raise TrackerError(f"{relative}: malformed YAML ({type(exc).__name__})") from exc


def _revision(root: Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
                                capture_output=True, text=True, timeout=2, check=True)
        value = result.stdout.strip()
        return value if re.fullmatch(r"[a-f0-9]{40}", value) else None
    except (OSError, subprocess.SubprocessError):
        return None


def read_tracker(root: Path) -> dict:
    raw = _read(root, TRACKER_PATH)
    next_raw = _read(root, NEXT_PATH)
    try:
        ledger = Ledger.model_validate(_parse(raw, TRACKER_PATH))
        selected = NextDocument.model_validate(_parse(next_raw, NEXT_PATH)).work_package
        by_id = {r.id: r for r in ledger.items}
        if selected.id not in by_id or selected.id != ledger.metadata.current_active_item:
            raise TrackerError("NEXT selection does not match the canonical tracker")
        active = [r.id for r in ledger.items if r.status == "IN_PROGRESS"]
        if len(active) > 1 or (active and active != [selected.id]):
            raise TrackerError("multiple or conflicting active tracker items")
        expected = "OPEN" if selected.status == "SELECTED_NOT_STARTED" else selected.status
        if by_id[selected.id].status != expected:
            raise TrackerError("NEXT selection status conflicts with tracker workflow status")
    except (ValidationError, RecursionError) as exc:
        # Pydantic's full error contains input values; only expose bounded field locations/types.
        if isinstance(exc, ValidationError):
            detail = "; ".join(".".join(map(str, e['loc'])) + ":" + e['type']
                               for e in exc.errors(include_input=False)[:5])
        else:
            detail = "recursive document"
        raise TrackerError(f"Tracker/NEXT contract invalid ({detail})") from exc
    if _read(root, TRACKER_PATH) != raw or _read(root, NEXT_PATH) != next_raw:
        raise TrackerError("Tracker/NEXT changed during read; retry")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "AVAILABLE", "read_only": True,
        "source": {"path": TRACKER_PATH, "sha256": hashlib.sha256(raw).hexdigest(),
                   "repository_revision": _revision(root),
                   "observed_runtime_revision": ledger.metadata.baseline_commit,
                   "version": ledger.metadata.version, "next_path": NEXT_PATH,
                   "next_sha256": hashlib.sha256(next_raw).hexdigest()},
        "selected": selected.model_dump(mode="json"),
        "counts": {s: sum(r.status == s for r in ledger.items) for s in STATUSES},
        "status_definitions": [s.model_dump(mode="json") for s in ledger.status_definitions],
        "readiness_gates": [g.model_dump(mode="json") for g in ledger.readiness_gates],
        "items": [r.model_dump(mode="json") for r in ledger.items],
    }


def install(app: FastAPI, *, root: Path) -> None:
    """Called inside the existing authenticated Owner API installation."""
    @app.get("/owner-api/v1/tracker")
    def tracker():
        try:
            return JSONResponse(read_tracker(root), headers={"Cache-Control": "no-store, max-age=0"})
        except TrackerError as exc:
            return JSONResponse({"status": "DEGRADED", "read_only": True,
                                 "source": TRACKER_PATH, "error": str(exc)},
                                status_code=503, headers={"Cache-Control": "no-store, max-age=0"})
