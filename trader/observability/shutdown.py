"""Shutdown evidence for children owned by an observation controller.

Identity mismatch, a missing /proc record, and SIGTERM delivery are not exit
confirmation. Only the owned Popen's waitpid-backed poll permits cleanup.
This module neither launches nor signals services or changes safety policy.
"""
from pathlib import Path


def service_state(process, identity):
    """Return TERMINATED, LIVE, or UNKNOWN without conflating identity and death."""
    try:
        if process.pid != identity['pid']:
            return 'UNKNOWN'
        if process.poll() is not None:
            return 'TERMINATED'
        fields = Path('/proc', str(process.pid), 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[19] == str(identity['start_ticks']) and fields[0] not in ('Z', 'X'):
            return 'LIVE'
    except (OSError, ValueError, KeyError, IndexError):
        pass
    return 'UNKNOWN'
