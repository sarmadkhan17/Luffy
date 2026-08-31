# Talk to Luffy — Intelligent Voice Analyst Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn "Talk to Luffy" into a voice-enabled, tool-calling trading analyst that reasons over the live journal on demand.

**Architecture:** A bounded agent loop replaces the single static brief: DeepSeek (via `BrainLLM.chat_tools`) is given read-only journal tools, calls them, we execute whitelisted queries, feed results back, and it answers. The deterministic ops layer (`detect_ops`) is preserved and runs first. The frontend `v-luffy` view is rebuilt in the Motion language with a breathing voice-orb and browser Web-Speech voice I/O.

**Tech Stack:** Python (FastAPI, sqlite `Journal`), OpenAI-compatible client (DeepSeek), Motion (motion.dev) via CDN, Web Speech API (SpeechRecognition + SpeechSynthesis), pytest.

**Spec:** `docs/superpowers/specs/2026-08-29-talk-to-luffy-analyst-design.md`

## Global Constraints

- **Read-only analyst.** Tools never mutate state. Ops (freeze/halt/panic/resume) stay in `detect_ops` and run before the LLM. The LLM must never trigger ops.
- **No arbitrary SQL from the LLM.** Tools are a fixed whitelist of typed functions; the model only picks a tool name + typed args.
- **Budget-guarded.** All model calls go through `BrainLLM` and respect `budget_left()`; on exhaustion/error, return the existing "brain offline" fallback.
- **Provider-swappable.** Model calls read `base_url`/`model` from config so Hermes-via-Ollama can swap in later with no code change. Default provider = DeepSeek.
- **No live API in CI.** Every test stubs `BrainLLM`/the OpenAI client. No network in tests.
- **Agent loop cap:** max 5 tool-calling steps per user message.

---

## File Structure

| File | Responsibility |
|---|---|
| `trader/brain/llm.py` (modify) | add `chat_tools()`; read `base_url` from config |
| `trader/chat/tools.py` (create) | read-only tool functions over `Journal` + OpenAI tool schemas + `TOOLS`/`TOOL_SCHEMAS` registry |
| `trader/chat/agent.py` (create) | `AnalystAgent` — the bounded tool-calling loop |
| `trader/chat/engine.py` (modify) | keep `detect_ops`; delegate the answer path to `AnalystAgent` |
| `config.yaml` (modify) | `brain.base_url`, `chat` block (model, max_steps) |
| `trader/dashboard/web/index.html` (modify) | rebuild `v-luffy` (Motion orb + states + mic); load Motion |
| `tests/test_chat_agent.py` (create) | tools, ops-intercept, stubbed-LLM loop, budget paths |

---

## Task 1: `BrainLLM.chat_tools()` + configurable base_url

**Files:**
- Modify: `trader/brain/llm.py`
- Modify: `config.yaml` (brain block)
- Test: `tests/test_chat_agent.py`

**Interfaces:**
- Consumes: existing `BrainLLM.__init__(cfg)`, `self.available`, `self.budget_left()`, `self._spend()`.
- Produces: `BrainLLM.chat_tools(messages: list[dict], tools: list[dict], deep: bool=False) -> object | None` — returns the OpenAI assistant `message` object (has `.content: str|None` and `.tool_calls: list|None`), or `None` on no-budget/error.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_agent.py
import types
from trader.brain.llm import BrainLLM

def _cfg():
    return {"brain": {"model_fast": "deepseek-chat", "model_deep": "deepseek-reasoner",
                      "max_tokens_per_call": 4000, "daily_token_budget": 200000,
                      "base_url": "https://api.deepseek.com"}}

class _FakeMsg:
    def __init__(self): self.content = "hi"; self.tool_calls = None

class _FakeClient:
    def __init__(self): self.chat = types.SimpleNamespace(
        completions=types.SimpleNamespace(create=self._create))
    def _create(self, **kw):
        self.kw = kw
        msg = _FakeMsg()
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)],
                                     usage=types.SimpleNamespace(total_tokens=42))

def test_chat_tools_returns_message_and_spends(monkeypatch):
    llm = BrainLLM(_cfg())
    llm._key = "x"                      # force available
    fake = _FakeClient()
    llm._client = fake
    before = llm.budget_left()
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    msg = llm.chat_tools([{"role": "user", "content": "hey"}], tools)
    assert msg.content == "hi"
    assert fake.kw["tools"] == tools               # tools forwarded
    assert llm.budget_left() == before - 42        # tokens spent

