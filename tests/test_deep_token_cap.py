"""Regression: deep (reasoner) calls need their own, larger token cap.

deepseek-reasoner bills chain-of-thought against max_tokens and emits the
answer last. With the fast-call cap (4000) the autopsy prompt exhausted the
budget mid-reasoning, so the API returned finish_reason='length' with EMPTY
content — billed ~7k tokens per attempt and reported as "no LLM/budget".
"""
import types

from trader.brain.llm import BrainLLM


def _cfg(**over):
    b = {"model_fast": "deepseek-chat", "model_deep": "deepseek-reasoner",
         "max_tokens_per_call": 4000, "max_tokens_deep": 16000,
         "daily_token_budget": 200000,
         "base_url": "https://api.deepseek.com"}
    b.update(over)
    return {"brain": b}


class _FakeClient:
    """Records kwargs; replays a canned (content, finish_reason)."""

    def __init__(self, content="ok", finish_reason="stop"):
        self._content, self._finish = content, finish_reason
        self.kw = None
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.kw = kw
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=self._content,
                                              tool_calls=None),
                finish_reason=self._finish)],
            usage=types.SimpleNamespace(total_tokens=6989))


def _llm(client, cfg=None):
    llm = BrainLLM(cfg or _cfg())
    llm._key, llm._client = "x", client
    # Keep the test off the live usage file: budget_left() reads the REAL
    # day's spend (total and per purpose), so without this every one of these
    # cap assertions fails whenever the production DeepSeek budget happens to
    # be exhausted. The cap logic is what is under test here, not the budget.
    import pathlib
    import tempfile
    llm._usage_path = pathlib.Path(tempfile.mkdtemp()) / "brain_usage.json"
    return llm


def test_deep_call_uses_deep_cap():
    fake = _FakeClient()
    assert _llm(fake).chat("p", deep=True) == "ok"
    assert fake.kw["max_tokens"] == 16000
    assert fake.kw["model"] == "deepseek-reasoner"


def test_fast_call_keeps_small_cap():
    fake = _FakeClient()
    assert _llm(fake).chat("p", deep=False) == "ok"
    assert fake.kw["max_tokens"] == 4000


def test_tool_call_uses_deep_cap():
    fake = _FakeClient()
    _llm(fake).chat_tools([{"role": "user", "content": "p"}], [], deep=True)
    assert fake.kw["max_tokens"] == 16000


def test_deep_cap_defaults_when_config_lacks_key():
    cfg = _cfg()
    del cfg["brain"]["max_tokens_deep"]
    fake = _FakeClient()
    _llm(fake, cfg).chat("p", deep=True)
    assert fake.kw["max_tokens"] == 16000


def test_truncated_reasoning_returns_none_not_empty_string(caplog):
    """The failure that hid for six days: content empty, tokens billed."""
    fake = _FakeClient(content="", finish_reason="length")
    llm = _llm(fake)
    with caplog.at_level("WARNING"):
        assert llm.chat("p", deep=True) is None
        assert llm.chat_json("p", deep=True) is None
    assert "empty content" in caplog.text
    assert "finish_reason=length" in caplog.text


def test_tradingview_budget_is_actually_enforced():
    """daily_runs is decorative while budget_enabled is false."""
    import yaml
    from pathlib import Path

    cfg = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config.yaml").read_text())
    tv = cfg["tv_harness"]

    assert tv["budget_enabled"] is True, "the cap is ignored while this is off"
    assert tv["daily_runs"] <= 5, tv["daily_runs"]
