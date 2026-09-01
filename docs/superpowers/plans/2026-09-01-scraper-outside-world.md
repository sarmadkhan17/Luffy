# Scraper: the outside world, done right — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Scraper collect real prebuilt strategies and real research prose from the web, and queue them on two labelled streams — one for the Strategist, one for the Researcher.

**Architecture:** The Scraper keeps its existing shape (scrape → screen → rank → queue) but changes what it points at, what it stores, and who it stores it for. Its second job — turning scraped text into legacy genomes and deploying them straight into the population — is deleted; there is one strategy-creation path and it runs through the Strategist and the Analyst.

**Tech Stack:** Python 3.12, `requests`, `re`, SQLite via `trader.core.journal`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md`

## Global Constraints

- Python 3.12; run everything through `./venv/bin/python`.
- No new third-party dependencies.
- `MIN_TEXT = 80` in `trader/brain/ideas.py` is the queue's quality floor; do not lower it.
- Storage stays in `brain_events`. Do not add tables.
- Backwards compatibility: rows written before this plan carry no stream label and must keep behaving as `strategy` items.
- Full suite must pass: `./venv/bin/python -m pytest tests/ -q`. Baseline is **534 passing**.
- Every task ends with a commit.

## File Structure

| file | responsibility | change |
|---|---|---|
| `trader/brain/ideas.py` | the queue: record, screen, rank, consume | add a `stream` label |
| `trader/brain/scraper.py` | fetch prebuilt strategies + research prose, queue them | retarget, add research scorer, delete Job B |
| `trader/brain/crawler.py` | deep-read finance sites, queue passages | delete its Job B call site |
| `config.yaml` | knobs | TV section/tags, budget, cadence |
| `tests/test_ideas_streams.py` | NEW — stream labelling and filtering | create |
| `tests/test_scraper_research_stream.py` | NEW — research scorer and routing | create |
| `tests/test_scraper_sources.py` | existing — 6 tests cover Job B | delete those tests |
| `scripts/analyst_gauntlet.py` | operator script driving Job B | delete |

---

### Task 1: The idea queue carries a stream label

**Files:**
- Modify: `trader/brain/ideas.py`
- Test: `tests/test_ideas_streams.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `ideas.record(journal, idea, stream="strategy")`, `ideas.pending(journal, stream=None, ...)`, `ideas.next_idea(journal, stream=None, ...)`. `stream` is `"strategy"` or `"research"`. `pending(stream=None)` returns every stream.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ideas_streams.py
"""The queue serves two consumers now, so an item has to say which it is for."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.brain import ideas
from trader.core.journal import Journal

TEXT = ("Funding divergence is a mean-reversion entry: go long on the "
        "reclaim, stop-loss below the swept low, take-profit at the range "
        "midpoint. The exit is time-based if momentum does not resume.")


def _j(tmp_path):
    return Journal(tmp_path / "q.db")


def test_pending_filters_by_stream(tmp_path):
    j = _j(tmp_path)
    ideas.record(j, {"idea_id": "s1", "title": "A strategy", "text": TEXT,
                     "source": "tv"}, stream="strategy")
    ideas.record(j, {"idea_id": "r1", "title": "A finding", "text": TEXT,
                     "source": "arxiv"}, stream="research")

    assert {i["idea_id"] for i in ideas.pending(j, stream="strategy")} == {"s1"}
    assert {i["idea_id"] for i in ideas.pending(j, stream="research")} == {"r1"}
    assert {i["idea_id"] for i in ideas.pending(j)} == {"s1", "r1"}


def test_rows_written_before_streams_existed_read_as_strategy(tmp_path):
    j = _j(tmp_path)
    # exactly what the old code wrote: no stream key at all
    j.log_brain_event(ideas.KIND, "legacy1",
                      {"title": "Old", "text": TEXT, "source": "rss",
                       "url": "", "score": 9.0})

    got = ideas.pending(j, stream="strategy")

    assert [i["idea_id"] for i in got] == ["legacy1"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_ideas_streams.py -q`
Expected: FAIL — `record()` takes 2 positional arguments but 3 were given.

- [ ] **Step 3: Implement**

In `trader/brain/ideas.py`, add the constant near `KIND`:

```python
STREAMS = ("strategy", "research")
DEFAULT_STREAM = "strategy"      # rows written before streams existed
```

In `_row_to_idea`, add the field to the returned dict:

```python
            "score": float(d.get("score", 0.0) or 0.0),
            "stream": d.get("stream") or DEFAULT_STREAM}
```

Change `record`'s signature and the payload it writes:

```python
def record(journal, idea: dict, stream: str = DEFAULT_STREAM) -> bool:
```

```python
    journal.log_brain_event(KIND, iid, {
        "title": title,
        "text": text,
        "source": (idea.get("source") or "")[:200],
        "url": (idea.get("url") or idea.get("link") or "")[:400],
        "stream": stream if stream in STREAMS else DEFAULT_STREAM,
        "score": score(text, title)})
```

Add the filter to `pending` — signature and the one new condition:

```python
def pending(journal, max_age_days: float = MAX_AGE_DAYS,
            limit: int = POOL, stream: str | None = None) -> list[dict]:
```

```python
        if idea and len(idea["text"]) >= MIN_TEXT and idea["score"] > 0:
            if stream is not None and idea["stream"] != stream:
                continue
            out.append(idea)
```

And pass it through `next_idea`:

```python
def next_idea(journal, max_age_days: float = MAX_AGE_DAYS,
              stream: str | None = None) -> dict | None:
    q = pending(journal, max_age_days=max_age_days, limit=1, stream=stream)
    return q[0] if q else None
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_ideas_streams.py tests/test_idea_pipeline.py -q`
Expected: PASS, both files.

- [ ] **Step 5: Commit**

```bash
git add trader/brain/ideas.py tests/test_ideas_streams.py
git commit -m "feat(ideas): label queued items by stream — strategy or research"
```

---

### Task 2: A scorer for research prose

**Files:**
- Modify: `trader/brain/scraper.py`
- Test: `tests/test_scraper_research_stream.py`

**Interfaces:**
- Consumes: `ideas.STREAMS` from Task 1.
- Produces: `scraper.research_score(item) -> int` and `scraper.streams_for(item) -> set[str]`.

**Why this exists:** the current `idea_score` rewards trading jargon (`entry`, `stop-loss`, `backtest`) and is right for prebuilt strategies. Verified during design: a realistic research paragraph about funding and open interest scored **0** on it. Research prose asserts that an observable predicts something, with a scope and ideally a magnitude — different words entirely.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scraper_research_stream.py
"""Two consumers, two screens. A finding is not a setup."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.brain.scraper import idea_score, research_score, streams_for

