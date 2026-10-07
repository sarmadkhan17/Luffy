"""Reproduce the existing static guard using unchanged HEAD writer bytes.

This is an exact-source baseline check, not a full clean-checkout suite.
"""
import pathlib
import sys

sys.path.insert(0, '/mnt/luffy-data/luffy/production')
from tests.test_strategy_factory_handoff import (
    test_factory_writes_no_trading_state_and_has_no_trading_caller as _original)


def test_unchanged_head_static_guard(monkeypatch):
    writer = pathlib.Path('/mnt/luffy-data/luffy/production/trader/strategy/factory_handoff.py')
    baseline = pathlib.Path('/mnt/luffy-data/test-tmp/str04-baseline-factory.py').read_text()
    read = pathlib.Path.read_text
    def head_read(path, *args, **kwargs):
        if path.resolve() == writer:
            return baseline
        return read(path, *args, **kwargs)
    monkeypatch.setattr(pathlib.Path, 'read_text', head_read)
    _original()
