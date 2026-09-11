"""The LLM Judge is gone.

56 of its 58 reviews applied nothing; its promote/demote writes went through
Journal.query(), which does not commit; and `active` changes no sizing —
proving size counts the ACCOUNT's closed trades (risk.py:174). /judge now
reports book health and rent from data.
"""
import importlib
import inspect

import pytest


def test_the_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("trader.brain.judge")


def test_the_kernel_starts_no_brain_judge_thread():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert "_brain_judge_loop" not in src
    assert "brain.judge" not in src


def test_slash_judge_answers_from_the_post_mortem():
    from trader import kernel as K
    assert "summary_text" in inspect.getsource(K)


def test_the_manager_no_longer_wraps_the_judge():
    from trader.org import Org
    assert "trader.brain.judge" not in Org.load().manager.wraps


def test_config_carries_no_judge_cadence():
    from trader.core.config import load_config
    b = load_config()["brain"]
    assert "judge_interval_minutes" not in b
    assert "judge_meta_every_hours" not in b