SETUP = {"title": "Funding divergence setup",
         "text": "Mean-reversion entry on the reclaim, stop-loss below the "
                 "swept low, take-profit at the range midpoint."}

FINDING = {"title": "Short-horizon reversal in cross-sectional crypto returns",
           "text": "We document that coins in the top decile of 24-hour "
                   "relative return underperform the bottom decile by 41 bps "
                   "over the following 8 hours. The effect is significant "
                   "out-of-sample and decays with a half-life of roughly two "
                   "days, and it persists after controlling for volatility."}


def test_research_prose_scores_on_the_research_screen():
    assert research_score(FINDING) > 0


def test_the_strategy_screen_would_have_discarded_that_finding():
    # this is the bug the second scorer exists to fix
    assert idea_score(FINDING) < 1


def test_a_setup_routes_to_the_strategy_stream():
    assert "strategy" in streams_for(SETUP)


def test_a_finding_routes_to_the_research_stream():
    assert "research" in streams_for(FINDING)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_scraper_research_stream.py -q`
Expected: FAIL — `ImportError: cannot import name 'research_score'`.

- [ ] **Step 3: Implement**

In `trader/brain/scraper.py`, below the existing `HYPE` regex:

```python
#: research prose asserts that an observable predicts something, in a scope,
#: ideally with a magnitude. It rarely contains a single word from
#: STRATEGY_TERMS, which is why a second screen is needed rather than a
#: looser one.
CLAIM_TERMS = re.compile(
    r"\b(predict\w*|forecast\w*|correlat\w+|regress\w+|significan\w+"
    r"|hypothes\w+|evidence|out[- ]of[- ]sample|in[- ]sample|p[- ]value"
    r"|t[- ]stat\w*|sharpe|information ratio|anomal\w+|risk premium|factor"
    r"|cross[- ]section\w*|autocorrelat\w+|persist\w+|decay|half[- ]life"
    r"|microstructure|adverse selection|inventory|order flow|toxicity"
    r"|decile|quantile|percentile|basis points?|bps)\b", re.I)
