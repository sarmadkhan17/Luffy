"""One atomic kernel heartbeat producer, observed independently by watchdog."""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path
from uuid import uuid4

from ..core.config import ROOT
from ..observability.safety import publish

log = logging.getLogger(__name__)
SCHEMA = 'luffy-heartbeat.v1'


def read_heartbeat(path, producer, now):
    value = json.loads(Path(path).read_text())
    if (not isinstance(value, dict) or not isinstance(producer, str) or not producer.strip()
            or value.get('schema') != SCHEMA or value.get('producer') != producer
            or not isinstance(value.get('instance_id'), str) or not value['instance_id'].strip()
            or type(value.get('sequence')) is not int or value['sequence'] < 1
            or type(value.get('timestamp')) not in (int, float)
            or not math.isfinite(value['timestamp']) or value['timestamp'] > now
            or type(value.get('started_at')) not in (int, float)
            or not math.isfinite(value['started_at']) or value['started_at'] > value['timestamp']
            or not isinstance(value.get('context'), dict)):
        raise ValueError('invalid_heartbeat')
    # The kernel publishes this field only after successful cycle bookkeeping.
    # Publication alone is not evidence that a safety-critical cycle completed.
    cycle = value['context'].get('last_successful_cycle_at')
    if (type(cycle) not in (int, float) or not math.isfinite(cycle)
            or not 0 <= value['started_at'] <= cycle <= value['timestamp'] <= now):
        raise ValueError('invalid_heartbeat_cycle_context')
    return value


class Heartbeat:
    def __init__(self, name='luffy', *, path=None, clock=time.time, instance_id=None):
        self.path = Path(path) if path is not None else ROOT / 'data' / f'heartbeat_{name}.json'
        self.producer = name
        self.clock = clock
        self.instance_id = instance_id or uuid4().hex
        self.started_at = clock()
        self.sequence = 0
        self._lock = threading.Lock()

    def beat(self, extra=None):
        with self._lock:
            next_sequence = self.sequence + 1
            payload = dict(schema=SCHEMA, producer=self.producer, instance_id=self.instance_id,
                           sequence=next_sequence, timestamp=self.clock(), started_at=self.started_at,
                           context=extra or {})
            publish(self.path, payload)
            self.sequence = next_sequence

    def age_seconds(self):
        try:
            now = self.clock()
            return now - read_heartbeat(self.path, self.producer, now)['timestamp']
        except Exception:
            return None


def start_stall_monitor(heartbeat, stale_after=None, on_stall=None):
    """Legacy diagnostic thread; external observer owns death detection.
    Caller supplies existing registered policy. No default safety threshold."""
    def loop():
        while True:
            time.sleep(30)  # existing diagnostic poll cadence
            age = heartbeat.age_seconds()
            if stale_after is not None and (age is None or age > stale_after):
                log.critical('HEARTBEAT UNAVAILABLE/STALE age=%s', age)
                if on_stall:
                    try:
                        on_stall(age)
                    except Exception:
                        log.exception('stall handler failed')
    t = threading.Thread(target=loop, name='stall-monitor', daemon=True)
    t.start()
    return t
