"""Negative controls for the reconciled historical authority guard."""
import subprocess
from pathlib import Path

import pytest

from tests.authority_history_guard import verify

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name,old,new', [
    ('risk.py', 'self.cfg = cfg', 'self.cfg = {}'),
    ('executor.py', 'cfg=self.cfg', 'cfg={}'),
    ('executor.py', 'positive(amount) > requested_amount', 'False'),
    ('executor.py', 'from .entry_authority import positive, submit', 'from .entry_authority import positive'),
    ('risk_intent.py', 'import', '# removed import'),
])
def test_authority_guard_rejects_isolated_regressions(name, old, new):
    sources = {n: (ROOT / 'trader/engine' / n).read_text()
               for n in ('risk.py', 'risk_intent.py', 'executor.py')}
    assert old in sources[name]
    sources[name] = sources[name].replace(old, new, 1)
    def historical(commit, filename):
        return subprocess.check_output(['git', 'show', commit + ':trader/engine/' + filename],
                                       cwd=ROOT, text=True)
    with pytest.raises((AssertionError, SyntaxError)):
        verify(historical, sources.__getitem__)
