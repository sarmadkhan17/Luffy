# Repository guidance

Read [CLAUDE.md](CLAUDE.md) before working in this repository and follow its
project guidance. Keep shared instructions there rather than duplicating them
in this file. Explicit instructions from the current user session take
precedence.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

For conceptual trading-memory questions, if memory_lookup resolves the concept, answer from that exact memory only. Do not expand into strategy performance, backtests, source files, research history, or Graphify neighbors unless the user explicitly asks for broader context, validation, or implementation details.

For questions like "which mechanisms work in X regime?" or "what fails in X?", use reverse memory lookup:

./venv/bin/python scripts/memory_lookup.py --reverse <relation> "<target>"

Examples:
./venv/bin/python scripts/memory_lookup.py --reverse works_in "Ranging Regime"
./venv/bin/python scripts/memory_lookup.py --reverse fails_in "Strong Trend Expansion"

Answer only from the returned concepts unless broader context is explicitly requested.

## Graphify cost control

Use Graphify as a code-only engineering graph for normal repository work.

- Prefer `graphify explain`, `graphify query`, and `graphify update .` against the existing code-only graph.
- Do not run full semantic extraction of the repository or knowledge vault unless the user explicitly requests it.
- Trading knowledge should come from `scripts/memory_lookup.py` and structured Obsidian YAML, not semantic Graphify extraction.
