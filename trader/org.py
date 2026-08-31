"""Company directory — Luffy's org chart as data.

`org.yaml` at the repo root is the source of truth: one Manager over a flat team
of employees, each wrapping existing code. This module loads that file into
dataclasses and answers two questions the knowledge vault needs:

- `author_for(note_type)` — which employee signs a given kind of note?
- `owner_for(module)`     — which employee is responsible for a code module?

This is a metadata/organizing layer ONLY. Nothing in the trade loop consumes it;
the orchestrator's decision math is untouched. The Vault (and, later, the
dashboard) are the only readers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .core.config import ROOT

ORG_PATH = ROOT / "org.yaml"


@dataclass
class Employee:
    name: str
    title: str
    reports_to: str | None = None
    wraps: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    desc: str = ""
    #: for Analysts: the analyst's ``.name`` (matches Vote.agent / ledger rows)
    agent_key: str | None = None
    #: how the cockpit derives this employee's live status. One of:
    #: manager | analyst | trader | risk | librarian | events | none
    status_source: str = "none"
    #: brain_event kinds this employee emits (used when status_source == "events")
    events: list[str] = field(default_factory=list)
    #: command-deck filter group (drives the filter chips), metadata-only. One of:
    #: BRAIN | ANALYST | RESEARCH | RISK | EXECUTION | KNOWLEDGE
    category: str = ""


@dataclass
class Org:
    manager: Employee
    employees: list[Employee]

    # ── construction ────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Org":
        data = yaml.safe_load(Path(path or ORG_PATH).read_text()) or {}
        manager = cls._employee(data.get("manager", {}))
        employees = [cls._employee(e) for e in (data.get("employees") or [])]
        # append the real analysts so the roster never drifts from the code
        have = {e.name for e in employees}
        for a in cls.analysts_from_code():
            if a.name not in have:
                employees.append(a)
        return cls(manager=manager, employees=employees)

    @staticmethod
    def _employee(d: dict) -> Employee:
        return Employee(
            name=d["name"],
            title=d.get("title", d["name"]),
            reports_to=d.get("reports_to"),
            wraps=list(d.get("wraps") or []),
            authors=list(d.get("authors") or []),
            desc=d.get("desc", ""),
            agent_key=d.get("agent_key"),
            status_source=d.get("status_source", "none"),
            events=list(d.get("events") or []),
            category=d.get("category", ""),
        )

    @staticmethod
    def analysts_from_code() -> list[Employee]:
        """One Employee per concrete Analyst subclass in trader/agents/.

        Importing the agent modules registers the subclasses; we then read each
        analyst's ``name`` and module so the org roster matches reality without
        importing the heavy kernel.
        """
        from .agents.base import Analyst
        # importing the modules registers the subclasses (side-effect import)
        from .agents import flow, momentum, structure, positioning, orderbook_depth  # noqa: F401

        out: list[Employee] = []
        for cls in Analyst.__subclasses__():
            # only real analysts under trader/agents/ — ignore test doubles that
            # leak into Analyst.__subclasses__() via global interpreter state
            if not cls.__module__.startswith("trader.agents."):
                continue
            key = getattr(cls, "name", None)
            if not key or key == "analyst":
                continue
            out.append(Employee(
                name=f"{key.capitalize()} Analyst",
                title="Analyst",
                reports_to="Manager",
                wraps=[cls.__module__],
                authors=["agent-ledger"],
                desc=f"Measures the {key} mechanism and casts a vote.",
                agent_key=key,
                status_source="analyst",
                category="ANALYST",
            ))
        out.sort(key=lambda e: e.name)
        return out

    # ── queries ─────────────────────────────────────────────────────────
    def all(self) -> list[Employee]:
        """Everyone, Manager first."""
        return [self.manager, *self.employees]

    def author_for(self, note_type: str) -> str | None:
        """Name of the employee who signs `note_type` notes, if any."""
        for e in self.all():
            if note_type in e.authors:
                return e.name
        return None

    def owner_for(self, module: str) -> str | None:
        """Name of the employee responsible for a dotted `module` path."""
        for e in self.all():
            if module in e.wraps:
                return e.name
        return None

    def analyst_node(self, agent_key: str) -> str | None:
        """Vault node name for an analyst given its ``.name`` key."""
        for e in self.all():
            if e.agent_key == agent_key:
                return e.name
        return None
