# Research pipeline — Phase 0: groundwork — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear the ground for the research pipeline: admission refuses untestable specs, the failed LLM consumers are removed with data-only replacements, every LLM call is tagged with a purpose under its own budget, and the kernel can run heavy work in a supervised low-priority child process.

**Architecture:** No new subsystem yet. `Analyst.admit` gains one refusal. `BrainLLM` gains a `purpose` argument and per-purpose reserved budgets. The LLM Theorist becomes `brain/postmortem.py` (health against the admitted envelope, via the existing `strategy/health.assess_health`) plus a read-only `brain/doctrine.py`; the LLM Judge goes, `/judge` reads the post-mortem. `core/child.py` is a small spawn-based runner used from phase 2 on.

**Tech Stack:** Python 3.12, pytest, SQLite (WAL) via `trader/core/journal.py`, `multiprocessing` (spawn context).

**Spec:** `docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md` (Part 4 gate 4 "the admission-hole fix", Part 6 "LLM removals", Part 1 "child process").

## Global Constraints

- Run everything through the venv: `./venv/bin/python …`.
- `scripts/backtest_equivalence.py` must stay PASS; `scripts/bench_vector_backtest.py` must stay above 20x.
- Journal writes go through `Journal._tx()` or the journal's own writers (`log_brain_event`, `upsert_spec`, `kv_set`) — never `Journal.query()`, which does not commit.
- Donchian keeps trading throughout. The kernel is restarted only through `./restart.sh kernel` (the cron watchdog is live; `touch data/watchdog.off` before any deliberate stop longer than a restart).
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
  ```
- `logs/luffy.log` contains NUL bytes: use `grep -a`.

---

### Task 1: Admission refuses an untestable spec

**Files:**
- Modify: `trader/brain/analyst.py:342-353` (`Analyst.admit`)
- Test: `tests/test_admission_null_gate.py:90-95`

**Interfaces:**
- Produces: `Analyst.admit(spec, book) -> (False, ev)` with `ev["untestable"] is True` and `"untestable"` in `ev["reason"]` whenever `ev["null_consistency_p"] is None`.

- [ ] **Step 1: Replace the silence test with the refusal tests**

In `tests/test_admission_null_gate.py`, replace the whole function `test_too_few_symbols_does_not_block_admission_on_its_own` (lines 90-95) with:

```python
def test_too_few_symbols_to_test_is_refused_as_untestable(monkeypatch):
    """2026-09-11: spec_funding_filtered_trend_pullback was admitted on 20
    trades with the rotation null run on 0 symbols, because silence did not
    block. A spec nobody can test is refused, not waved through."""
    ok, ev = _admit_with(monkeypatch, {0: 0.5, 1: 0.5})
    assert ok is False
    assert ev["null_consistency_p"] is None
    assert ev["null_symbols"] == 2
    assert ev["untestable"] is True
    assert "untestable" in ev["reason"]


def test_no_symbol_carrying_a_percentile_is_refused(monkeypatch):
    ok, ev = _admit_with(monkeypatch, {})
    assert ok is False
    assert ev["null_symbols"] == 0
    assert ev["untestable"] is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_admission_null_gate.py -q -p no:cacheprovider`
Expected: 2 FAILED (`assert ok is False` — admit currently returns True on silence).

- [ ] **Step 3: Implement the refusal**

In `trader/brain/analyst.py`, replace lines 342-353 (from `ev = self._with_null_evidence(...)` to the final `return True, ev` of `admit`) with:

```python
        ev = self._with_null_evidence(ev, self._null_percentiles(spec, tf))
        # A spec that cannot beat a rotation of its OWN entries across
        # independent symbols has no edge, whatever its profit factor says.
        # Too few symbols to run that test used to be "silence", and silence
        # did not block: on 2026-09-11 that admitted a spec on 20 trades with
        # the null run on 0 symbols. Untestable is now a refusal.
        p = ev.get("null_consistency_p")
        if p is None:
            from ..strategy import null_baseline
            return False, {**ev, "untestable": True, "reason":
                           f"untestable: the rotation null ran on "
                           f"{ev['null_symbols']} symbols, fewer than the "
                           f"{null_baseline.MIN_SYMBOLS} needed to judge it"}
        if p > self.null_max_p:
            return False, {**ev, "reason":
                           f"beats its own rotation no more often than chance "
                           f"across {ev['null_symbols']} symbols "
                           f"(p={p:.2g} > {self.null_max_p})"}
        return True, ev
```

- [ ] **Step 4: Run the admission tests**

Run: `./venv/bin/python -m pytest tests/test_admission_null_gate.py tests/test_admission_records_null.py tests/test_null_consistency_gate.py tests/test_analyst.py -q -p no:cacheprovider`
Expected: all PASS. (`test_analyst.py:118` refuses a twin on overlap before the null runs; if it fails, its fixture relied on silence — give it a passing percentile set, never restore the silence rule.)

- [ ] **Step 5: Commit**

```bash
git add trader/brain/analyst.py tests/test_admission_null_gate.py
git commit -F - <<'EOF'
fix(analyst): a spec nobody can test is refused, not admitted on silence

