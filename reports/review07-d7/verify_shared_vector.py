"""Verify the EXACT proposed shared-file patch in memory, without editing D6 files.

This is integration-candidate evidence, not an applied shared-source change.
"""
import difflib
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import pytest
from trader.strategy import vector_backtest as vb
p=ROOT/'trader/strategy/vector_backtest.py'
old=p.read_text()
new=old.replace('highs[j], lows[j], closes[j]), intended_quantity)',
    '''highs[j], lows[j], closes[j],
                completed=bool(row.get('observation_quality' if df.attrs.get('read_mode') == 'retrospective' else 'quality', 'VALID') == 'VALID'
                               and row.get('bar_state', 'FINAL') == 'FINAL'),
                atr=float(atr[j]),
                signal_exit=(bool(exit_sig[j]) if exit_sig is not None and exit_sig_available is not None
                             and exit_sig_available[j] else None)), intended_quantity)''')
needle='    atr = atr_series(df, 14).to_numpy(float)'
assert old.count(needle)==2
new=new.replace(needle,needle+'''
    if (getattr(exit_spec, "_exit_semantics_id", None) is not None
            and exit_spec.trail.get("kind") == "atr"):
        from .exit_policy import trail_atr_series
        from ..core.types import TF_MS
        atr = trail_atr_series(df, TF_MS[exit_spec._exit_timeframe]).to_numpy(float)''')
# Semantic plumbing only. D6 must replace the explicit signal-fill barrier
# with its legitimate executable-price contract before signal studies can score.
new=new.replace('transitions_out=None):', 'transitions_out=None, exit_sig_available=None):')
new=new.replace('first_bar: int = 0) -> dict:', 'first_bar: int = 0, exit_sig=None, exit_sig_available=None) -> dict:')
new=new.replace('score_from: int = 0, exit_evidence_out=None)',
                'score_from: int = 0, exit_evidence_out=None, exit_sig_available=None)')
new=new.replace('arm_at_r, None, fund_arr,', 'arm_at_r, exit_sig, fund_arr,')
new=new.replace('funding_8h, bar_minutes)', 'funding_8h, bar_minutes, exit_sig_available=exit_sig_available)')
new=new.replace('funding_8h, bar_minutes, trace)', 'funding_8h, bar_minutes, trace, exit_sig_available=exit_sig_available)')
new=new.replace('            if transition.due:\n                exit_i, exit_px',
    '            if transition.due:\n                if transition.reason == "signal":\n                    raise ValueError("signal_exit_execution_timing_requires_D6")\n                exit_i, exit_px')
new=new.replace('ex = compiled.exit_signal(', 'ex, ex_available = compiled.exit_signal_detail(')
new=new.replace('exit_sig=ex, funding=fund)', 'exit_sig=ex, funding=fund, exit_sig_available=ex_available)')
new=new.replace('exit_sig=None if ex is None else ex[sl],',
    'exit_sig=None if ex is None else ex[sl],\n                        exit_sig_available=None if ex_available is None else ex_available[sl],')
patch=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),
    fromfile='a/trader/strategy/vector_backtest.py',tofile='b/trader/strategy/vector_backtest.py'))
assert patch == (ROOT/'reports/review07-d7/PENDING-D6-vector-ATR-observations.patch').read_text()
exec(compile(new,str(p),'exec'),vb.__dict__)
raise SystemExit(pytest.main(['-q',str(ROOT/'reports/review07-d7/test_vector_handoff.py'),
    'tests/test_vector_backtest.py','tests/test_research_portfolio_null.py','tests/test_null_cost_symmetry.py']))
