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