On 2026-09-11 spec_funding_filtered_trend_pullback was admitted on exactly
20 trades with the rotation null run on 0 symbols: _null_percentiles skips
any symbol under 8 test trades, consistency_p returned None, and "silence
does not block admission". Untestable is now REFUSED for every proposer.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
```

---

### Task 2: Every LLM call carries a purpose, under its own budget

**Files:**
- Modify: `trader/brain/llm.py` (whole budget section, `chat`, `chat_tools`, `chat_json`)
- Modify: `trader/chat/agent.py:39,58`, `trader/chat/engine.py:103`, `trader/brain/spec_writer.py:222`, `trader/brain/strategist.py:76`, `trader/brain/pine.py:389`
- Modify: `trader/brain/scraper.py:22,224`, `trader/brain/crawler.py:28,175` (remove unused LLM handles)
- Modify: `config.yaml` (`brain:` block)
- Modify: `tests/test_chat_agent.py` (`_isolate_budget`)
- Test: `tests/test_llm_purpose_budget.py` (create)

**Interfaces:**
- Produces: `BrainLLM.chat(prompt, deep=False, json_mode=False, purpose="misc")`, `BrainLLM.chat_json(prompt, deep=False, purpose="misc")`, `BrainLLM.chat_tools(messages, tools, deep=False, purpose="misc")`, `BrainLLM.budget_left(purpose="misc") -> int`, `BrainLLM._spend(tokens, purpose="misc")`, `BrainLLM._purpose_today() -> dict[str, int]`, attribute `BrainLLM._usage_path: Path`, attribute `BrainLLM.purpose_budgets: dict[str, int]`. Phase 4 calls with `purpose="research"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm_purpose_budget.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_llm_purpose_budget.py -q -p no:cacheprovider`
Expected: FAIL (`_purpose_today` / `purpose` do not exist).

- [ ] **Step 3: Implement purposes in `trader/brain/llm.py`**

In `__init__`, after `self.daily_budget = int(b["daily_token_budget"])`, add:

```python
        #: purpose -> reserved daily cap. A listed purpose spends only its
        #: own slice; unlisted purposes share what is left of daily_budget
        #: after every reservation, so no consumer can eat another's slice.
        self.purpose_budgets: dict[str, int] = {
            k: int(v) for k, v in (b.get("purpose_budgets") or {}).items()}
        self._usage_path = ROOT / "data" / "brain_usage.json"
```

Replace the methods `_tokens_today`, `_spend` and `budget_left` (lines 30-47 and 54-55) with:

```python
    def _usage(self) -> dict:
        try:
            return json.loads(self._usage_path.read_text())
        except Exception:
            return {}

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _tokens_today(self) -> int:
        """Every token spent today, tagged or not."""
        return int(self._usage().get(self._today(), 0))

    def _purpose_today(self) -> dict[str, int]:
        return dict((self._usage().get("_by_purpose") or {})
                    .get(self._today()) or {})

    def _spend(self, tokens: int, purpose: str = "misc") -> None:
        try:
            data, today = self._usage(), self._today()
            data[today] = int(data.get(today, 0)) + tokens
            bp = data.setdefault("_by_purpose", {}).setdefault(today, {})
            bp[purpose] = int(bp.get(purpose, 0)) + tokens
            self._usage_path.write_text(json.dumps(data))
        except Exception:
            pass

    def budget_left(self, purpose: str = "misc") -> int:
        total_left = max(0, self.daily_budget - self._tokens_today())
        used = self._purpose_today()
        if purpose in self.purpose_budgets:
            own = self.purpose_budgets[purpose] - used.get(purpose, 0)
        else:
            shared = self.daily_budget - sum(self.purpose_budgets.values())
            own = shared - sum(v for k, v in used.items()
                               if k not in self.purpose_budgets)
        return max(0, min(total_left, own))
```

Change `chat`'s signature and its budget/spend/log lines:

```python
    def chat(self, prompt: str, deep: bool = False,
             json_mode: bool = False, purpose: str = "misc") -> str | None:
        if not self.available or self.budget_left(purpose) <= 0:
            return None
```
and inside it replace `self._spend(used)` and the `log.info(...)` with:

```python
            self._spend(used, purpose)
            log.info(f"brain call [{purpose}]: {used} tokens "
                     f"(budget left {self.budget_left(purpose)})")
```

Change `chat_tools`:

```python
    def chat_tools(self, messages: list[dict], tools: list[dict],
                   deep: bool = False, purpose: str = "misc"):
        """OpenAI-compatible tool-calling. Returns the assistant message
        object (.content, .tool_calls) or None on no-budget/error."""
        if not self.available or self.budget_left(purpose) <= 0:
            return None
```
and inside it replace `self._spend(used)` with `self._spend(used, purpose)`.

Change `chat_json`:

```python
    def chat_json(self, prompt: str, deep: bool = False,
                  purpose: str = "misc") -> dict | None:
        text = self.chat(prompt, deep=deep, json_mode=True, purpose=purpose)
