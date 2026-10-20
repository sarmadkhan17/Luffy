# Repository guidance

Read [CLAUDE.md](CLAUDE.md) before working in this repository and follow its
project guidance. Keep shared instructions there rather than duplicating them
in this file. Explicit instructions from the current user session take
precedence.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- Graphs live outside the repository (`~/.local/state/luffy-graphs/<commit>/`), one per exact
  commit. For caller/relationship questions use `luffy-graphify explain "<symbol>()"`,
  `luffy-graphify path --undirected "<A>" "<B>"` or `luffy-graphify query "<question>"`.
  A `STALE GRAPH` banner means the graph was built for another commit: treat the result as a
  lead, never as evidence. `luffy-graphify list` shows the available commits.
- Graph edges are incomplete. A missing caller is not proof of dead code; confirm with
  `luffy-callers` and `rg` (see the `luffy-review` skill).
- Do not run `graphify update .` in a working repository and do not rely on or regenerate the
  tracked `graphify-out/`; it is a historical snapshot and updating it dirties tracked files.
  Refresh with `luffy-graphify refresh` only from a clean checkout of the commit to index.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.

For conceptual trading-memory questions, if memory_lookup resolves the concept, answer from that exact memory only. Do not expand into strategy performance, backtests, source files, research history, or Graphify neighbors unless the user explicitly asks for broader context, validation, or implementation details.

For questions like "which mechanisms work in X regime?" or "what fails in X?", use reverse memory lookup:

./venv/bin/python scripts/memory_lookup.py --reverse <relation> "<target>"

Examples:
./venv/bin/python scripts/memory_lookup.py --reverse works_in "Ranging Regime"
./venv/bin/python scripts/memory_lookup.py --reverse fails_in "Strong Trend Expansion"

Answer only from the returned concepts unless broader context is explicitly requested.

## Graphify cost control

Use Graphify as a code-only engineering graph for normal repository work.

- Prefer `luffy-graphify explain`, `path` and `query` against the external code-only graph.
- Do not run full semantic extraction of the repository or knowledge vault unless the user explicitly requests it.
- Trading knowledge should come from `scripts/memory_lookup.py` and structured Obsidian YAML, not semantic Graphify extraction.
