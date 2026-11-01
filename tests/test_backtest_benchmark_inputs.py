"""An empty seed directory must not count as a zero-cost vector benchmark."""
from pathlib import Path
import runpy
from unittest.mock import Mock

import pytest


def test_incomplete_specs_refuse_before_timing_either_engine(monkeypatch):
    from trader.core import config
    from trader.data import feed
    from trader.strategy import backtest, evidence, library, seed_specs
    monkeypatch.setattr(config, 'load_config', lambda: {'risk': {}})
    monkeypatch.setattr(feed, 'DataFeed', lambda: object())
    monkeypatch.setattr(evidence, 'load_frames', lambda *_: {'SYNTH': [1, 2]})
    monkeypatch.setattr(library, 'build_seed_population', lambda: [('seed', object())])
    monkeypatch.setattr(seed_specs, 'load_seed_specs', lambda: [])
    legacy = Mock(side_effect=AssertionError('must not run'))
    monkeypatch.setattr(backtest, 'backtest', legacy)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(Path(__file__).parents[1] / 'scripts/bench_vector_backtest.py'),
                       run_name='__main__')
    assert exc.value.code == 1
    legacy.assert_not_called()
