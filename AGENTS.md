# Repository guidance

Read [CLAUDE.md](CLAUDE.md) before working in this repository and follow its
project guidance. Keep shared instructions there rather than duplicating them
in this file. Explicit instructions from the current user session take
precedence.

## graphify

When the user types `/graphify`, use the installed graphify skill (`.claude/skills/graphify`)
before doing anything else.

Rules:
- Use the plain `graphify` CLI (`graphify query "<question>"`, `graphify explain "<symbol>"`,
  `graphify path "<A>" "<B>"`) only if a graph for the current checkout is available. Generated
  `graphify-out*/` directories are untracked; a graph that does not match the checked-out
  commit is a lead, never evidence.
- Graph edges are incomplete. A missing caller is not proof of dead code; confirm with `rg`.
- Do not run `graphify update .` in a working repository.

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
