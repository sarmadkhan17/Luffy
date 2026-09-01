"""Org registry tests — the company directory loaded from org.yaml.

The org is a metadata/organizing layer only: nothing here touches trading
decisions. These tests pin the roster to the real Analyst classes and check the
author/owner reverse-lookups the Vault relies on.
"""
from __future__ import annotations

from trader.org import Org, Employee


def test_load_parses_manager_and_employees():
    org = Org.load()
    assert org.manager.name == "Manager"
    assert org.manager.reports_to is None
    names = {e.name for e in org.employees}
    for expected in ("Scraper", "Strategist", "Theorist",
                     "Risk Officer", "Trader", "Librarian"):
        assert expected in names
    # every non-manager employee reports to the Manager (flat, two layers)
    assert all(e.reports_to == "Manager" for e in org.employees)


def test_analysts_appended_from_code():
    org = Org.load()
    analysts = [e for e in org.employees if e.title == "Analyst"]
    keys = {e.agent_key for e in analysts}
    # canonical roster from kernel AGENTS/order — must match the real classes
    assert keys == {"structure", "momentum", "flow", "value",
                    "rotation", "positioning", "depth"}
    # each analyst wraps its own module and files into the agent ledger
    for a in analysts:
        assert a.wraps and a.wraps[0].startswith("trader.agents.")
        assert "agent-ledger" in a.authors


def test_analysts_from_code_standalone():
    analysts = Org.analysts_from_code()
    assert len(analysts) == 7
    assert all(isinstance(a, Employee) and a.title == "Analyst"
               for a in analysts)


def test_author_for_reverse_lookup():
    org = Org.load()
    assert org.author_for("strategy") == "Strategist"
    assert org.author_for("postmortem") == "Theorist"
    assert org.author_for("daily-review") == "Manager"
    assert org.author_for("theory") == "Librarian"
    assert org.author_for("nonexistent") is None


def test_owner_for_reverse_lookup():
    org = Org.load()
    assert org.owner_for("trader.engine.risk") == "Risk Officer"
    assert org.owner_for("trader.brain.strategist") == "Strategist"
    assert org.owner_for("trader.knowledge.vault") == "Librarian"
    assert org.owner_for("trader.engine.orchestrator") == "Manager"
    assert org.owner_for("trader.nonexistent") is None


def test_all_returns_manager_first():
    org = Org.load()
    everyone = org.all()
    assert everyone[0].name == "Manager"
    assert len(everyone) == len(org.employees) + 1
