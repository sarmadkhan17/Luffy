"""installation/observe.sh: the cycle lock must not leak into children, and every child is bounded."""
import fcntl
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'installation' / 'observe.sh'

STUB = """#!/usr/bin/env bash
if [ -e /proc/$$/fd/9 ]; then echo "FD9_INHERITED $*"; else echo "FD9_CLOSED $*"; fi
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'installation').mkdir()
    shutil.copy(SCRIPT, tmp_path / 'installation' / 'observe.sh')
    (tmp_path / 'venv' / 'bin').mkdir(parents=True)
    stub = tmp_path / 'venv' / 'bin' / 'python'
    stub.write_text(STUB)
    stub.chmod(0o755)
    (tmp_path / 'data').mkdir()
    for flag in ('declared_population', 'investigation', 'accounting'):
        (tmp_path / 'data' / f'{flag}.enabled').touch()
    return tmp_path


def _run(root):
    return subprocess.run(['bash', str(root / 'installation' / 'observe.sh')], capture_output=True, text=True, timeout=60)


def test_children_do_not_inherit_the_cycle_lock_descriptor(root):
    out = _run(root)
    assert out.returncode == 0, out.stderr
    assert 'FD9_INHERITED' not in out.stdout
    assert out.stdout.count('FD9_CLOSED') == 4                 # declared, learning, investigation, accounting all ran
    assert out.stdout.count('observer_exit=0') == 4


def test_every_timeout_has_a_kill_after():
    text = SCRIPT.read_text()
    timeouts = re.findall(r'^\s*run .*?\btimeout\b.*$', text, re.M)
    assert len(timeouts) == 4
    for line in timeouts:
        assert re.search(r'timeout --kill-after=\d+s \d+s', line), line
    for module in ('learning', 'investigation'):
        line = next(line for line in timeouts if f'trader.observability.{module} ' in line)
        assert '--kill-after=5s' in line
    # worst case must still fit one 5-minute cron period
    total = sum(int(k) + int(t) for k, t in re.findall(r'--kill-after=(\d+)s (\d+)s', text))
    assert total < 300, total


def test_a_busy_cycle_is_logged_not_silent(root):
    lock = root / 'data' / 'observation-cycle.lock'
    with open(lock, 'w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        out = _run(root)
    assert out.returncode == 0
    assert 'observer_cycle skipped=locked' in out.stdout
    assert 'FD9' not in out.stdout                              # no observer ran


def test_observers_off_still_wins(root):
    (root / 'data' / 'observers.off').touch()
    out = _run(root)
    assert out.returncode == 0 and out.stdout == ''
