Knowledge review: 175/175 owned files fully read; 174 KEEP, 1 SIMPLIFY (Vault). No files removed/merged. Full per-file hashes, frontmatter, claim provenance, links and conclusions: knowledge-files.json. Original baseline is 4deca5c; source Markdown remains byte-identical.

The implemented narrative path is source knowledge/ -> first-bootstrap copy to data/knowledge/ -> hourly Kernel strategy/daily/ledger/MOC projection -> owner Knowledge tree/note/graph and human Obsidian readers. Company roster derives from org.yaml plus seven actual analyst subclasses; author/module lookups organize existing code only. Runtime-generated strategy notes describe compiled specs, historical genomes and closed journal records; they do not admit strategies. Six-hour arithmetic postmortem and manual dashboard autopsy append notes only on changed verdicts. Exact source-memory lookup excludes candidates and uses structured relations; Absorption/fails_in and reverse works_in/Ranging Regime both verified offline. See knowledge-capabilities.json for root-owned projection measurements.

The frozen doctrine path is separate: Kernel._strategist_knowledge loads data/doctrine.json plus Harvester's numeric coverage brief, then SpecWriter consumes that payload. No automatic distillation of vault prose or candidate claims into doctrine/research/decision authority was found. Canonical research/semantic memories and their established consumers are separate architecture (root verifies those consumers). The vault is connected to observation and owner evidence; the broader SDD20 semantic learning goals are not satisfied merely by Markdown links. Deferred automatic synthesis is not classified as a defect or implemented as new machinery. Source notes are copied only when runtime vault is absent; source edits are not a synchronization protocol.

Confirmed and corrected defects:

- K10-01: Journal.trades_for_strategy returns all states, while Vault counted all as 'closed trades'. Filter closed rows before wins/P&L. Frozen source strategy snapshots are untouched.
- K10-02: unused json.loads(stats_json) could abort all later strategy/daily/ledger/MOC refresh outputs on malformed historical cache. Remove that unused parse; calculate from trade evidence already consumed.
- K10-03: same-title incidents within one minute overwrote prior evidence, possible through manual/scheduled Book health callers. Exclusive creation preserves the first historical filename and adds deterministic collision suffixes. Existing note bytes and first filenames stay unchanged.
- K10-04: strategy/daily scalar summation treated retained UNKNOWN monetary evidence as booked numeric truth. Reuse existing economics.journal_pnl and booking_receipts classifier; UNKNOWN makes amount/wins unavailable; estimates are labelled DERIVED_ESTIMATE; recorded amounts are labelled JOURNAL_BOOKED, with funding/venue-net limits. A later numeric close cannot erase an earlier UNKNOWN partial receipt. Daily reads closed rows instead of COALESCE sum. No financial records, receipt hashes, accounting logic or risk authority changed.

The metadata review preserves 109 status=candidate atomic notes, including three explicit duplicate-review candidates, because their source identity/lineage is evidence. They are intentionally excluded from exact lookup. Source-role snapshots include historical Researcher, former Strategist responsibilities and a Stub Analyst test-double artifact; current Org excludes non-trader.agents classes. Those historical artifacts remain evidence, not a current roster. Candidate advice conflicts (positive sl_fill anomaly vs audit first; live/paper promotion thresholds; macro hard/soft gates), merged_from aliases and changing P&L populations must not become live policy. They were not rewritten, deduplicated by claim, or escalated into a new retrieval/authority mechanism. SDD20 requires retention with confidence/provenance; SDD25 treats knowledge evidence as traceable observations.

Costs: full-refresh scans strategy records plus trade histories, writes each strategy and daily/ledger/MOC; sibling family links can grow quadratically. Receipt-aware truth adds one uncapped receipt read per closed trade, intentionally reusing existing correctness behavior rather than claiming a speedup. Unknown receipt history must not be capped. Daily history and incidents are append/retention data; no age-based deletion contract exists. Org YAML parsing/imports are bounded roster metadata. Historical seed_vault.py has top-level journal/runtime writes and missing-doctrine seeding: preserved as an explicit historical utility, never executed in this review. seed_doctrine does not rewrite existing frozen doctrine; missing-file bootstrap policy remains historical/owner scope. No providers, production databases, process restart or trade activation used.

Review-only concerns requiring a separate established identity/retention policy before change: strategy filenames keyed by normalized names can collide when names coincide; title/slug path semantics are not an opaque evidence ID; source snapshot freshness/backlinks are not current-state proof. No destructive renaming, automatic deletion, policy change, or speculative integration introduced.

Focused verification:

`./venv/bin/python -m pytest -q tests/test_org.py tests/test_org_vault.py tests/test_company.py tests/test_postmortem.py tests/test_theorist_removed.py tests/test_acc03_economics.py tests/test_owner_reads.py tests/test_owner_reads_m2.py`

Result: 118 passed in 40.08s. Earlier focused roster/vault/company/postmortem/theorist run before financial coverage additions: 39 passed in 3.71s. New tests cover mixed open/closed/unresolved rows, malformed unused cached stats, three same-minute notes preserving original bytes, retained UNKNOWN partial evidence followed by numeric final close, and estimated/missing money. Temporary SQLite only. No full CI/integration.

Safe HEAD-source module loading into a temporary namespace reproduced failures without swapping shared source files:

```
HEAD mixed-state note: - closed trades: 2 · wins: 2
- realized P&L: +10.00 USDT
HEAD unused stats error: JSONDecodeError
HEAD repeated title note count: 1 first evidence retained: False
HEAD unknown money note: - realized P&L: +0.00 USDT
```

Fixture truth for mixed states: one closed +2 and one open +8; original generated +10 and 2 closed. Identical invocation twice erased first verdict. New tests enforce corrected truth. All source hashes pinned to initial HEAD; exact per-file evidence retains original metadata rather than modifying history. Consolidated commit is owned by root; no independent commits made.

Final independent-review follow-up: shared economics classifier now preserves the existing executor `booked_pnl_basis='estimated'` marker even when its acquisition basis is `venue_order_fills` (executor.py:639–642). Root authorized the dependency fix; chat/notify owner implemented it and supplies its failing-before proof. One additional Vault regression verifies both generated strategy and daily Markdown retain DERIVED_ESTIMATE from that actual retained receipt shape. `./venv/bin/python -m pytest -q tests/test_org_vault.py` — **12 passed in 1.28s**. Together with the preceding affected-consumer set this adds one unique test (119 unique knowledge verification cases; 118 prior +1 new), not twelve new unique cases. No duplicate financial classifier or receipt mutation introduced.
