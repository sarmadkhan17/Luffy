"""The Strategist's authoring hand — an LLM writes a StrategySpec.

This is the job an LLM is actually good at and the one the old pipeline never
gave it. `proposer._llm_genome()` handed DeepSeek a dict of gene ranges and
asked it to pick floats — the single task a language model is worst at — while
`harvester.extract_batch()` instructed it to "map it to the NEAREST family and
fill unspecified genes with schema midpoints", which is why 409 scraped ideas
produced zero strategies.

Here it composes a mechanism: names the inefficiency, writes the entry and
filter logic in the expression DSL, and chooses its own exits. The optimizer
tunes literals; the Analyst decides whether it is working. Compile errors are
fed back so the model repairs its own output rather than the caller guessing.
"""
from __future__ import annotations

import json
import logging
import re

from ..strategy import dsl
from ..strategy.compile import compile_spec
from ..strategy.features import FEATURES
from ..strategy.spec import (MIN_INVALIDATION, MIN_THESIS, StrategySpec,
                             ExitSpec)

log = logging.getLogger(__name__)

MAX_REPAIRS = 2


def vocabulary() -> str:
    """The feature registry, rendered for a prompt.

    Novelty is bounded by this list — a mechanism whose observables are not
    here cannot be expressed, which is precisely why the old eight-template
    system could only ever produce moving-average variants.
    """
    lines = []
    for name in sorted(FEATURES):
        f = FEATURES[name]
        args = []
        for a in f.arg_specs:
            args.append("expr" if a == "series" else
                        f"{a[0].__name__}[{a[1]}..{a[2]}]"
                        if a[1] is not None else "str")
        sig = f"{name}({', '.join(args)})" if args else name
        note = ""
        if f.requires != ("ohlcv",):
            note = f"  [needs {'+'.join(f.requires)}]"
        elif f.domain:
            note = f"  [range {f.domain[0]}..{f.domain[1]}]"
        lines.append(f"  {sig}{note}")
    return "\n".join(lines)


_RULES = """DSL rules (violations are rejected, not repaired for you):
- An expression is ONE restricted Python boolean expression.
- Allowed: and or not, < <= > >= == !=, + - * /, parentheses, numbers,
  and calls to the features listed above. NOTHING else.
- Forbidden: attributes, indexing with [], comprehensions, lambdas, keyword
  arguments, ** , if/else expressions, assignment, semicolons.
- Use prev(expr, n) for a value n bars ago. A CROSS is
  `prev(rsi(14), 1) < 30 and rsi(14) >= 30`, which is a different and much
  rarer event than the level test `rsi(14) >= 30`.
- donchian_hi(n) already excludes the current bar. `prev(close,1) >
  donchian_hi(48)` is therefore UNSATISFIABLE — a close cannot exceed the
  maximum high of a window containing it. Use prev(donchian_hi(48), 2).
- Every numeric literal you write becomes a tunable parameter, so choose a
  sensible centre rather than an extreme.
- Features marked [needs ...] read data that may not span the test window; a
  spec using them is refused unless the data covers it."""


#: Facts this firm established by measurement, as opposed to beliefs it
#: currently holds. Doctrine is the Theorist's to rewrite; this is not.
_MEASURED = """What measurement has already established here — do not re-litigate it:
- A profit factor on its own is a statement about arithmetic. Exit geometry
  sets a win rate by itself: TP 4.5 ATR over SL 2.5 ATR pays a coin flip 35.7%
  of the time whatever the entry says. An ALWAYS-LONG rule scored PF 1.28 on
  real candles purely on market drift. Your strategy is measured against a
  rotation of its OWN entry signals, so a mechanism that cannot beat its own
  signals fired at a random offset has no edge, whatever its profit factor.
- 23 specs were screened this way. Most scored AT or BELOW the no-edge line
  of ~0.76, and a Bollinger band fade landed at the 10th percentile over 272
  trades — materially worse than entering at random. Classic single-indicator
  reversion on 15m bars is the most heavily mined space there is; assume it is
  arbitraged unless you have a reason it is not.
- The one mechanism that beat both its rotation null AND an always-long/
  always-short control did so with NO fixed target, an ATR trailing stop, a
  long hold, and BOTH directions enabled. Long alone won 2 of 5 yearly windows
  and short alone 3 of 5; only the pair won 4 of 5, because the two legs are
  anti-correlated across regimes. A one-directional trend rule is usually a
  market-direction bet wearing a strategy's clothes.
- A fixed target amputates the fat right tail a continuation mechanism lives
  on. If your thesis is that a move persists, do not cap it.
- 4h beat 1h on every mechanism tested, because cost is charged per round trip.
- Declare `universe`: a liquidity floor is part of the strategy. The largest
  single loss on record (-$199.24) was taken on a market too thin for any
  evidence to cover."""