def test_chat_tools_none_when_no_budget(monkeypatch):
    llm = BrainLLM(_cfg())
    llm._key = "x"
    monkeypatch.setattr(llm, "budget_left", lambda: 0)
    assert llm.chat_tools([{"role": "user", "content": "hey"}], []) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_chat_agent.py -k chat_tools -v`
Expected: FAIL — `AttributeError: 'BrainLLM' object has no attribute 'chat_tools'`

- [ ] **Step 3: Implement**

In `trader/brain/llm.py`, in `__init__` (after `self._key = ...`) add:
```python
        self._base_url = b.get("base_url", "https://api.deepseek.com")
```
Change the client creation inside `chat()` from the hardcoded URL to `base_url=self._base_url`. Then add the method after `chat()`:
```python
    def chat_tools(self, messages: list[dict], tools: list[dict],
                   deep: bool = False):
        """OpenAI-compatible tool-calling. Returns the assistant message
        object (.content, .tool_calls) or None on no-budget/error."""
        if not self.available or self.budget_left() <= 0:
            return None
        try:
            from openai import OpenAI
            if self._client is None:
                self._client = OpenAI(api_key=self._key,
                                      base_url=self._base_url)
            resp = self._client.chat.completions.create(
                model=self.model_deep if deep else self.model_fast,
                messages=messages,
                tools=tools,
                max_tokens=self.max_tokens,
                timeout=120)
            used = getattr(resp.usage, "total_tokens", 0) or 0
            self._spend(used)
            return resp.choices[0].message
        except Exception as e:
            log.warning(f"brain tool call failed: {e}")
            return None
```
In `config.yaml`, under `brain:` add: `  base_url: https://api.deepseek.com`

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_chat_agent.py -k chat_tools -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add trader/brain/llm.py config.yaml tests/test_chat_agent.py
git commit -m "feat(chat): add BrainLLM.chat_tools + configurable base_url"
```

---

## Task 2: Read-only journal tools + schemas

**Files:**
- Create: `trader/chat/tools.py`
- Test: `tests/test_chat_agent.py` (append)

**Interfaces:**
- Consumes: `Journal.query(sql, params)`, `Journal.open_trades()`, `Journal.agent_accuracy(since_hours)`.
- Produces:
  - functions `get_positions(journal)`, `get_pnl(journal, period="today")`, `get_trades(journal, symbol=None, strategy=None, status=None, limit=20)`, `get_decisions(journal, symbol=None, action=None, limit=10)`, `get_strategy_performance(journal, name=None)`, `get_agent_stats(journal, since_hours=168)`, `get_equity_curve(journal, limit=50)` — each returns JSON-serializable `list`/`dict`.
  - `TOOLS: dict[str, callable]` name→function.
  - `TOOL_SCHEMAS: list[dict]` OpenAI tool schemas.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_agent.py (append)
from trader.core.journal import Journal
from trader.chat import tools as T

def _journal(tmp_path):
    j = Journal(str(tmp_path / "t.db"))
    j.execute if False else None
    # seed via raw sql through the same connection
    j.query("INSERT INTO trades(symbol,side,amount,entry_price,status,realized_pnl,closed_at,strategy_name)"
            " VALUES('SOL/USDT','long',10,100,'closed',50,'2026-08-29T10:00:00','VWAP Fade')")
    j.query("INSERT INTO trades(symbol,side,amount,entry_price,status,realized_pnl,closed_at,strategy_name)"
            " VALUES('ARB/USDT','long',20,1,'closed',-20,'2026-08-29T11:00:00','EMA Trend')")
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
```
> Note: `Journal(...)` creates the schema on init (see `trader/core/journal.py`). If your `Journal` has no direct INSERT helper, `query()` executes arbitrary SQL — fine for tests. Confirm the `trades` columns exist by reading `journal.py` before running.

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_chat_agent.py -k "pnl or trades_filter or registry" -v`
Expected: FAIL — `ModuleNotFoundError: trader.chat.tools`

- [ ] **Step 3: Implement `trader/chat/tools.py`**

```python
"""Read-only analyst tools. Each takes a Journal + typed kwargs and returns
JSON-serializable data. No writes, no arbitrary SQL from callers."""
from __future__ import annotations
from datetime import datetime, timezone
from ..core.journal import Journal