#: a claim with a number attached is worth more than one without
MAGNITUDE = re.compile(r"\b\d+(\.\d+)?\s?(%|bps|basis points|x)\b", re.I)
#: does it say WHERE the claim holds?
SCOPE_TERMS = re.compile(
    r"\b(regime|horizon|timeframe|intraday|daily|weekly|hours?|days?"
    r"|bull|bear|trending|ranging|volatil\w+)\b", re.I)


def research_score(item: dict) -> int:
    """Screen for a market CLAIM rather than a trade setup."""
    hay = f"{item.get('title', '')} {item.get('text', '')[:1200]}"
    score = 2 * len(CLAIM_TERMS.findall(hay))
    if MAGNITUDE.search(hay):
        score += 4
    if SCOPE_TERMS.search(hay):
        score += 2
    if HYPE.search(hay):
        score -= 8
    return score


def streams_for(item: dict) -> set[str]:
    """Which consumers should see this. An item may serve both."""
    out = set()
    if idea_score(item) >= 1:
        out.add("strategy")
    if research_score(item) >= 4:
        out.add("research")
    return out
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_scraper_research_stream.py -q`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add trader/brain/scraper.py tests/test_scraper_research_stream.py
git commit -m "feat(scraper): screen research prose separately from trade setups"
```

---

### Task 3: Point the Scraper at TradingView Scripts

**Files:**
- Modify: `trader/brain/scraper.py`, `config.yaml`
- Test: `tests/test_scraper_sources.py` (add), `tests/test_scraper_queues_ideas.py` (update the stub name)

**Interfaces:**
- Consumes: `streams_for` from Task 2.
- Produces: `Scraper.scrape_tv_scripts(tag) -> list[dict]`. Replaces `scrape_tv_ideas`.

**Verified during design, do not re-derive:** `https://www.tradingview.com/scripts/{tag}/` returns HTTP 200 and the existing regex matches 24 cards with descriptions of min/median/max length **1,537 / 5,708 / 26,910 characters**. `/ideas/` returns chart commentary. Both fetched live on 2026-09-01.

**This supersedes one line in the spec.** The spec says "fetch the page body, not just the listing card". Measurement shows the listing card *already carries the full description* — TradingView embeds it in the listing JSON. No separate per-script fetch is needed, and building one would be wasted work plus 24 extra requests per tag. What was actually losing the text is the `[:600]` truncation in `scrape_tv_ideas`, which discarded ~90% of a median description. Widening that truncation is the whole fix.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_scraper_sources.py
def test_scrape_tv_scripts_requests_the_scripts_section(monkeypatch):
    """/ideas/ is people predicting; /scripts/ is the Pine library."""
    from trader.brain import scraper as S

    seen = []

    class _R:
        status_code = 200
        text = ""

    def _get(url, **kw):
        seen.append(url)
        return _R()

    monkeypatch.setattr(S.requests, "get", _get)
    s = _scraper_with(None)         # existing helper in this file, takes an llm
    s.tv_pages = 1
    s.scrape_tv_scripts("meanreversion")

    assert seen, "no request was made"
    assert "/scripts/" in seen[0], seen[0]
    assert "/ideas/" not in seen[0], seen[0]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_scraper_sources.py -q -k scripts_section`
Expected: FAIL — `AttributeError: 'Scraper' object has no attribute 'scrape_tv_scripts'`.

- [ ] **Step 3: Implement**

Rename the method and change the URL. In `trader/brain/scraper.py` replace the `def scrape_tv_ideas(self, tag: str)` line and its URL construction:

```python
    def scrape_tv_scripts(self, tag: str) -> list[dict]:
        """TradingView's public Pine library — published strategies.

        NOT /ideas/, which is chart commentary: 452 of 452 items queued from
        there were bare headlines with no mechanism in them.
        """
        out: list[dict] = []
        seen: set[str] = set()
        for page in range(1, max(1, self.tv_pages) + 1):
            suffix = f"?page={page}" if page > 1 else ""
            try:
                r = requests.get(
                    f"https://www.tradingview.com/scripts/{tag}/{suffix}",
                    headers=UA, timeout=12)
```

Leave the rest of the method body unchanged, but update the `source` field it writes:

```python
                page_items.append({
                    "source": f"tradingview.com/scripts/{tag}",
                    "idea_id": idea_id,
                    "title": name.strip(),
                    "text": desc.strip()[:4000]})