def _prompt(idea: dict | None, doctrine: dict | None, avoid: list,
            data: str = "") -> str:
    head = ("You are the Strategist for an autonomous crypto futures trading "
            "firm. Write ONE new trading strategy.\n\n")
    if idea:
        head += (f"Research the Researcher surfaced:\n"
                 f"  source: {idea.get('source', 'unknown')}\n"
                 f"  title: {idea.get('title', '')}\n"
                 f"  text: {(idea.get('text') or '')[:900]}\n\n"
                 "Express its MECHANISM if it has a testable one. Do not force "
                 "it into a shape it does not have — say so by writing a "
                 "different strategy instead.\n\n")
    if doctrine:
        head += f"Operating beliefs:\n{json.dumps(doctrine)[:600]}\n\n"
    if data:
        # Its own section, not folded into `doctrine`: the brief is the
        # binding constraint on what can be SCORED, and truncating it away
        # is how three consecutive specs got written against series the
        # Analyst then refused for coverage.
        head += f"{data}\n\n"
    if avoid:
        head += ("The book already trades these, so a near-duplicate is "
                 f"worthless however well it backtests:\n  "
                 + "\n  ".join(avoid[:12]) + "\n\n")
    return (
        head
        + "Available features:\n" + vocabulary() + "\n\n" + _RULES + "\n\n"
        + _MEASURED + "\n\n"
        "Judgement you should apply:\n"
        "- No edge lasts. Aim at a mechanism that pays in SOME regime, and "
        "say in `invalidation` how you would know it has stopped.\n"
        "- Costs are real: a round trip consumes ~18.5% of the risk staked on "
        "15m bars against ~7.8% on 4h. Choose the timeframe deliberately.\n"
        "- Exits are yours. A fade wants a tight stop and a near target; a "
        "continuation wants room and time.\n\n"
        "Output JSON ONLY, no prose:\n"
        "{\n"
        '  "name": "Three To Five Words",\n'
        f'  "thesis": ">= {MIN_THESIS} chars naming the inefficiency and WHY '
        'it exists (who is on the other side, and why they act)",\n'
        f'  "invalidation": ">= {MIN_INVALIDATION} chars: how this dies",\n'
        '  "timeframe": "15m" | "1h" | "4h",\n'
        '  "direction": "long" | "short" | "both",\n'
        '  "entry_long": "<DSL expression, or empty string>",\n'
        '  "entry_short": "<DSL expression, or empty string>",\n'
        '  "filters": ["<DSL expression>", ...],\n'
        '  "regime_filter": ["TRENDING_UP"|"TRENDING_DOWN"|"RANGING"|"VOLATILE"],\n'
        '  "universe": {"include": [], "exclude": [],\n'
        '               "min_volume_usdt": 100000000},\n'
        '  "exit": {"stop": {"kind":"atr","mult":2.5},\n'
        '           "target": {"kind":"rr","v":3.0} | {"kind":"none"},\n'
        '           "trail": {"kind":"atr","mult":3.0,"arm_at_r":1.0},\n'
        '           "time": {"max_bars": 200}}\n'
        "}")


#: a liquidity floor is part of a strategy, not a global setting. The largest
#: single loss on record was taken on a market too thin for evidence to cover.
_DEFAULT_MIN_VOLUME = 100_000_000