def _today_like() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d") + "%"


def get_positions(journal: Journal) -> list[dict]:
    out = []
    for t in journal.open_trades():
        out.append({k: t.get(k) for k in
                    ("symbol", "side", "amount", "entry_price",
                     "stop_loss", "take_profit", "leverage", "strategy_name")})
    return out


def get_pnl(journal: Journal, period: str = "today") -> dict:
    where, params = "", ()
    if period == "today":
        where, params = "WHERE closed_at LIKE ?", (_today_like(),)
    rows = journal.query(
        f"SELECT realized_pnl FROM trades WHERE status='closed' "
        + (f"AND closed_at LIKE ?" if period == "today" else ""),
        params if period == "today" else ())
    pnls = [float(r["realized_pnl"] or 0) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    n = len(pnls)
    return {
        "period": period,
        "realized_pnl": round(sum(pnls), 2),
        "trades": n,
        "wins": wins, "losses": losses,
        "win_rate": round(100 * wins / n, 1) if n else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
    }


def get_trades(journal: Journal, symbol: str | None = None,
               strategy: str | None = None, status: str | None = None,
               limit: int = 20) -> list[dict]:
    clauses, params = [], []
    if symbol:   clauses.append("symbol=?");        params.append(symbol)
    if strategy: clauses.append("strategy_name=?"); params.append(strategy)
    if status:   clauses.append("status=?");        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    limit = max(1, min(int(limit), 100))
    return journal.query(
        f"SELECT symbol,side,amount,entry_price,realized_pnl,status,"
        f"strategy_name,closed_at FROM trades {where} "
        f"ORDER BY id DESC LIMIT {limit}", tuple(params))


def get_decisions(journal: Journal, symbol: str | None = None,
                  action: str | None = None, limit: int = 10) -> list[dict]:
    clauses, params = ["action!='HOLD'"], []
    if symbol: clauses.append("symbol=?"); params.append(symbol)
    if action: clauses.append("action=?"); params.append(action)
    where = "WHERE " + " AND ".join(clauses)
    limit = max(1, min(int(limit), 50))
    return journal.query(
        f"SELECT ts,symbol,action,score,executed,skip_reason "
        f"FROM decisions {where} ORDER BY id DESC LIMIT {limit}", tuple(params))


def get_strategy_performance(journal: Journal,
                             name: str | None = None) -> list[dict]:
    if name:
        return journal.query(
            "SELECT name,state,kind,params FROM strategies WHERE name=?", (name,))
    return journal.query(
        "SELECT name,state,kind FROM strategies "
        "WHERE state IN ('paper','active','demoted') ORDER BY state LIMIT 20")


def get_agent_stats(journal: Journal, since_hours: float = 168.0) -> list[dict]:
    rows = journal.agent_accuracy(since_hours=since_hours)
    return [{"agent": a["agent"], "n": a["n"],
             "accuracy": round(a["accuracy"] or 0, 2)} for a in rows]


def get_equity_curve(journal: Journal, limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    rows = journal.query(
        f"SELECT ts,equity FROM equity ORDER BY ts DESC LIMIT {limit}")
    return list(reversed(rows))


TOOLS = {
    "get_positions": get_positions,
    "get_pnl": get_pnl,
    "get_trades": get_trades,
    "get_decisions": get_decisions,
    "get_strategy_performance": get_strategy_performance,
    "get_agent_stats": get_agent_stats,
    "get_equity_curve": get_equity_curve,
}


def _schema(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object",
                       "properties": props or {},
                       "required": required or []}}}


