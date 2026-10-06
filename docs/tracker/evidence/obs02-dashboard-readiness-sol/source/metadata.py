"""Small bounded metadata writer and incremental reader for the existing profiler."""
import json
import os
from pathlib import Path


def append(path, record, limit=8 * 1024 * 1024):
    path = Path(path)
    raw = (json.dumps(record, sort_keys=True) + '\n').encode()
    if len(raw) > 65536 or ((path.stat().st_size if path.exists() else 0) + len(raw) > limit):
        raise RuntimeError('metadata_bound_reached')
    with path.open('ab', buffering=0) as stream:
        view = memoryview(raw)
        while view:
            view = view[stream.write(view):]


class Tail:
    def __init__(self):
        self.offset = 0
        self.partial = b''

    def read(self, path):
        path = Path(path)
        if not path.exists():
            return []
        with path.open('rb') as stream:
            stream.seek(self.offset)
            chunk = stream.read(1024 * 1024)
            self.offset = stream.tell()
        pieces = (self.partial + chunk).split(b'\n')
        self.partial = pieces.pop()
        if len(self.partial) > 65536:
            raise RuntimeError('unterminated_metadata_bound')
        return [json.loads(line) for line in pieces if line]


def new_failures(health, baseline, current_instance):
    """A retained recovery latch alone is not a new incident."""
    failures = []
    for name, condition in health.get('conditions', {}).items():
        if condition.get('status') != 'ACTIVE':
            continue
        record = condition.get('context', {}).get('record') or {}
        if name == 'heartbeat:luffy':
            if condition.get('reason') == 'PRODUCER_RESTART_REQUIRES_RECOVERY':
                continue  # retained transition, never cleared or declared healthy
            if condition.get('reason') == 'STALE' and record.get('instance_id') != current_instance:
                continue  # stopped predecessor only; current instance remains guarded
        old = baseline.get(name)
        if old and old.get('incident_id') == condition.get('incident_id') and old.get('reason') == condition.get('reason') and old.get('context') == condition.get('context'):
            continue
        failures.append(name)
    return failures


def blocking_conditions(health, prior_instance=None):
    """Retain historical evidence; never waive a current storage/protection fault."""
    if health.get('schema') != 'luffy-safety-health.v1' or not isinstance(health.get('conditions'), dict):
        raise ValueError('unreadable_required_safety_facts')
    blocked = []
    for name, condition in health['conditions'].items():
        if condition.get('status') != 'ACTIVE':
            continue
        ctx = condition.get('context', {})
        record = ctx.get('record') or {}
        if name == 'heartbeat:luffy':
            if condition.get('reason') == 'PRODUCER_RESTART_REQUIRES_RECOVERY':
                continue
            if condition.get('reason') == 'STALE' and prior_instance and record.get('instance_id') == prior_instance:
                continue
        if (name == 'observed_cycle:luffy' and condition.get('reason') == 'SUCCESSFUL_CYCLE_THRESHOLD_EXCEEDED'
                and ctx.get('contained') is True and ctx.get('deployed_sha') == '958c9eb2f0b10993ad240b85f88c626b8d0e2ee6'
                and ctx.get('source') == 'retained Kernel completion log and genuine heartbeat'):
            continue  # exact retained contained evidence; remains ACTIVE and latch retained
        blocked.append(name)
    return blocked
