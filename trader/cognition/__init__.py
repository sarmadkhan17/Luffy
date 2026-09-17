"""Offline cognition: observations -> attention -> market state -> competing
hypotheses -> resolved outcomes, replayed point-in-time from an explicit file.

Research tooling only. Nothing here places orders, admits strategies, reads
the live database, knowledge vault or config, or calls a network or an LLM.
Imports are stdlib plus this package. See
docs/superpowers/specs/2026-09-15-cognition-offline.md.
"""
SCHEMA_VERSION = "cognition.v1"
