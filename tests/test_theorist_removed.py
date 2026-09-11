"""The LLM Theorist is gone; its job is the data-only post-mortem.

It spent ~55% of all tokens (645k of 1.17M since 2026-08-25) writing prose
doctrine, and `brain/rules.py` — the "executable doctrine" it could set —
had no reader anywhere in the code.
"""
import importlib
import inspect

import pytest


@pytest.mark.parametrize("mod", ["trader.brain.theorist", "trader.brain.rules"])
def test_the_module_is_gone(mod):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(mod)


def test_the_kernel_runs_the_data_postmortem():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert "brain.theorist" not in src
    assert "run_postmortem" in src
    assert "load_doctrine" in src


def test_the_dashboard_autopsy_button_runs_the_postmortem():
    from trader.dashboard import server
    src = inspect.getsource(server)
    assert "brain.theorist" not in src
    assert "run_postmortem" in src


def test_the_theorist_role_wraps_the_postmortem():
    from trader.org import Org
    t = next(e for e in Org.load().employees if e.name == "Theorist")
    assert "trader.brain.postmortem" in t.wraps
    assert "postmortem" in t.events
