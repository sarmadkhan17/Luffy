"""Disabled admission and caller/fallback regressions, entirely offline."""
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trader.brain.llm import BrainLLM
from trader.core.journal import Journal


@pytest.fixture
def offline(tmp_path, monkeypatch):
    import openai
    import socket

    # Tripwires remain active even if a future change ignores the fake client.
    constructor = Mock(side_effect=AssertionError("SDK construction forbidden"))
    transport = Mock(side_effect=AssertionError("network forbidden"))
    monkeypatch.setattr(openai, "OpenAI", constructor)
    monkeypatch.setattr(socket.socket, "connect", transport)
    monkeypatch.setattr("trader.brain.llm.Env.deepseek_key", lambda: "offline-secret")
    cfg = {"brain": {"enabled": False, "model_fast": "f", "model_deep": "d",
                     "max_tokens_per_call": 100, "daily_token_budget": 100000}}
    llm = BrainLLM(cfg)
    llm._usage_path = tmp_path / "usage.json"
    llm._usage_path.write_text(json.dumps({"historical-day": 234}))
    sent = Mock(side_effect=AssertionError("provider attempt forbidden"))
    llm._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=sent)))
    yield cfg, llm, sent
    sent.assert_not_called()
    constructor.assert_not_called()
    transport.assert_not_called()


@pytest.mark.parametrize("deep", [False, True])
@pytest.mark.parametrize("method", ["chat", "chat_tools", "chat_json", "_call", "_admit"])
def test_disabled_refusal_is_explicit_without_budget_or_transport_access(
        offline, monkeypatch, caplog, method, deep):
    cfg, llm, _ = offline
    assert not llm.available
    assert llm.budget_left("research") == 100000
    before = llm._usage_path.read_bytes()
    llm._usage_path.with_suffix(".json.lock").unlink()
    # A consumer's permissive availability/budget fallback cannot override config.
    monkeypatch.setattr(BrainLLM, "available", property(lambda self: True))
    budget = Mock(return_value=100000)
    monkeypatch.setattr(llm, "budget_left", budget)
    args = {"chat": ("private prompt",), "chat_json": ("private prompt",),
            "chat_tools": ([{"role": "user", "content": "private prompt"}], []),
            "_call": ({}, deep, "research"), "_admit": ({}, deep, "research")}
    kwargs = {} if method.startswith("_") else {"deep": deep, "purpose": "research"}
    with caplog.at_level(logging.WARNING, logger="trader.brain.llm"):
        assert getattr(llm, method)(*args[method], **kwargs) is None
    budget.assert_not_called()
    assert llm._usage_path.read_bytes() == before
    assert not llm._usage_path.with_suffix(".json.lock").exists()
    records = [r for r in caplog.records if getattr(r, "event", None) == "provider_admission_refused"]
    assert len(records) == 1
    assert records[0].reason_code == "brain_disabled"
    assert records[0].purpose == "research" and records[0].provider_attempts == 0
    assert "brain_disabled" in records[0].getMessage()
    assert "private prompt" not in caplog.text and "offline-secret" not in caplog.text


def test_chat_engine_fallback_cannot_send(offline, tmp_path, caplog):
    from trader.chat.agent import FALLBACK
    from trader.chat.engine import ChatEngine

    cfg, llm, _ = offline
    engine = ChatEngine(Journal(tmp_path / "chat.db"), cfg)
    engine.llm = engine.agent.llm = llm
    with caplog.at_level(logging.WARNING):
        assert engine.handle("what is happening?") == FALLBACK
        assert engine.handle("try again", [{"who": "Luffy", "text": FALLBACK}]) == FALLBACK
    assert engine.consulted == []
    assert sum(getattr(r, "reason_code", None) == "brain_disabled" for r in caplog.records) == 2


@pytest.mark.parametrize("permissive_availability", [False, True])
def test_background_spec_writer_and_pine_template_fallback(offline, monkeypatch, permissive_availability):
    from trader.brain.pine import refine_with_llm
    from trader.brain.spec_writer import SpecWriter

    _, llm, _ = offline
    if permissive_availability:
        monkeypatch.setattr(BrainLLM, "available", property(lambda self: True))
    spec, trace = SpecWriter(llm).write()
    assert spec is None
    if permissive_availability:
        assert trace["attempts"] and all(a["error"] == "non-JSON reply" for a in trace["attempts"])
    else:
        assert trace == {"reason": "no LLM available"}
    genome = SimpleNamespace(hypothesis="offline", params={})
    assert refine_with_llm(genome, "retained template", llm) == ("retained template", False)


@pytest.mark.parametrize("permissive_availability", [False, True])
def test_background_legacy_review_uses_deterministic_fallback(offline, tmp_path, monkeypatch, permissive_availability):
    from trader.brain.strategist import Strategist

    cfg, llm, _ = offline
    journal = Journal(tmp_path / "review.db")
    review = Strategist(journal, cfg)
    review.llm = llm
    if permissive_availability:
        monkeypatch.setattr(BrainLLM, "available", property(lambda self: True))
    monkeypatch.setattr(review, "should_review", lambda: (True, "offline proof"))
    monkeypatch.setattr(review, "_population_report", lambda: [{"id": "legacy"}])
    monkeypatch.setattr(review, "_build_prompt", lambda *a: "offline review")
    monkeypatch.setattr("trader.strategy.promotion.evaluate_population", lambda *a: [])
    monkeypatch.setattr("trader.brain.strategist.Vault.run_full_refresh", lambda *a: None)
    result = review.review()
    assert result["reviewed"] is True
    event = journal.query("SELECT detail FROM brain_events WHERE kind='review_complete'")[0]
    assert json.loads(event["detail"])["llm"] is False


def test_kernel_background_generation_records_refusal_without_send(offline, tmp_path, monkeypatch):
    from trader.kernel import Kernel

    cfg, llm, _ = offline
    kernel = Kernel.__new__(Kernel)
    kernel.cfg = cfg
    kernel.journal = Journal(tmp_path / "kernel.db")
    kernel.feed = kernel.notifier = None
    analyst = SimpleNamespace(review_deployed=lambda book: [])
    monkeypatch.setattr("trader.brain.analyst.Analyst", lambda *a: analyst)
    monkeypatch.setattr("trader.brain.llm.BrainLLM", lambda *a: llm)
    monkeypatch.setattr(kernel, "_research_handoff", lambda *a: None)
    monkeypatch.setattr(kernel, "_strategist_knowledge", lambda: {})
    report = kernel._mechanism_once()
    assert report["added"] == [] and report["book"] == 0
    event = kernel.journal.query("SELECT detail FROM brain_events WHERE kind='spec_write_failed'")[0]
    assert json.loads(event["detail"])["reason"] == "no LLM available"


def test_off_switch_applies_to_an_existing_client_and_reconstructed_fallback(offline, caplog):
    cfg, llm, _ = offline
    cfg["brain"]["enabled"] = True
    assert llm.available
    cfg["brain"]["enabled"] = False
    assert not llm.available
    fallback = BrainLLM(cfg)
    fallback._usage_path = llm._usage_path
    fallback._client = llm._client
    fallback._base_url = "https://fallback.invalid"
    with caplog.at_level(logging.WARNING):
        assert llm.chat("retry") is None
        assert fallback.chat_json("retry", deep=True, purpose="strategist") is None
    assert sum(getattr(r, "reason_code", None) == "brain_disabled" for r in caplog.records) == 2