```

- [ ] **Step 4: Tag every call site**

- `trader/chat/agent.py:39`: `msg = self.llm.chat_tools(messages, T.TOOL_SCHEMAS, purpose="chat")`
- `trader/chat/agent.py:58`: add `purpose="chat"` as the last argument of that `self.llm.chat_tools(` call.
- `trader/chat/engine.py:103`: `answer = self.llm.chat(prompt, deep=False, purpose="chat")`
- `trader/brain/spec_writer.py:222`: `raw = self.llm.chat_json(prompt, deep=(attempt == 0), purpose="strategist")`
- `trader/brain/strategist.py:76`: `raw = self.llm.chat_json(self._build_prompt(snap), deep=True, purpose="legacy_review")`
- `trader/brain/pine.py:389`: `raw = llm.chat_json(prompt, deep=False, purpose="tv")`
- Run `grep -n "budget_left()" trader/chat/*.py trader/brain/spec_writer.py`; in each hit pass the same purpose as that module's calls (`budget_left("chat")` in `trader/chat/`, `budget_left("strategist")` in `spec_writer.py`).

Remove the unused LLM handles (nothing calls them; `ideas.score()` is what screens):
- `trader/brain/scraper.py`: delete line 22 `from ..brain.llm import BrainLLM` and line 224 `self.llm = BrainLLM(cfg)`.
- `trader/brain/crawler.py`: delete line 28 `from ..brain.llm import BrainLLM` and line 175 `self.llm = self.scraper.llm`.
- Confirm nothing else reads them: `grep -n "self\.llm" trader/brain/scraper.py trader/brain/crawler.py` must print nothing.

- [ ] **Step 5: Reserve the slices in `config.yaml`**

In the `brain:` block, after `daily_token_budget: 200000`, add:

```yaml
  # Reserved daily slices (tokens). A listed purpose spends only its own
  # slice; unlisted purposes (strategist, tv, legacy_review, misc) share what
  # is left of daily_token_budget. `research` is held for the research
  # pipeline's Reason step so no other consumer can starve it.
  purpose_budgets:
    research: 120000
    chat: 30000
```

- [ ] **Step 6: Move the chat test to the file seam**

In `tests/test_chat_agent.py`, replace the body of `_isolate_budget` (keep its docstring) with:

```python
    import pathlib
    import tempfile
    llm._usage_path = pathlib.Path(tempfile.mkdtemp()) / "brain_usage.json"
    return llm
```

- [ ] **Step 7: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_llm_purpose_budget.py tests/test_chat_agent.py tests/test_scraper_queues_ideas.py tests/test_scraper_sources.py $(ls tests/test_crawler*.py tests/test_spec_writer*.py 2>/dev/null) -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add trader/brain/llm.py trader/chat/agent.py trader/chat/engine.py trader/brain/spec_writer.py trader/brain/strategist.py trader/brain/pine.py trader/brain/scraper.py trader/brain/crawler.py config.yaml tests/test_llm_purpose_budget.py tests/test_chat_agent.py
git commit -F - <<'EOF'
feat(llm): every call names its purpose and spends from its own slice

Spend since 2026-08-25 could only be attributed by which log line happened
to follow each call. Calls now carry a purpose, brain_usage.json keeps a
per-purpose tally beside the daily total, and brain.purpose_budgets reserves
slices no other consumer can eat: research 120k (the pipeline's Reason
step), chat 30k; everything else shares the remainder. The scraper and
crawler held BrainLLM handles that nothing called; removed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
```

---

### Task 3: A data-only post-mortem and a read-only doctrine loader

**Files:**
- Create: `trader/brain/postmortem.py`
- Create: `trader/brain/doctrine.py`
- Test: `tests/test_postmortem.py` (create; ports `tests/test_judge_health.py`)

**Interfaces:**
- Consumes: `trader.strategy.health.assess_health(expected_winrate, wins, losses) -> Health` (fields `verdict`, `trades`, `observed_winrate`, `p_underperform`, property `summary`); `journal.list_specs(states) -> [(row, spec)]`; `journal.query`; `journal.log_brain_event(kind, subject, detail)`; `journal.kv_get(key)`; `Vault.incident_note(title, body)`.
- Produces: `book_health(journal) -> list[dict]` (keys `id, name, state, live_trades, wins, pnl_usdt, health`), `run_postmortem(journal, vault=None) -> dict` (keys `ran, verdicts, changed, book, note`), `summary_text(journal) -> str`, `load_doctrine() -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_postmortem.py`:

```python
"""The Theorist without an LLM: is each live strategy still inside the
envelope it was admitted on?

The LLM Theorist spent ~55% of all tokens rewriting prose doctrine off
2-9-trade samples. What the book actually needs every 6h is this check,
which is arithmetic: Donchian Breakout Trail was admitted at a 38.85%
win rate, so 0 wins from 3 is an ordinary Tuesday, and 2 from 60 is broken.
"""
import inspect
import json

from trader.brain import postmortem as PM
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(sid, expected_wr=None):
    prov = {"expected_winrate": expected_wr} if expected_wr is not None else {}
    return StrategySpec(
        id=sid, name=sid, thesis="t", invalidation="i", provenance=prov,
        universe={"include": []}, timeframe="4h", direction="both",
        entry_long="close > ema(50)", entry_short="close < ema(50)",
        filters=[], exit=ExitSpec(), regime_filter=[], markets=["futures"])


class _Journal:
    def __init__(self, specs, trades, last=None, kv=None):
        self._specs, self._trades, self._last = specs, trades, last
        self.kv = kv or {}
        self.events = []

    def list_specs(self, states=None):
        return [({"state": "paper"}, s) for s in self._specs]

    def query(self, sql, args=()):
        if "FROM trades" in sql:
            w, n = self._trades.get(args[0], (0, 0))
            return [{"n": n, "w": w, "pnl": 0.0}]
        if "kind='postmortem'" in sql:
            return [{"detail": json.dumps(self._last)}] if self._last else []
        return []

    def log_brain_event(self, kind, subject, detail):
        self.events.append((kind, subject, detail))

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)


class _Vault:
    def __init__(self):
        self.notes = []

    def incident_note(self, title, body):
        self.notes.append((title, body))


def test_a_spec_with_a_validated_rate_gets_a_health_verdict():
    rows = PM.book_health(_Journal([_spec("s1", 0.3885)], {"s1": (2, 60)}))
    assert rows[0]["health"]["verdict"] == "DIVERGED"
    assert rows[0]["health"]["expected_winrate"] == 0.3885


def test_a_young_strategy_reads_insufficient_not_broken():
    rows = PM.book_health(_Journal([_spec("s1", 0.3885)], {"s1": (0, 3)}))
    assert rows[0]["health"]["verdict"] == "INSUFFICIENT"


def test_a_strategy_performing_as_validated_reads_consistent():
    rows = PM.book_health(_Journal([_spec("s1", 0.3885)], {"s1": (39, 61)}))
    assert rows[0]["health"]["verdict"] == "CONSISTENT"


def test_no_validated_rate_reports_no_health_rather_than_inventing_one():
    rows = PM.book_health(_Journal([_spec("s1")], {"s1": (1, 4)}))
    assert rows[0]["health"] is None


def test_the_first_run_records_every_verdict_as_a_change():
    j = _Journal([_spec("s1", 0.3885)], {"s1": (0, 3)})
    v = _Vault()
    rep = PM.run_postmortem(j, v)
    assert rep["ran"] is True
    assert rep["changed"] == {"s1": "INSUFFICIENT"}
    assert j.events[0][0] == "postmortem"
    assert len(v.notes) == 1


def test_an_unchanged_book_writes_no_note():
    j = _Journal([_spec("s1", 0.3885)], {"s1": (0, 3)},
                 last={"verdicts": {"s1": "INSUFFICIENT"}})
    v = _Vault()
    rep = PM.run_postmortem(j, v)
    assert rep["changed"] == {}
    assert v.notes == []
    assert j.events and j.events[0][0] == "postmortem"


def test_the_summary_names_each_strategy_and_the_rent():
    j = _Journal([_spec("Donchian", 0.3885)], {"Donchian": (0, 3)},
                 kv={"rent_state": json.dumps(
                     {"week_start": "2026-09-07", "net": 11.83, "bar": 50.0,
                      "status": "IN_PROGRESS"})})
    text = PM.summary_text(j)
    assert "Donchian" in text
    assert "11.83" in text and "2026-09-07" in text


def test_the_post_mortem_spends_no_tokens():
    src = inspect.getsource(PM)
    assert "BrainLLM" not in src and ".chat" not in src


def test_doctrine_loads_read_only():
    from trader.brain.doctrine import load_doctrine
    d = load_doctrine()
    assert "beliefs" in d and "version" in d
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_postmortem.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.brain.postmortem'`.

- [ ] **Step 3: Create `trader/brain/doctrine.py`**

```python
"""Doctrine, read-only.

`data/doctrine.json` holds the firm's operating beliefs. The LLM Theorist
used to rewrite them every 6h off 2-9-trade samples; it was removed on
2026-09-11 and the file is now frozen (v25) — read here, written by nothing.
"""
from __future__ import annotations

import json

from ..core.config import ROOT


def load_doctrine() -> dict:
    p = ROOT / "data" / "doctrine.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"version": 0, "updated_at": "", "beliefs": []}
```

- [ ] **Step 4: Create `trader/brain/postmortem.py`**

```python
"""The Theorist, as arithmetic: is each live strategy still inside the
envelope it was admitted on?

Replaces the LLM autopsy (removed 2026-09-11; ~55% of all tokens, output
was prose doctrine drawn from 2-9-trade samples). A spec carries the
out-of-sample win rate it was admitted on in `provenance.expected_winrate`;
`strategy.health.assess_health` asks whether the live record is consistent
with it. A note reaches the vault only when a verdict changes, so a quiet
book stays quiet.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..strategy.health import assess_health


def book_health(journal) -> list[dict]:
    out = []
    for row, spec in journal.list_specs(["paper", "active"]):
        t = journal.query(
            "SELECT COUNT(*) n, "
            "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) w, "
            "SUM(realized_pnl) pnl FROM trades "
            "WHERE strategy_id=? AND status='closed'", (spec.id,))[0]
        n, wins = int(t["n"] or 0), int(t["w"] or 0)
        exp = (spec.provenance or {}).get("expected_winrate")
        health = None
        if isinstance(exp, (int, float)) and exp > 0:
            h = assess_health(float(exp), wins, max(0, n - wins))
            health = {"verdict": h.verdict, "expected_winrate": float(exp),
                      "observed_winrate": round(h.observed_winrate, 4),
                      "p_underperform": round(h.p_underperform, 4),
                      "trades": h.trades, "summary": h.summary}
        out.append({"id": spec.id, "name": spec.name, "state": row["state"],
                    "live_trades": n, "wins": wins,
                    "pnl_usdt": round(float(t["pnl"] or 0), 2),
                    "health": health})
    return out


def _verdict(r: dict) -> str:
    return (r["health"] or {}).get("verdict", "NO_ENVELOPE")


def run_postmortem(journal, vault=None) -> dict:
    rows = book_health(journal)
    verdicts = {r["id"]: _verdict(r) for r in rows}
    last = journal.query(
        "SELECT detail FROM brain_events WHERE kind='postmortem' "
        "ORDER BY id DESC LIMIT 1")
    try:
        prev = json.loads(last[0]["detail"]).get("verdicts", {}) if last else {}
    except Exception:
        prev = {}
    changed = {k: v for k, v in verdicts.items() if prev.get(k) != v}
    rep = {"ts": datetime.now(timezone.utc).isoformat(),
           "verdicts": verdicts, "changed": changed, "book": rows}
    journal.log_brain_event("postmortem", "book", rep)
    note = ""
    if changed and vault is not None:
        body = "\n".join(
            f"- **{r['name']}** — " + (r["health"]["summary"] if r["health"]
                                       else f"{r['live_trades']} closed "
                                            f"trades, no validated envelope")
            for r in rows if r["id"] in changed)
        vault.incident_note("Book health", body)
        note = "Book health"
    return {"ran": True, **rep, "note": note}


def summary_text(journal) -> str:
    """What `/judge` answers: the book's health and the week's rent, from
    data. No model is asked anything."""
    lines = ["🧠 book health (data, no LLM)"]
    rows = book_health(journal)
    for r in rows:
        h = r["health"]
        lines.append(f"· {r['name'][:28]}: " + (
            h["summary"] if h else
            f"{r['live_trades']} closed trades, no validated envelope"))
    if not rows:
        lines.append("· no strategies in the book")
    try:
        rent = json.loads(journal.kv_get("rent_state") or "{}")
    except Exception:
        rent = {}
    if rent:
        lines.append(f"rent: week of {rent.get('week_start', '?')} net "
                     f"${float(rent.get('net') or 0):.2f} vs "
                     f"${float(rent.get('bar') or 0):.0f} · "
                     f"{rent.get('status', '')}")
    return "\n".join(lines)
```

- [ ] **Step 5: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_postmortem.py -q -p no:cacheprovider`
Expected: all PASS. (If `Health` has no `summary` property, `trader/brain/judge.py:_health_of` would already have failed — it reads `h.summary`; check `grep -n "summary" trader/strategy/health.py`.)

- [ ] **Step 6: Commit**

```bash
git add trader/brain/postmortem.py trader/brain/doctrine.py tests/test_postmortem.py
git commit -F - <<'EOF'
feat(brain): the post-mortem is arithmetic — health against the admitted envelope

The replacement that must exist before the LLM Theorist and Judge go.
book_health() checks each live spec's record against the win rate it was
admitted on (strategy.health.assess_health); run_postmortem() journals the
verdicts every run and writes a vault note only when one changes;
summary_text() is what /judge will answer. doctrine.load_doctrine() is the
read-only loader the Strategist's prompt still needs.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
```

---

### Task 4: Remove the LLM Theorist and the dead rules module

**Files:**
- Modify: `trader/kernel.py:1114-1122` (6h tick), `trader/kernel.py:443-452` (`_strategist_knowledge`)
- Modify: `trader/dashboard/server.py:367-385` (`/api/brain/autopsy`), `trader/dashboard/server.py:746-748` (Theorist card stats)
- Modify: `org.yaml:69-77` (Theorist block)
- Delete: `trader/brain/theorist.py`, `trader/brain/rules.py`
- Test: `tests/test_theorist_removed.py` (create)

**Interfaces:**
- Consumes: `run_postmortem(journal, vault)`, `load_doctrine()` from Task 3; `Vault(journal)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_theorist_removed.py`:

```python
"""The LLM Theorist is gone; its job is the data-only post-mortem.

It spent ~55% of all tokens (645k of 1.17M since 2026-08-25) writing prose
doctrine, and `brain/rules.py` — the "executable doctrine" it could set —
had no reader anywhere in the code.
"""
import importlib
import inspect

import pytest


@pytest.mark.parametrize("mod", ["trader.brain.theorist", "trader.brain.rules"])
def test_the_module_is_gone(mod):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(mod)


def test_the_kernel_runs_the_data_postmortem():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert "brain.theorist" not in src
    assert "run_postmortem" in src
    assert "load_doctrine" in src


def test_the_dashboard_autopsy_button_runs_the_postmortem():
    from trader.dashboard import server
    src = inspect.getsource(server)
    assert "brain.theorist" not in src
    assert "run_postmortem" in src


def test_the_theorist_role_wraps_the_postmortem():
    from trader.org import Org
    t = next(e for e in Org.load().employees if e.name == "Theorist")
    assert "trader.brain.postmortem" in t.wraps
    assert "postmortem" in t.events
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_theorist_removed.py -q -p no:cacheprovider`
Expected: FAIL (modules still import; kernel still references `brain.theorist`).

- [ ] **Step 3: Kernel — 6h tick**

In `trader/kernel.py`, replace:

```python
        if Kernel._outcome_tick % 360 == 5:     # ~every 6h: theorist autopsy
            try:
                from .brain.theorist import Theorist
                rep = Theorist(self.journal, self.cfg).run_autopsy()
                if rep.get("ran"):
                    log.info(f"theorist autopsy → {rep['note']} "
                             f"(doctrine v{rep.get('doctrine_version')})")
            except Exception as e:
                log.warning(f"theorist failed: {e}")
```

with:

```python
        if Kernel._outcome_tick % 360 == 5:     # ~every 6h: book post-mortem
            try:
                from .brain.postmortem import run_postmortem
                from .knowledge.vault import Vault
                rep = run_postmortem(self.journal, Vault(self.journal))
                if rep.get("changed"):
                    log.info(f"postmortem: verdicts changed {rep['changed']}")
            except Exception as e:
                log.warning(f"postmortem failed: {e}")
```

- [ ] **Step 4: Kernel — doctrine for the Strategist's prompt**

In `trader/kernel.py` `_strategist_knowledge`, replace:

```python
        try:
            from .brain.theorist import Theorist
            out["doctrine"] = Theorist._load_doctrine()
        except Exception as e:
```

with:

```python
        try:
            from .brain.doctrine import load_doctrine
            out["doctrine"] = load_doctrine()
        except Exception as e:
```

- [ ] **Step 5: Dashboard**

In `trader/dashboard/server.py`, inside `run_autopsy`, replace:

```python
            from ..brain.theorist import Theorist
            loop = asyncio.get_event_loop()
            rep = await loop.run_in_executor(
                None, lambda: Theorist(journal, cfg).run_autopsy())
            return rep
```

with:

```python
            from ..brain.postmortem import run_postmortem
            from ..knowledge.vault import Vault
            loop = asyncio.get_event_loop()
            rep = await loop.run_in_executor(
                None, lambda: run_postmortem(journal, Vault(journal)))
            return rep
```

and in `_EVENT_STATS` replace `"Theorist": lambda: [["Autopsies", str(_ev_count(["autopsy"]))],` with `"Theorist": lambda: [["Post-mortems", str(_ev_count(["postmortem"]))],`.

- [ ] **Step 6: Org roster**

In `org.yaml`, replace the Theorist block's `wraps`, `events` and `desc` lines:

```yaml
    wraps: [trader.brain.postmortem, trader.brain.doctrine]
    authors: [postmortem]
    status_source: events
    events: [postmortem, doctrine_created]
    category: RESEARCH
    desc: Checks every live strategy against the envelope it was admitted on — data only, no LLM.
```

- [ ] **Step 7: Delete the modules**

```bash
git rm trader/brain/theorist.py trader/brain/rules.py
grep -rn "brain\.theorist\|brain import theorist\|from \.theorist\|brain\.rules\|load_rules" trader/ scripts/ tests/ --include=*.py
```
Expected: the grep prints nothing.

- [ ] **Step 8: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_theorist_removed.py tests/test_postmortem.py tests/test_org.py tests/test_org_vault.py tests/test_company.py tests/test_kernel*.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add -A trader/kernel.py trader/dashboard/server.py org.yaml tests/test_theorist_removed.py trader/brain/theorist.py trader/brain/rules.py
git commit -F - <<'EOF'
remove(theorist): the LLM autopsy is replaced by the data post-mortem

The Theorist spent ~55% of all tokens since 2026-08-25 (645k of 1.17M)
rewriting prose doctrine off 2-9-trade samples, and brain/rules.py — the
executable doctrine it could set — had no reader. The kernel's 6h tick and
the dashboard's autopsy button now run brain.postmortem; the Strategist's
prompt reads doctrine through brain.doctrine, frozen at v25.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
```

---

### Task 5: Remove the LLM Judge; `/judge` reads the post-mortem

**Files:**
- Modify: `trader/kernel.py:245-247` (thread start), `trader/kernel.py:490-512` (`_brain_judge_loop`), the `/judge` branch in the Telegram handler
- Modify: `org.yaml:20` (Manager `wraps`), `config.yaml` (`brain:` judge keys)
- Delete: `trader/brain/judge.py`, `tests/test_judge.py`, `tests/test_judge_health.py` (ported to `tests/test_postmortem.py` in Task 3)
- Test: `tests/test_judge_removed.py` (create)

**Interfaces:**
- Consumes: `summary_text(journal)` from Task 3.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_judge_removed.py`:

```python
"""The LLM Judge is gone.

56 of its 58 reviews applied nothing; its promote/demote writes went through
Journal.query(), which does not commit; and `active` changes no sizing —
proving size counts the ACCOUNT's closed trades (risk.py:174). /judge now
reports book health and rent from data.
"""
import importlib
import inspect

import pytest


def test_the_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("trader.brain.judge")


def test_the_kernel_starts_no_brain_judge_thread():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert "_brain_judge_loop" not in src
    assert "brain.judge" not in src


def test_slash_judge_answers_from_the_post_mortem():
    from trader import kernel as K
    assert "summary_text" in inspect.getsource(K)


def test_the_manager_no_longer_wraps_the_judge():
    from trader.org import Org
    assert "trader.brain.judge" not in Org.load().manager.wraps


def test_config_carries_no_judge_cadence():
    from trader.core.config import load_config
    b = load_config()["brain"]
    assert "judge_interval_minutes" not in b
    assert "judge_meta_every_hours" not in b
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_judge_removed.py -q -p no:cacheprovider`
Expected: FAIL.

- [ ] **Step 3: Kernel — drop the thread and the loop**

In `trader/kernel.py`, delete these three lines from the thread starts:

```python
        if self.cfg.get("brain", {}).get("judge_interval_minutes"):
            threading.Thread(target=self._brain_judge_loop, daemon=True,
                             name="brain-judge").start()
```

and delete the whole method `_brain_judge_loop` (from `    def _brain_judge_loop(self) -> None:` through its last line `            _t.sleep(interval)`).

- [ ] **Step 4: Kernel — `/judge`**

Replace the whole `elif msg.startswith("/judge"):` branch (up to, not including, `elif msg.startswith("/tv"):`) with:

```python
        elif msg.startswith("/judge"):
            try:
                from .brain.postmortem import summary_text
                reply(summary_text(self.journal))
            except Exception as e:
                reply(f"/judge failed: {e}")
```

- [ ] **Step 5: Org and config**

- `org.yaml` line 20: `  wraps: [trader.kernel, trader.engine.orchestrator]`
- `config.yaml`: delete the two lines `  judge_interval_minutes: 360 …` and `  judge_meta_every_hours: 168 …` from the `brain:` block.

- [ ] **Step 6: Delete the module and its tests**

```bash
git rm trader/brain/judge.py tests/test_judge.py tests/test_judge_health.py
grep -rn "brain\.judge\|BrainJudge\|StrategyJudge\|_brain_judge_loop" trader/ scripts/ tests/ --include=*.py
```
Expected: the grep prints only `tests/test_judge_removed.py` lines.

- [ ] **Step 7: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_judge_removed.py tests/test_postmortem.py tests/test_org.py tests/test_company.py tests/test_kernel*.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add -A trader/kernel.py org.yaml config.yaml tests/test_judge_removed.py trader/brain/judge.py tests/test_judge.py tests/test_judge_health.py
git commit -F - <<'EOF'
remove(judge): /judge answers from data; the LLM review is gone

56 of 58 Brain-Judge reviews applied nothing, its promote/demote writes
went through the non-committing Journal.query(), and `active` changes no
sizing. StrategyJudge (Pine forging) had no caller outside its own test.
/judge now reports brain.postmortem's book health and the week's rent.
Its health tests moved to tests/test_postmortem.py.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
```

---

### Task 6: Delete the orphaned Proposer

**Files:**
- Delete: `trader/strategy/proposer.py`
- Modify: `tests/test_single_creation_path.py:82-100` (`test_review_runs_no_proposer`)

- [ ] **Step 1: Rewrite the guard to assert the module is gone**

In `tests/test_single_creation_path.py`, add `import importlib` and `import pytest` beside the existing imports, and replace the whole function `test_review_runs_no_proposer` with:

```python
def test_the_legacy_proposer_is_gone():
    """It had no caller after 2026-09-11; a module that writes strategies
    and is called by nothing is a second creation path waiting for one."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("trader.strategy.proposer")
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_single_creation_path.py -q -p no:cacheprovider`
Expected: 1 FAILED (`test_the_legacy_proposer_is_gone` — the module still imports).

- [ ] **Step 3: Delete it**

```bash
git rm trader/strategy/proposer.py
grep -rn "import proposer\|from .proposer\|strategy\.proposer" trader/ scripts/ tests/ --include=*.py
```
Expected: the grep prints only the new test line.

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_single_creation_path.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A tests/test_single_creation_path.py trader/strategy/proposer.py
git commit -F - <<'EOF'
remove(proposer): an orphaned strategy writer is a creation path waiting

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
```

---

### Task 7: A kernel-owned, low-priority child-process runner

**Files:**
- Create: `trader/core/child.py`
- Test: `tests/test_child_runner.py` (create)

**Interfaces:**
- Produces: `run_child(fn, *args, timeout_s: float, nice: int = 19, **kwargs) -> ChildResult`; `@dataclass ChildResult(ok: bool, value: Any = None, error: str = "", timed_out: bool = False, elapsed_s: float = 0.0, exitcode: int | None = None)`. `fn` must be importable at module top level (the spawn context pickles it by reference). Phase 2's search batches run through this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_child_runner.py`:

```python
"""Heavy research work runs in a supervised child, never in the kernel.

The kernel's pandas work holds the GIL, so a CPU-heavy search thread would
slow the trade loop directly, and a crash in it would take trading down.
The child is spawned (no inherited SQLite handles or threads), niced, and
killed at its deadline.
"""
import os
import time

from trader.core.child import run_child


def _double(x):
    return 2 * x


def _boom():
    raise ValueError("search blew up")


def _sleep(s):
    time.sleep(s)
    return "late"


def _niceness():
    return os.nice(0)


def _hard_exit():
    os._exit(3)


def test_the_value_comes_back():
    r = run_child(_double, 21, timeout_s=60)
    assert r.ok is True and r.value == 42 and r.error == ""


def test_an_exception_is_reported_not_raised():
    r = run_child(_boom, timeout_s=60)
    assert r.ok is False
    assert "ValueError" in r.error and "search blew up" in r.error


def test_a_child_past_its_deadline_is_killed():
    t0 = time.monotonic()
    r = run_child(_sleep, 30, timeout_s=2)
    assert r.ok is False and r.timed_out is True
    assert time.monotonic() - t0 < 15


def test_the_child_runs_at_low_priority():
    r = run_child(_niceness, timeout_s=60, nice=19)
    assert r.ok is True and r.value >= 19


def test_a_child_that_dies_without_answering_is_a_failure():
    r = run_child(_hard_exit, timeout_s=60)
    assert r.ok is False and r.timed_out is False
    assert r.exitcode == 3
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_child_runner.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.core.child'`.

- [ ] **Step 3: Implement `trader/core/child.py`**

```python
"""Run one function in a separate, low-priority process with a deadline.

The research pipeline's search is CPU-heavy pandas work. Run on a kernel
thread it would hold the GIL against the trade loop, and an exception or a
memory blow-up in it would be the kernel's. So the kernel thread owns the
schedule and hands each batch to a child:

- **spawn**, never fork: the kernel holds threads and SQLite connections,
  and a forked copy of those is undefined behaviour;
- **niced** (19 by default), so the trade loop always wins the CPU;
- **killed at its deadline**, and a child that dies without answering is a
  failure with its exit code, never a hang.

The child must not write the database: it returns results, and the calling
thread writes them through the journal.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ChildResult:
    ok: bool
    value: Any = None
    error: str = ""
    timed_out: bool = False
    elapsed_s: float = 0.0
    exitcode: int | None = None


def _entry(conn, fn, args, kwargs, nice):
    try:
        if nice:
            os.nice(nice)
        conn.send(("ok", fn(*args, **kwargs)))
    except BaseException:
        conn.send(("err", traceback.format_exc()[-4000:]))
    finally:
        conn.close()


def run_child(fn: Callable, *args, timeout_s: float, nice: int = 19,
              **kwargs) -> ChildResult:
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    p = ctx.Process(target=_entry, args=(child, fn, args, kwargs, nice),
                    daemon=True)
    t0 = time.monotonic()
    p.start()
    child.close()
    try:
        if not parent.poll(timeout_s):
            return ChildResult(ok=False, timed_out=True,
                               error=f"timed out after {timeout_s}s",
                               elapsed_s=time.monotonic() - t0)
        try:
            kind, payload = parent.recv()
        except EOFError:
            p.join(5)
            return ChildResult(ok=False, exitcode=p.exitcode,
                               error=f"child exited with code {p.exitcode} "
                                     f"before answering",
                               elapsed_s=time.monotonic() - t0)
        p.join(5)
        if kind == "ok":
            return ChildResult(ok=True, value=payload, exitcode=p.exitcode,
                               elapsed_s=time.monotonic() - t0)
        return ChildResult(ok=False, error=payload, exitcode=p.exitcode,
                           elapsed_s=time.monotonic() - t0)
    finally:
        if p.is_alive():
            p.terminate()
            p.join(5)
        if p.is_alive():
            p.kill()
            p.join(5)
        parent.close()
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_child_runner.py -q -p no:cacheprovider`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/core/child.py tests/test_child_runner.py
git commit -F - <<'EOF'
feat(core): run heavy work in a spawned, niced child with a deadline

The research search must not share the kernel's GIL or its fate. run_child
spawns (never forks a process holding threads and SQLite handles), nices
to 19, kills at the deadline, and reports a child that died without
answering as a failure with its exit code.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
```

---

### Task 8: Record it, verify everything, restart

**Files:**
- Modify: `CLAUDE.md` (firm table rows 94-95, daemon table row 149, line 154, line 163, the open-fault bullet at ~420, line 459)

- [ ] **Step 1: Update CLAUDE.md**

- Firm table — replace the Theorist row with:
  `| Theorist | `brain/postmortem.py`, `brain/doctrine.py` | Every 6h, checks each live strategy against the envelope it was admitted on — data only, no LLM. `data/doctrine.json` is frozen at v25: read, never rewritten. |`
- Firm table — replace the Manager row with:
  `| Manager | `kernel.py`, `engine/orchestrator.py` | Coordinates. `/judge` reports book health and rent from data. The LLM Judge was removed 2026-09-11 (56 of 58 reviews applied nothing). |`
- After the firm table, add one paragraph: "**LLM spend is per-purpose.** Every `BrainLLM` call passes `purpose=`; `brain.purpose_budgets` reserves slices (research 120k, chat 30k) that no other consumer can eat, and the rest share the remainder of `daily_token_budget`. `data/brain_usage.json` keeps the per-purpose tally under `_by_purpose`."
- Daemon table — delete the `brain-judge` row.
- Line 154 — replace "the Theorist autopsy (~360 cycles)" with "the data-only book post-mortem (~360 cycles)".
- Line 163 — replace "is silence, not a pass, and does not block on its own." with "is **untestable, and REFUSED** — since 2026-09-11, when silence admitted a spec on 20 trades with the null run on 0 symbols."
- The open-fault bullet "A spec can be admitted with no null evidence at all" — append: "**Closed for new admissions 2026-09-11:** untestable is now REFUSED (`Analyst.admit`)."
- Line 459 — replace "not beliefs the Theorist may rewrite." with "not beliefs to be rewritten."

- [ ] **Step 2: Full verification**

Run each and read the output:

```bash
timeout 2400 ./venv/bin/python -m pytest tests/ -q -p no:cacheprovider 2>&1 | grep -aE "^FAILED|^ERROR|passed|failed" | tail -30
timeout 900 ./venv/bin/python scripts/backtest_equivalence.py 2>&1 | tail -1
timeout 900 ./venv/bin/python scripts/bench_vector_backtest.py 2>&1 | tail -4
```

Expected: no failures other than the known wall-clock test `test_macro_guard::test_a_restart_reuses_the_cached_calendar` (CLAUDE.md open fault); `PASS`; speedup above 20x.

- [ ] **Step 3: Restart and watch the kernel boot**

```bash
./restart.sh kernel && ./restart.sh dashboard
```

Then wait for a fresh cycle and check the boot:

```bash
grep -a "" logs/luffy.log | tail -400 | grep -a "BOOT\|cycle #\|Traceback\|\[ERROR\]\|brain call \[" | tail -10
./venv/bin/python -m trader.kernel --status
```

Expected: `LUFFY BOOT`, at least one `cycle #`, no `Traceback`; `heartbeat_age_s` under 120; the next `brain call [...]` line (whenever the mechanism thread next writes a spec) carries a purpose tag.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -F - <<'EOF'
docs: phase 0 — untestable is refused, the Theorist is arithmetic, the Judge is gone

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
```
