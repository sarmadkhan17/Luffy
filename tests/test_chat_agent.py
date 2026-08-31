"""Tests for the Talk-to-Luffy analyst: tools, agent loop, ops intercept."""
import json
import types

from trader.brain.llm import BrainLLM


def _cfg():
    return {"brain": {"model_fast": "deepseek-chat",
                      "model_deep": "deepseek-reasoner",
                      "max_tokens_per_call": 4000,
                      "daily_token_budget": 200000,
                      "base_url": "https://api.deepseek.com"}}


# ── Task 1: BrainLLM.chat_tools ──────────────────────────────────────────
class _FakeMsg:
    def __init__(self):
        self.content = "hi"
        self.tool_calls = None


class _FakeClient:
    def __init__(self):
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.kw = kw
        msg = _FakeMsg()
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg)],
            usage=types.SimpleNamespace(total_tokens=42))


def test_chat_tools_returns_message_and_spends():
    llm = BrainLLM(_cfg())
    llm._key = "x"
    fake = _FakeClient()
    llm._client = fake
    before = llm.budget_left()
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    msg = llm.chat_tools([{"role": "user", "content": "hey"}], tools)
    assert msg.content == "hi"
    assert fake.kw["tools"] == tools
    assert llm.budget_left() == before - 42


def test_chat_tools_none_when_no_budget(monkeypatch):
    llm = BrainLLM(_cfg())
    llm._key = "x"
    monkeypatch.setattr(llm, "budget_left", lambda: 0)
    assert llm.chat_tools([{"role": "user", "content": "hey"}], []) is None


# ── Task 2: read-only tools ──────────────────────────────────────────────
from trader.core.journal import Journal          # noqa: E402
from trader.chat import tools as T                # noqa: E402


def _journal(tmp_path):
    j = Journal(str(tmp_path / "t.db"))
    j.query(
        "INSERT INTO trades(id,symbol,side,amount,entry_price,opened_at,"
        "status,realized_pnl,closed_at,strategy_name) VALUES"
        "('t1','SOL/USDT','long',10,100,'2026-08-29T09:00:00','closed',"
        "50,'2026-08-29T10:00:00','VWAP Fade')")
    j.query(
        "INSERT INTO trades(id,symbol,side,amount,entry_price,opened_at,"
        "status,realized_pnl,closed_at,strategy_name) VALUES"
        "('t2','ARB/USDT','long',20,1,'2026-08-29T09:30:00','closed',"
        "-20,'2026-08-29T11:00:00','EMA Trend')")
    j._conn().commit()
    return j


def test_get_pnl_today(tmp_path, monkeypatch):
    j = _journal(tmp_path)
    monkeypatch.setattr(T, "_today_like", lambda: "2026-08-29%")
    r = T.get_pnl(j, period="today")
    assert r["realized_pnl"] == 30.0
    assert r["wins"] == 1 and r["losses"] == 1
    assert r["win_rate"] == 50.0


def test_get_trades_filter_symbol(tmp_path):
    j = _journal(tmp_path)
    rows = T.get_trades(j, symbol="SOL/USDT")
    assert len(rows) == 1 and rows[0]["strategy_name"] == "VWAP Fade"


def test_registry_schemas_match_tools():
    names = {s["function"]["name"] for s in T.TOOL_SCHEMAS}
    assert names == set(T.TOOLS.keys())


# ── Task 3: AnalystAgent loop ────────────────────────────────────────────
from trader.chat.agent import AnalystAgent        # noqa: E402


class _StubLLM:
    """Yields a scripted sequence of assistant messages."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat_tools(self, messages, tools, deep=False):
        self.calls.append(messages)
        return self.script.pop(0)


def _tc(name, args):
    return types.SimpleNamespace(
        id="c1", type="function",
        function=types.SimpleNamespace(name=name, arguments=json.dumps(args)))


def _msg(content=None, tool_calls=None):
    return types.SimpleNamespace(content=content, tool_calls=tool_calls)


def test_agent_runs_tool_then_answers(tmp_path):
    j = _journal(tmp_path)
    llm = _StubLLM([
        _msg(tool_calls=[_tc("get_pnl", {"period": "all"})]),
        _msg(content="You're up 30 USDT, 1 win 1 loss."),
    ])
    out = AnalystAgent(j, llm).run("how did we do?")
    assert "30" in out
    assert any(m.get("role") == "tool" for m in llm.calls[1])


def test_agent_stops_at_max_steps(tmp_path):
    j = _journal(tmp_path)
    forever = _StubLLM([_msg(tool_calls=[_tc("get_positions", {})])] * 10)
    out = AnalystAgent(j, forever, max_steps=3).run("loop?")
    assert isinstance(out, str) and len(forever.calls) <= 4


def test_agent_fallback_when_llm_unavailable(tmp_path):
    j = _journal(tmp_path)

    class Dead:
        def chat_tools(self, *a, **k):
            return None

    out = AnalystAgent(j, Dead()).run("hi")
    assert "offline" in out.lower() or "budget" in out.lower()


# ── Task 4: ChatEngine wiring ────────────────────────────────────────────
from trader.chat.engine import ChatEngine         # noqa: E402


def test_ops_intercept_before_agent(tmp_path):
    j = _journal(tmp_path)
    eng = ChatEngine(j, _cfg())
    reply = eng.handle("please freeze entries", do_ops=True)
    assert "FROZEN" in reply
    assert j.kv_get("control_state") == "FROZEN"


def test_question_routes_to_agent(tmp_path, monkeypatch):
    j = _journal(tmp_path)
    eng = ChatEngine(j, _cfg())
    monkeypatch.setattr("trader.chat.agent.AnalystAgent.run",
                        lambda self, m, h=None: "AGENT_ANSWER")
    assert eng.handle("how is pnl?", do_ops=True) == "AGENT_ANSWER"