TOOL_SCHEMAS = [
    _schema("get_positions", "Current open positions with entry, size, SL/TP, strategy."),
    _schema("get_pnl", "Realized P&L, win-rate, profit factor.",
            {"period": {"type": "string", "enum": ["today", "all"],
                        "description": "today or all-time"}}),
    _schema("get_trades", "Recent trades, optionally filtered.",
            {"symbol": {"type": "string"}, "strategy": {"type": "string"},
             "status": {"type": "string", "enum": ["open", "closed"]},
             "limit": {"type": "integer"}}),
    _schema("get_decisions", "Recent entry/exit decisions incl. skip_reason (why a trade was or wasn't taken).",
            {"symbol": {"type": "string"}, "action": {"type": "string"},
             "limit": {"type": "integer"}}),
    _schema("get_strategy_performance", "Strategy states/kinds; pass name for one.",
            {"name": {"type": "string"}}),
    _schema("get_agent_stats", "Analyst accuracy over a window.",
            {"since_hours": {"type": "number"}}),
    _schema("get_equity_curve", "Equity points over time for trend questions.",
            {"limit": {"type": "integer"}}),
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_chat_agent.py -k "pnl or trades_filter or registry" -v`
Expected: PASS. If a column name differs, read `trader/core/journal.py` schema and adjust SQL, then re-run.

- [ ] **Step 5: Commit**

```bash
git add trader/chat/tools.py tests/test_chat_agent.py
git commit -m "feat(chat): read-only analyst tools + OpenAI tool schemas"
```

---

## Task 3: `AnalystAgent` tool-calling loop

**Files:**
- Create: `trader/chat/agent.py`
- Test: `tests/test_chat_agent.py` (append)

**Interfaces:**
- Consumes: `BrainLLM.chat_tools(messages, tools)`, `tools.TOOLS`, `tools.TOOL_SCHEMAS`.
- Produces: `AnalystAgent(journal, llm, max_steps=5)`, method `run(message: str, history: list[dict]|None=None) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_agent.py (append)
import json, types
from trader.chat.agent import AnalystAgent

class _StubLLM:
    """Yields a scripted sequence of assistant messages."""
    def __init__(self, script): self.script = list(script); self.calls = []
    def chat_tools(self, messages, tools, deep=False):
        self.calls.append(messages)
        return self.script.pop(0)

def _tc(name, args):
    return types.SimpleNamespace(id="c1", type="function",
        function=types.SimpleNamespace(name=name, arguments=json.dumps(args)))

def _msg(content=None, tool_calls=None):
    return types.SimpleNamespace(content=content, tool_calls=tool_calls)

def test_agent_runs_tool_then_answers(tmp_path):
    j = _journal(tmp_path)
    llm = _StubLLM([
        _msg(tool_calls=[_tc("get_pnl", {"period": "today"})]),  # step 1: call tool
        _msg(content="You're up 30 USDT today, 1 win 1 loss."),  # step 2: final
    ])
    out = AnalystAgent(j, llm).run("how did we do today?")
    assert "30" in out
    # the tool result was fed back on the 2nd call
    assert any(m.get("role") == "tool" for m in llm.calls[1])

def test_agent_stops_at_max_steps(tmp_path):
    j = _journal(tmp_path)
    forever = _StubLLM([_msg(tool_calls=[_tc("get_positions", {})])] * 10)
    out = AnalystAgent(j, forever, max_steps=3).run("loop?")
    assert isinstance(out, str) and len(forever.calls) <= 3

def test_agent_fallback_when_llm_unavailable(tmp_path):
    j = _journal(tmp_path)
    class Dead:
        def chat_tools(self, *a, **k): return None
    out = AnalystAgent(j, Dead()).run("hi")
    assert "offline" in out.lower() or "budget" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_chat_agent.py -k agent -v`
Expected: FAIL — `ModuleNotFoundError: trader.chat.agent`

- [ ] **Step 3: Implement `trader/chat/agent.py`**

```python
"""Bounded tool-calling analyst loop. Read-only; never triggers ops."""
from __future__ import annotations
import json, logging
from ..core.journal import Journal
from . import tools as T

log = logging.getLogger(__name__)

SYSTEM = (
    "You are Luffy, a sharp, concise crypto trading analyst embedded in an "
    "autonomous trading system. Answer the operator's questions about the "
    "system's own trading using the tools to fetch real data. Cite concrete "
    "numbers from tool results — never invent figures. If the data doesn't "
    "answer it, say so. You are read-only: you cannot place, freeze, or close "
    "trades. Be direct, a little witty, no generic financial advice.")

FALLBACK = ("Brain offline (no budget or API error). Ops still work: try "
            "'freeze', 'close all', or ask again later.")


class AnalystAgent:
    def __init__(self, journal: Journal, llm, max_steps: int = 5):
        self.journal = journal
        self.llm = llm
        self.max_steps = max_steps

    def run(self, message: str, history: list[dict] | None = None) -> str:
        messages = [{"role": "system", "content": SYSTEM}]
        for h in (history or [])[-6:]:
            role = "assistant" if h.get("who") == "Luffy" else "user"
            messages.append({"role": role, "content": (h.get("text") or "")[:400]})
        messages.append({"role": "user", "content": message})

        for _ in range(self.max_steps):
            msg = self.llm.chat_tools(messages, T.TOOL_SCHEMAS)
            if msg is None:
                return FALLBACK
            calls = getattr(msg, "tool_calls", None)
            if not calls:
                return (msg.content or "").strip() or "…"
            # record the assistant's tool-call turn
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [
                                 {"id": c.id, "type": "function",
                                  "function": {"name": c.function.name,
                                               "arguments": c.function.arguments}}
                                 for c in calls]})
            for c in calls:
                result = self._exec(c.function.name, c.function.arguments)
                messages.append({"role": "tool", "tool_call_id": c.id,
                                 "content": json.dumps(result, default=str)})
        # ran out of steps — ask for a final answer without tools
        msg = self.llm.chat_tools(messages + [
            {"role": "user", "content": "Answer now with what you have."}], [])
        return ((getattr(msg, "content", None) or "").strip() if msg else FALLBACK) or FALLBACK

    def _exec(self, name: str, raw_args: str):
        fn = T.TOOLS.get(name)
        if not fn:
            return {"error": f"unknown tool {name}"}
        try:
            args = json.loads(raw_args) if raw_args else {}
        except Exception:
            args = {}
        try:
            return fn(self.journal, **args)
        except Exception as e:
            log.warning(f"tool {name} failed: {e}")
            return {"error": str(e)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_chat_agent.py -k agent -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add trader/chat/agent.py tests/test_chat_agent.py
git commit -m "feat(chat): AnalystAgent bounded tool-calling loop"
```

---

## Task 4: Wire `AnalystAgent` into `ChatEngine`

**Files:**
- Modify: `trader/chat/engine.py`
- Test: `tests/test_chat_agent.py` (append)

**Interfaces:**
- Consumes: `AnalystAgent`, existing `detect_ops`, `BrainLLM`.
- Produces: unchanged `ChatEngine.handle(message, history, do_ops=True) -> str`, now routing non-ops questions through `AnalystAgent`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_agent.py (append)
from trader.chat.engine import ChatEngine

def test_ops_intercept_before_agent(tmp_path):
    j = _journal(tmp_path)
    eng = ChatEngine(j, _cfg())
    # 'freeze' must be handled deterministically, not via the LLM
    reply = eng.handle("please freeze entries", do_ops=True)
    assert "FROZEN" in reply
    assert j.kv_get("control_state") == "FROZEN"

def test_question_routes_to_agent(tmp_path, monkeypatch):
    j = _journal(tmp_path)
    eng = ChatEngine(j, _cfg())
    monkeypatch.setattr("trader.chat.agent.AnalystAgent.run",
                        lambda self, m, h=None: "AGENT_ANSWER")
    assert eng.handle("how is pnl?", do_ops=True) == "AGENT_ANSWER"
```
> `ChatEngine._set_state` uses `ControlStateMachine`; the seeded `Journal` supports `kv_get/kv_set`. If `handle("freeze")` needs extra state tables, they are created by `Journal.__init__`.

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_chat_agent.py -k "intercept or routes" -v`
Expected: FAIL — `test_question_routes_to_agent` fails (engine still calls single-shot `ask`).

- [ ] **Step 3: Implement**

In `trader/chat/engine.py`:
- Add import near the top: `from .agent import AnalystAgent`.
- In `__init__`, after `self.llm = BrainLLM(cfg)` add:
  `self.agent = AnalystAgent(journal, self.llm, max_steps=int(cfg.get("chat", {}).get("max_steps", 5)))`
- In `handle`, replace the final `return self.ask(message, history)` with:
  `return self.agent.run(message, history)`
- Keep `ask()` and `situation_brief()` in the file (still used as a fallback / reference), or delete `ask()` if unused — leaving them is fine.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_chat_agent.py -v`
Expected: PASS (whole file green)

- [ ] **Step 5: Commit**

```bash
git add trader/chat/engine.py tests/test_chat_agent.py config.yaml
git commit -m "feat(chat): route questions through AnalystAgent, keep ops deterministic"
```

---

## Task 5: Frontend — Motion voice-orb + rebuilt `v-luffy`

**Files:**
- Modify: `trader/dashboard/web/index.html`

**Interfaces:**
- Consumes: `POST /api/chat` `{message, history}` → `{reply}` (unchanged).
- Produces: rebuilt `#v-luffy` with `#luffy-orb`, mic button `#mic`, `setOrbState(state)`, voice via Web Speech.

This task is browser-side; **verification is manual QA** (no pytest).

- [ ] **Step 1: Load Motion**

In the `<head>` of `index.html`, after the existing library `<script>` tags, add:
```html
<script src="https://cdn.jsdelivr.net/npm/motion@latest/dist/motion.js"></script>
```

- [ ] **Step 2: Replace the `v-luffy` right-column markup**

Replace the "Talk to Luffy" panel (the `.plabel` + `#chat-log` + quick buttons + input row inside `#v-luffy`) with an orb header, chat log, suggestion chips, and a composer that includes a mic button:
```html
<div class="plabel">Talk to Luffy <span style="text-transform:none">· your analyst</span></div>
<div style="display:flex;align-items:center;gap:12px;padding:6px 0 12px">
  <div id="orb-wrap" style="position:relative;width:40px;height:40px">
    <div id="orb-aura" style="position:absolute;inset:-10px;border-radius:50%;background:radial-gradient(circle,rgba(0,209,160,.35),transparent 68%)"></div>
    <div id="luffy-orb" style="position:absolute;inset:0;border-radius:50%;background:radial-gradient(circle at 35% 30%,#5ff0d0,#00d1a0 55%,#00a0b8);box-shadow:0 0 20px rgba(0,209,160,.5)"></div>
  </div>
  <div id="orb-state" style="font-size:11px;color:var(--dim);font-family:var(--mono)">idle</div>
  <button id="mute" class="btn" style="margin-left:auto" onclick="toggleMute()">🔊</button>
</div>
<div id="chat-log" style="flex:1;overflow-y:auto"></div>
<div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap">
  <button class="btn" onclick="quick('how are we doing today?')">Today</button>
  <button class="btn" onclick="quick('why did you skip the last signal?')">Why skip?</button>
  <button class="btn" onclick="quick('how are the strategies doing?')">Strategy health</button>
  <button class="btn danger" onclick="quick('freeze entries')">Freeze</button>
</div>
<div style="display:flex;gap:6px;margin-top:8px">
  <input id="chat-in" class="btn" style="flex:1;background:var(--panel)" placeholder="Ask Luffy anything…" onkeydown="if(event.key==='Enter')sendChat()">
  <button id="mic" class="btn" onclick="toggleMic()" title="Speak">🎤</button>
  <button class="btn" onclick="sendChat()" style="border-color:rgba(0,209,160,.4);color:var(--green)">Send</button>
</div>
```

- [ ] **Step 3: Add orb-state + voice script**

In the dashboard's main `<script>` (near `sendChat`), add:
```javascript
/* ── Luffy orb states (Motion) ── */
const _Mo = window.Motion || null;
let _orbAnim = null;
function setOrbState(s){
  document.getElementById('orb-state').textContent = s;
  if(!_Mo) return;
  if(_orbAnim) _orbAnim.stop && _orbAnim.stop();
  const orb = '#luffy-orb', aura = '#orb-aura';
  if(s==='listening') _orbAnim=_Mo.animate(orb,{scale:[1,1.18,1]},{duration:.9,repeat:Infinity,ease:'easeInOut'});
  else if(s==='thinking') _orbAnim=_Mo.animate(orb,{scale:[1,1.12,1]},{duration:1.1,repeat:Infinity,ease:'easeInOut'});
  else if(s==='speaking') _orbAnim=_Mo.animate(aura,{opacity:[.5,1,.5],scale:[1,1.25,1]},{duration:.7,repeat:Infinity,ease:'easeInOut'});
  else _orbAnim=_Mo.animate(orb,{scale:[1,1.06,1]},{duration:4.5,repeat:Infinity,ease:'easeInOut'});
}

/* ── voice: browser Web Speech ── */
let _muted=false, _rec=null, _listening=false;
try{ _muted = localStorage.getItem('luffyMute')==='1'; }catch(e){}
function _syncMute(){ const b=document.getElementById('mute'); if(b) b.textContent=_muted?'🔇':'🔊'; }
function toggleMute(){ _muted=!_muted; try{localStorage.setItem('luffyMute',_muted?'1':'0');}catch(e){} if(_muted&&window.speechSynthesis)speechSynthesis.cancel(); _syncMute(); }
function speak(text){
  if(_muted || !window.speechSynthesis) return;
  speechSynthesis.cancel();
  const u=new SpeechSynthesisUtterance(text.replace(/[*_`#]/g,''));
  u.rate=1.05; u.onstart=()=>setOrbState('speaking'); u.onend=()=>setOrbState('idle');
  speechSynthesis.speak(u);
}
function _SR(){ return window.SpeechRecognition||window.webkitSpeechRecognition; }
function toggleMic(){
  const SR=_SR();
  if(!SR){ document.getElementById('mic').style.display='none'; return; }
  if(window.speechSynthesis) speechSynthesis.cancel();        // barge-in
  if(_listening){ _rec && _rec.stop(); return; }
  _rec=new SR(); _rec.lang='en-US'; _rec.interimResults=true; _rec.continuous=false;
  _listening=true; setOrbState('listening');
  _rec.onresult=e=>{ const t=[...e.results].map(r=>r[0].transcript).join('');
    document.getElementById('chat-in').value=t;
    if(e.results[e.results.length-1].isFinal){ _rec.stop(); } };
  _rec.onerror=()=>{ _listening=false; setOrbState('idle'); };
  _rec.onend=()=>{ _listening=false; setOrbState('idle');
    const t=document.getElementById('chat-in').value.trim(); if(t) sendChat(); };
  _rec.start();
}
```

- [ ] **Step 4: Hook `sendChat` into orb + speech**

In `sendChat()`, set `setOrbState('thinking')` right after showing the pending bubble, and after the reply is set, add `setOrbState('idle'); speak(r.reply||'');`. On the boot (`initChatChart` or the `show('luffy')` branch), call `setOrbState('idle'); _syncMute();`. Hide `#mic` if `_SR()` is falsy on load.