```

Note the `[:4000]` — it was `[:600]`, which truncated away most of a 3,888-character median description.

Update the caller inside `harvest_once`:

```python
        for tag in self.tags:
            got = self.scrape_tv_scripts(tag)
            scraped += got
```

In `config.yaml`, replace the `scraper.tv_tags` list:

```yaml
  tv_tags: ["meanreversion", "momentum", "volatility", "trendanalysis",
            "bitcoin", "ethereum"]
```

In `tests/test_scraper_queues_ideas.py`, update the stub:

```python
    s.scrape_tv_scripts = lambda tag: []
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_scraper_sources.py tests/test_scraper_queues_ideas.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/brain/scraper.py config.yaml tests/
git commit -m "feat(scraper): read the Pine script library, not chart commentary"
```

---

### Task 4: Route both streams into the queue

**Files:**
- Modify: `trader/brain/scraper.py`
- Test: `tests/test_scraper_queues_ideas.py`

**Interfaces:**
- Consumes: `ideas.record(..., stream=)` (Task 1), `streams_for` (Task 2), `scrape_tv_scripts` (Task 3).
- Produces: `harvest_once()` returns stats including `queued_strategy` and `queued_research`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_scraper_queues_ideas.py
def test_a_finding_reaches_the_research_stream(tmp_path):
    finding = {"idea_id": "arxiv_1",
               "title": "Short-horizon reversal in cross-sectional returns",
               "text": "Coins in the top decile of 24-hour relative return "
                       "underperform the bottom decile by 41 bps over the "
                       "following 8 hours. Significant out-of-sample, decays "
                       "with a half-life near two days, persists after "
                       "controlling for volatility regime.",
               "source": "arxiv.org/q-fin", "url": "https://arxiv.org/abs/1"}
    j, s = _scraper(tmp_path, [finding])

    stats = s.harvest_once()

    assert stats["queued_research"] == 1
    assert {i["idea_id"] for i in idea_queue.pending(j, stream="research")} \
        == {"arxiv_1"}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_scraper_queues_ideas.py -q -k research_stream`
Expected: FAIL — `KeyError: 'queued_research'`.

- [ ] **Step 3: Implement**

In `harvest_once`, replace the screen so items are kept if they score on *either* screen, and record per stream. Replace:

```python
        candidates = [i for i in fresh if idea_score(i) >= 1]
```

with:

```python
        candidates = [i for i in fresh if streams_for(i)]
```

Add the two counters to the `stats` dict at the top of the method:

```python
        stats = {"scraped": 0, "new": 0, "screened": 0, "queued_strategy": 0,
                 "queued_research": 0, "skipped": 0}
```

Replace the recording loop body:

```python
        for idea in batch:
            per_source.setdefault(idea["source"], {"scraped": 0, "queued": 0})
            per_source[idea["source"]]["scraped"] += 1
            for stream in sorted(streams_for(idea)):
                if ideas.record(self.journal, idea, stream=stream):
                    stats[f"queued_{stream}"] += 1
                    per_source[idea["source"]]["queued"] += 1
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_scraper_queues_ideas.py -q`
Expected: PASS, both tests.

- [ ] **Step 5: Commit**

```bash
git add trader/brain/scraper.py tests/test_scraper_queues_ideas.py
git commit -m "feat(scraper): route scraped items to the strategy and research streams"
```

---

### Task 5: Delete Job B

**Files:**
- Modify: `trader/brain/scraper.py`, `trader/brain/crawler.py`
- Delete: `scripts/analyst_gauntlet.py`
- Test: `tests/test_scraper_sources.py` (remove 6 tests)

**Interfaces:**
- Produces: nothing. This removes `extract_batch`, `_genome_from`, `_dual_gauntlet`, `_deploy` and the `FAMILY_DOCS` / `FAMILY_GENE_SPECS` imports from the Scraper.

**Why:** these turn a scraped item into a legacy genome and write it straight into the population, bypassing the Strategist and the Analyst. It has deployed 4 strategies (`harvest_accepted` ×4), two of which are still in the book. `spec_writer.py`'s own docstring argues against it: *"instructed it to map it to the NEAREST family and fill unspecified genes with schema midpoints, which is why 409 scraped ideas produced zero strategies."*