def _universe(raw) -> dict:
    """Whatever the writer declared, normalised. Was hardcoded to empty, so a
    declared universe was silently discarded and the scan planner saw nothing.
    """
    u = raw if isinstance(raw, dict) else {}
    inc = [str(x) for x in (u.get("include") or []) if x]
    exc = [str(x) for x in (u.get("exclude") or []) if x]
    try:
        floor = float(u.get("min_volume_usdt", _DEFAULT_MIN_VOLUME))
    except (TypeError, ValueError):
        floor = _DEFAULT_MIN_VOLUME
    return {"include": inc, "exclude": exc, "min_volume_usdt": floor}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "spec").lower()).strip("_")[:40]


def _to_spec(raw: dict, provenance: dict) -> StrategySpec:
    ex = raw.get("exit") or {}
    return StrategySpec(
        id=f"spec_{_slug(raw.get('name', ''))}",
        name=(raw.get("name") or "").strip(),
        thesis=(raw.get("thesis") or "").strip(),
        invalidation=(raw.get("invalidation") or "").strip(),
        provenance=provenance,
        universe=_universe(raw.get("universe")),
        timeframe=raw.get("timeframe") or "1h",
        direction=raw.get("direction") or "long",
        entry_long=(raw.get("entry_long") or "").strip(),
        entry_short=(raw.get("entry_short") or "").strip(),
        filters=[f for f in (raw.get("filters") or []) if (f or "").strip()],
        exit=ExitSpec(
            stop=ex.get("stop") or {"kind": "atr", "mult": 2.0},
            target=ex.get("target") or {"kind": "rr", "v": 2.0},
            trail=ex.get("trail") or {"kind": "none"},
            time=ex.get("time") or {"max_bars": 32},
            signal_exit=(ex.get("signal_exit") or "")),
        regime_filter=raw.get("regime_filter") or ["RANGING"],
        markets=["futures"])


class SpecWriter:
    """Wraps a BrainLLM. `llm` needs only `.available` and `.chat_json`."""

    def __init__(self, llm):
        self.llm = llm

    def write(self, idea: dict | None = None, doctrine: dict | None = None,
              avoid: list | None = None,
              data: str = "") -> tuple[StrategySpec | None, dict]:
        """Compose one spec. Returns (spec_or_None, trace)."""
        if not (self.llm and getattr(self.llm, "available", False)):
            return None, {"reason": "no LLM available"}

        prompt = _prompt(idea, doctrine, avoid or [], data)
        trace: dict = {"attempts": []}
        for attempt in range(MAX_REPAIRS + 1):
            raw = self.llm.chat_json(prompt, deep=(attempt == 0),
                                     purpose="strategist")
            if not isinstance(raw, dict):
                trace["attempts"].append({"error": "non-JSON reply"})
                prompt = _prompt(idea, doctrine, avoid or [], data) + \
                    "\n\nYour previous reply was not valid JSON. Output JSON only."
                continue
            provenance = {"source_kind": "strategist",
                          "author": "llm",
                          "idea_id": (idea or {}).get("idea_id", ""),
                          "source_url": (idea or {}).get("url", "")}
            try:
                spec = _to_spec(raw, provenance)
            except Exception as e:
                trace["attempts"].append({"error": f"malformed: {e}"})
                continue

            errs = StrategySpec.validate(spec)
            if not errs:
                try:
                    compile_spec(spec)      # also derives data_requires
                    trace["attempts"].append({"ok": True, "name": spec.name})
                    return spec, trace
                except dsl.SpecError as e:
                    errs = [str(e)]

            trace["attempts"].append({"errors": errs[:4],
                                      "name": raw.get("name")})
            if attempt == MAX_REPAIRS:
                break
            # feed the errors back — the model repairs its own output far
            # more reliably than any heuristic the caller could apply
            prompt = (_prompt(idea, doctrine, avoid or [], data)
                      + "\n\nYour previous attempt was REJECTED:\n"
                      + json.dumps(raw)[:900]
                      + "\n\nProblems:\n- " + "\n- ".join(str(e) for e in errs[:4])
                      + "\n\nFix them and output the corrected JSON only.")
        return None, {**trace, "reason": "no valid spec after repairs"}
