"""Watchdog — heartbeat pattern salvaged from the old project.

The kernel writes a heartbeat every cycle; a monitor thread alerts and can
trigger self-restart if the loop stalls silently (no exception raised).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from ..core.config import ROOT

log = logging.getLogger(__name__)


class Heartbeat:
    def __init__(self, name: str = "luffy"):
        self.path = ROOT / "data" / f"heartbeat_{name}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def beat(self, extra: dict | None = None) -> None:
        payload = {"timestamp": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   **(extra or {})}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, self.path)          # atomic

    def age_seconds(self) -> float | None:
        try:
            return time.time() - json.loads(self.path.read_text())["timestamp"]
        except Exception:
            return None


def start_stall_monitor(heartbeat: Heartbeat, stale_after: float = 240.0,
                        on_stall=None) -> threading.Thread:
    """Daemon thread; default action: log CRITICAL (restart strategy is the
    launcher's job — external supervisor re-execs on exit or heartbeat age)."""

    def _loop():
        while True:
            time.sleep(30)
            age = heartbeat.age_seconds()
            if age is not None and age > stale_after:
                log.critical(f"HEARTBEAT STALE {age:.0f}s (> {stale_after}s)")
                if on_stall:
                    try:
                        on_stall(age)
                    except Exception as e:
                        log.error(f"stall handler failed: {e}")

    t = threading.Thread(target=_loop, name="stall-monitor", daemon=True)
    t.start()
    return t