- [ ] **Step 5: Manual verification**

Run: `./restart.sh dashboard` then open the dashboard, go to **Talk to Luffy**:
1. Orb breathes at idle. ✓
2. Type "how are we doing today?" → orb goes **thinking**, a real data-grounded answer returns, orb **speaks** it aloud, returns to idle. ✓
3. Click 🎤, say a question → transcript fills, auto-sends on silence. ✓
4. While it's speaking, click 🎤 → speech stops (barge-in). ✓
5. Click 🔇 → no speech; reload → still muted (localStorage). ✓
6. In a browser without SpeechRecognition, mic is hidden and text chat still works. ✓

- [ ] **Step 6: Commit**

```bash
git add trader/dashboard/web/index.html
git commit -m "feat(dashboard): Motion voice-orb + Web Speech for Talk to Luffy"
```

---

## Self-Review

- **Spec coverage:** §4 agent loop → Task 3; §4.1 model/provider → Task 1; §5 toolbox → Task 2 (7 of 9 tools; `search_knowledge` and `get_price` deferred — see note); §6 voice → Task 5; §7 orb states → Task 5; §8 testing → Tasks 1–4; §9 file map → all tasks.
- **Deferred from spec (intentional, low-risk):** `search_knowledge` (vault grep) and `get_price` (needs `DataFeed`/exchange, network in tests). Add post-MVP as two more entries in `TOOLS`/`TOOL_SCHEMAS` with feed-stubbed tests; the registry test (Task 2) already enforces schema↔function parity so they can't be half-added.
- **Placeholder scan:** none — all steps carry real code.
- **Type consistency:** `chat_tools(messages, tools, deep=False)` used identically in Tasks 1/3; `TOOLS`/`TOOL_SCHEMAS` defined in Task 2, consumed in Task 3; `AnalystAgent(journal, llm, max_steps)` / `.run(message, history)` consistent across Tasks 3/4.

## Risks & checks

- **Journal column names** (`trades.closed_at`, `strategy_name`, `decisions.skip_reason`, `equity.ts`) are inferred from existing queries in `server.py`/`engine.py`. Task 2 Step 4 says: if a column differs, read `trader/core/journal.py` and adjust SQL. Do this before assuming a test failure is logic.
- **DeepSeek tool-calling**: verified OpenAI-compatible; if the live model ever returns `content` alongside `tool_calls`, the loop already records both.
- **Motion CDN**: same `dist/motion.js` global used in the approved mockups; if the dashboard is offline, the orb falls back to no-animation (guarded by `if(!_Mo)`), chat still works.
