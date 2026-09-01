"""Executable doctrine — beliefs that carry machine-readable rules.

The doctrine used to be a diary: the theorist wrote prose, nothing read
it. Now selected beliefs map to TUNABLE RULES within hard bounds. The
theorist (LLM autopsy) may set them; the orchestrator/risk consume them
at hot-reload cadence. Each rule declares its bounds so a hallucinating
LLM cannot set a insane value — validation clamps and rejects.

Rules live in data/doctrine.json under "rules":
  {id: {"value": x, "evidence": "...", "updated": iso}}

Whitelist (id → bounds → consumer):
  threshold_mult_RANGING / _TRENDING_UP / _TRENDING_DOWN / _VOLATILE
      0.70..1.40   multiplies the adaptive base threshold for that regime
  cooldown_minutes       15..180   like-for-like signal cooldown
  strategy_conf_floor     0.30..0.70   strategy signals below this don't vote
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from ..core.config import ROOT

log = logging.getLogger(__name__)

BOUNDS: dict[str, tuple[float, float]] = {
    "threshold_mult_RANGING": (0.70, 1.40),
    "threshold_mult_TRENDING_UP": (0.70, 1.40),
    "threshold_mult_TRENDING_DOWN": (0.70, 1.40),
    "threshold_mult_VOLATILE": (0.70, 1.40),
    "cooldown_minutes": (15.0, 180.0),
    "strategy_conf_floor": (0.30, 0.70),
}

_cache: dict = {"ts": 0.0, "rules": {}}


def load_rules() -> dict[str, float]:
    """Current validated rule values (bounded), 5-min cached."""
    now = time.time()
    if now - _cache["ts"] > 300:
        rules: dict[str, float] = {}
        try:
            p = ROOT / "data" / "doctrine.json"
            if p.exists():
                d = json.loads(p.read_text())
                for k, v in (d.get("rules") or {}).items():
                    if k in BOUNDS:
                        try:
                            val = float(v.get("value"))
                        except (TypeError, ValueError):
                            continue
                        lo, hi = BOUNDS[k]
                        rules[k] = min(hi, max(lo, val))
        except Exception as e:
            log.debug(f"rules load failed: {e}")
        _cache.update(ts=now, rules=rules)
    return _cache["rules"]


def apply_updates(doctrine: dict, updates: list[dict]) -> list[str]:
    """Validate + persist LLM-proposed rule edits. Returns applied ids.

    Unknown ids, out-of-bounds values, or edits without evidence are
    rejected (never clamped silently — the theorist must re-propose)."""
    rules = doctrine.setdefault("rules", {})
    applied = []
    for u in updates or []:
        rid = (u.get("id") or "").strip()
        if rid not in BOUNDS:
            continue
        try:
            val = float(u.get("value"))
        except (TypeError, ValueError):
            continue
        lo, hi = BOUNDS[rid]
        if not (lo <= val <= hi):
            continue
        ev = (u.get("evidence") or "").strip()
        if not ev:
            continue
        rules[rid] = {"value": val, "evidence": ev[:400],
                      "updated": datetime.now(timezone.utc).isoformat()}
        applied.append(rid)
    return applied


def whitelist_doc() -> str:
    return json.dumps({k: f"{lo}..{hi}" for k, (lo, hi) in BOUNDS.items()})
