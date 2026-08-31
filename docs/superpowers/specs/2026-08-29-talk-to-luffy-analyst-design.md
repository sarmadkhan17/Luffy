# Talk to Luffy — Intelligent Voice Analyst

**Date:** 2026-08-29
**Status:** Design approved, pending written-spec review
**Scope:** Upgrade the existing "Talk to Luffy" chat into a reasoning, voice-enabled personal trading analyst.

---

## 1. Goal

Turn the Talk-to-Luffy page from a single-snapshot Q&A into a **personal trading analyst** you can **speak to and hear back from**, that reasons over your live trading data on demand instead of answering from a fixed brief.

Two capability jumps:
- **(A) Intelligence** — a tool-calling agent that queries the journal/vault/prices itself and reasons over the results.
- **(B) Voice** — talk to it, and it talks back (free, on-device browser APIs).

## 2. Current state (baseline)

`trader/chat/engine.py` (135 lines) already:
- Builds a **fixed** `situation_brief()` from the journal (equity, open positions, today's taken/skipped, last 5 signals, 8 strategies, agent accuracy) and sends it to DeepSeek via `BrainLLM`.
- Matches **ops commands** (freeze/halt/panic/resume) deterministically in `detect_ops()`; the LLM never triggers them.
- Served by `POST /api/chat` in `trader/dashboard/server.py`, rendered in the `v-luffy` view in `web/index.html` via `sendChat()`.

**Limitations:** one static snapshot (can't drill into "why did ARB lose", "all VWAP-fade trades this week", win-rate trend); capped depth; ≤120-word answers; **no voice**.

## 3. Non-goals (explicit)

- **No LLM-driven ops.** Freeze/halt/close/panic stay on the deterministic layer. The analyst is **read-only**.
- **No agency / action-taking** (not even confirm-to-execute) in this iteration.
- **No premium cloud voice** (ElevenLabs/Whisper) — browser APIs only, behind an interface for later.
- **No local Hermes/Ollama** now — this dev box (7.7 GB RAM, no GPU, kernel already ~3.9 GB) can't host it. DeepSeek now; provider boundary kept clean to swap Hermes-via-Ollama in later.
- **No token streaming** (SSE) in v1 — reply arrives whole, then animates/speaks. Streaming is a future enhancement.

## 4. Architecture — tool-calling analyst

Replace the static brief with a bounded **agent loop**:

```
user msg ──▶ detect_ops() ──(match)──▶ deterministic ops reply  [unchanged]
              │ no match
              ▼
        AnalystAgent.run(msg, history)
              │
              ▼  system prompt + tool schemas + conversation
        ┌──────────────── loop (max ~5 steps, budget-guarded) ──────────────┐
        │  BrainLLM.chat_tools() → model emits tool_call(s)                  │
        │  execute whitelisted read-only tool(s) against Journal            │
        │  feed tool_response back into the model                           │
        └──────────────── until model emits a final answer ─────────────────┘
              ▼
        final answer (text) ──▶ /api/chat reply ──▶ UI renders + speaks
```

- New module `trader/chat/agent.py`: `AnalystAgent` (the loop) + a **tool registry**.
- `ChatEngine.handle()` keeps `detect_ops()` first, then delegates the `ask` path to `AnalystAgent` instead of the single-shot `ask()`.
- **Safety rails:** tools are a fixed whitelist of typed Python functions — **never arbitrary SQL**. Every figure the analyst states must come from a tool result; the system prompt forbids inventing numbers. Loop is capped (steps + `BrainLLM` daily token budget). On budget exhaustion / API error → existing "brain offline" fallback.

### 4.1 Model / provider

- **DeepSeek** now, via existing `BrainLLM`. Extend it with a `chat_tools(messages, tools)` method using DeepSeek's OpenAI-compatible function-calling.
- Provider stays swappable (base_url/model/key in config). **Future:** point at Hermes 4 on Ollama (another machine) or OpenRouter with no code change.

## 5. The analyst's toolbox (read-only)

Each tool is a typed function backed by a tested `Journal` query. Target set (~9):

| Tool | Returns |
|---|---|
| `get_positions()` | open trades + live mark/uPnL (reuse `_position_marks`) |
| `get_trades(symbol?, strategy?, since?, status?, limit)` | trade rows |
| `get_pnl(period, group_by?)` | realized P&L, win-rate, profit factor; optionally by symbol/strategy |
| `get_equity_curve(since)` | equity points for trend questions |
| `get_decisions(symbol?, action?, since?, limit)` | decisions incl. `skip_reason` + votes → "why did you skip/enter X" |
| `get_strategy_performance(name?)` | strategy state + stats |
| `get_agent_stats(since_hours)` | analyst accuracy |
| `search_knowledge(query)` | Obsidian vault search (postmortems/daily/strategies) |
| `get_price(symbol, tf?)` | recent price / basic indicators |

Tool schemas are declared once (name, description, JSON params) and passed to the model each turn. Adding a tool later = one registry entry + one query fn + one test.

## 6. Voice (free browser APIs, both directions)

Thin `web` module `voice.js` wrapping two Web Speech APIs, so premium cloud voice can replace it later without UI changes.

- **Input (STT):** `SpeechRecognition` — mic button toggles listening; interim transcript shows live in the input; on final result, auto-send.
- **Output (TTS):** `SpeechSynthesis` speaks each reply. Voice/rate chosen for clarity.
- **Barge-in:** tapping the mic while Luffy is speaking cancels TTS and starts listening.
- **Mute toggle:** persists in `localStorage` (wrapped in try/catch; page works if storage throws).
- **Degradation:** if the browser lacks the APIs, hide the mic, show a note, chat still works by text.

## 7. Frontend — the orb becomes a voice presence

Rebuild `v-luffy` in the approved Motion language (validated in brainstorm mockups):
- Load Motion in `index.html` alongside existing libs (lightweight-charts, vis-network).
- **Breathing orb with states** driven by Motion:
  - `idle` → slow breathe
  - `listening` → pulse + ring scaled to live mic input level
  - `thinking` → faster pulse + staggered dots
  - `speaking` → ripples while TTS is active
- Replies **stream in word-by-word** (staggered reveal); inline stat tokens highlighted.
- Suggested-question chips seed common asks; mic button in the composer.
- No overshoot springs (trading UI — confident, not playful).

## 8. Testing

- **Unit-test each tool** against a seeded in-memory journal (deterministic rows → expected output).
- **Test `detect_ops` still intercepts** before the agent (freeze/halt/panic/resume never reach the LLM).
- **Agent loop with a stubbed LLM** — inject a scripted tool-call sequence, assert tools run, results feed back, final answer returned. **No live API in CI.**
- **Budget/error paths** — exhausted budget and API error both return the graceful fallback.
- Voice is browser-side → manual QA (mic permission, listen→send, speak-back, barge-in, mute persistence, unsupported-browser fallback).

## 9. File-level change map

| File | Change |
|---|---|
| `trader/chat/agent.py` | **new** — `AnalystAgent` loop + tool registry |
| `trader/chat/tools.py` | **new** — read-only tool functions + JSON schemas |
| `trader/chat/engine.py` | keep `detect_ops`; delegate `ask` path to `AnalystAgent` |
| `trader/brain/llm.py` | add `chat_tools(messages, tools)` (OpenAI-compatible function-calling) |
| `trader/dashboard/web/index.html` | rebuild `v-luffy` (Motion orb + states), add mic button, load Motion |
| `trader/dashboard/web/voice.js` | **new** — STT/TTS wrapper (or inline module in index.html) |
| `tests/test_chat_agent.py` | **new** — tools, ops-intercept, stubbed-LLM loop, budget paths |
| `config.yaml` | chat model/provider knobs; voice defaults (optional) |

## 10. Future / follow-ups

- Swap model to **Hermes 4** (Ollama on a capable machine, or OpenRouter) — config-only.
- **Token streaming** (SSE) for faster first-word + tighter TTS.
- **Premium voice** behind the `voice.js` interface.
- Optional **confirm-to-act** agency (separate design; keeps read-only default).
- The **neural-brain graph** visual (approved separately in brainstorm) is its own spec/plan.
