"""Verify reconciled D6/D7 on the actual source; never patch modules in memory."""
from pathlib import Path
import sys
import pytest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
raise SystemExit(pytest.main(['-q', str(ROOT/'tests/test_d6_d7_vector_handoff.py'),
    str(ROOT/'tests/test_vector_backtest.py'), str(ROOT/'tests/test_research_portfolio_null.py'),
    str(ROOT/'tests/test_null_cost_symmetry.py')]))
