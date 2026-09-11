"""Each LLM consumer spends from its own slice.

One shared 200k-token pot let the Theorist and the Strategist take ~78% of
all spend while producing one unvalidated strategy. The research Reason
step needs a slice nobody else can eat, and every call must say who it is,
so spend can be attributed rather than guessed from log adjacency.
"""
import json
from datetime import datetime, timezone

from trader.brain.llm import BrainLLM


def _llm(tmp_path, caps=None, daily=1000):
    cfg = {"brain": {"model_fast": "f", "model_deep": "d",
                     "max_tokens_per_call": 10, "daily_token_budget": daily,
                     "purpose_budgets": caps or {}}}
    b = BrainLLM(cfg)
    b._usage_path = tmp_path / "brain_usage.json"
    return b


def test_spend_is_recorded_under_its_purpose(tmp_path):
    b = _llm(tmp_path)
    b._spend(42, "chat")
    assert b._purpose_today() == {"chat": 42}
    assert b._tokens_today() == 42


def test_a_reserved_purpose_cannot_be_eaten_by_others(tmp_path):
    b = _llm(tmp_path, {"research": 600})
    b._spend(400, "strategist")          # the shared pool is 1000 - 600
    assert b.budget_left("strategist") == 0
    assert b.budget_left("research") == 600


def test_a_capped_purpose_stops_at_its_cap(tmp_path):
    b = _llm(tmp_path, {"research": 600})
    b._spend(600, "research")
    assert b.budget_left("research") == 0
    assert b.budget_left("chat") == 400


def test_the_daily_total_still_binds(tmp_path):
    """Spend recorded before purposes existed carries no tag; it still counts."""
    b = _llm(tmp_path, {"research": 600})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    b._usage_path.write_text(json.dumps({today: 950}))
    assert b.budget_left("research") == 50


def test_a_spent_purpose_never_reaches_the_network(tmp_path):
    b = _llm(tmp_path, {"research": 600})
    b._key = "x"
    b._spend(600, "research")
    assert b.chat("hi", purpose="research") is None
    assert b._client is None