**Call sites, all of them** (verified by grep on 2026-09-01):
- `scraper.py:520` — `harvest_once` (the batch loop after queueing)
- `crawler.py:280` — `self.scraper._genome_from`
- `crawler.py:380,382` — `self.scraper._dual_gauntlet`, `self.scraper._deploy`
- `tests/test_scraper_sources.py:75,90,105,117,124,128`
- `scripts/analyst_gauntlet.py:62,78`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_scraper_sources.py
def test_the_scraper_no_longer_creates_strategies():
    """One creation path. The Scraper queues; the Strategist writes."""
    from trader.brain import scraper as S

    for gone in ("extract_batch", "_genome_from", "_dual_gauntlet", "_deploy"):
        assert not hasattr(S.Scraper, gone), (
            f"Scraper.{gone} still exists — that is the second, "
            f"population-writing creation path")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_scraper_sources.py -q -k no_longer_creates`
Expected: FAIL — `Scraper.extract_batch still exists`.

- [ ] **Step 3: Implement**

1. In `scraper.py`, delete the methods `extract_batch`, `_genome_from`, `_dual_gauntlet`, `_deploy`, the `FAMILY_DOCS` dict, and these imports:

```python
from ..strategy.genome import FAMILY_GENE_SPECS, Genome
```

2. In `harvest_once`, delete everything from `genomes = self.extract_batch(batch, tv_context)` down to the `harvest_rejected` logging block, leaving `self._log_cycle(stats, per_source)` and the `return stats`.

3. In `crawler.py`, delete the `_genome_from` call at line 280 and the `_dual_gauntlet` / `_deploy` block at lines 380-382, leaving the `ideas.record(...)` call that queues the mined passage.

4. Delete the 6 Job B tests from `tests/test_scraper_sources.py` (lines 75, 90, 105, 117, 124, 128 and their enclosing test functions).

5. `git rm scripts/analyst_gauntlet.py` — it exists only to drive `_dual_gauntlet` / `_deploy` by hand.

- [ ] **Step 4: Run the full suite**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: PASS. Count will be below the 534 baseline because 6 Job B tests were deleted and 7 were added across Tasks 1–4.

- [ ] **Step 5: Commit**

```bash
git add -A trader/brain/scraper.py trader/brain/crawler.py tests/ scripts/
git commit -m "refactor(scraper): delete the second strategy-creation path"
```

---

### Task 6: Enforce the TradingView budget and set the cadence

**Files:**
- Modify: `config.yaml`
- Test: `tests/test_deep_token_cap.py` (add)

**Interfaces:**
- Consumes: nothing. `TVHarness` already reads all of these; only the values change.

**Why:** `budget_enabled: false` is described in `tv_harness.py:143` as *"development switch: unlimited tester runs until Luffy finalizes"*, so `daily_runs: 20` is currently decorative and the harness runs unlimited.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_deep_token_cap.py
def test_tradingview_budget_is_actually_enforced():
    """daily_runs is decorative while budget_enabled is false."""
    import yaml
    from pathlib import Path

    cfg = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config.yaml").read_text())
    tv = cfg["tv_harness"]

    assert tv["budget_enabled"] is True, "the cap is ignored while this is off"
    assert tv["daily_runs"] <= 5, tv["daily_runs"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_deep_token_cap.py -q -k tradingview_budget`
Expected: FAIL — `assert False is True`.

- [ ] **Step 3: Implement**

In `config.yaml`:

```yaml
tv_harness:
  enabled: true
  daily_runs: 2
  budget_enabled: true
  run_timeout_s: 90
```

```yaml
mechanism:
  enabled: true
  interval_minutes: 720
  specs_per_cycle: 1
  max_specs: 8
```

```yaml
  ideas_per_cycle: 4
```

- [ ] **Step 4: Run the full suite**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config.yaml tests/test_deep_token_cap.py
git commit -m "chore(config): enforce the TV budget, 2 runs/day, 2 candidates/day"
```

---

## Done when

- The Scraper fetches from `tradingview.com/scripts/`, stores up to 4,000 characters of description, and queues items on labelled streams.
- `ideas.pending(j, stream="research")` returns research prose; `stream="strategy"` returns setups.
- `Scraper` has no `extract_batch`, `_genome_from`, `_dual_gauntlet` or `_deploy`, and `crawler.py` no longer calls them.
- `tv_harness.budget_enabled` is true with `daily_runs: 2`.
- Full suite green.

## Deliberately NOT in this plan

- Fetching Pine **source** via Playwright — depends on the budget landing first; own plan.
- Adding further scrape sources — worth doing, but after the pipeline is proven end to end.
- The Researcher that consumes the research stream — Plan 5.
