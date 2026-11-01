"""Isolated research and installed clients use one budget; no API calls."""
import json
import multiprocessing

import pytest

from trader.brain.llm import BrainLLM
from trader.core.config import load_config


def config(path):
    return {'brain': dict(model_fast='offline', model_deep='offline',
                         max_tokens_per_call=1000, daily_token_budget=1000,
                         purpose_budgets={'research': 600}, usage_ledger_path=str(path))}


def reserve_in_child(cfg, output):
    brain = BrainLLM(cfg)
    # Admission/reservation is exercised without sending anything to a provider.
    output.put(brain._admit({'prompt': 'offline'}, False, 'research'))


def test_spawned_research_reservation_blocks_other_checkout(tmp_path, monkeypatch):
    ledger = tmp_path / 'installed' / 'usage.json'
    cfg = config(ledger)
    monkeypatch.setattr('trader.brain.llm.ROOT', tmp_path / 'isolated-checkout')
    context = multiprocessing.get_context('spawn')
    output = context.Queue()
    process = context.Process(target=reserve_in_child, args=(cfg, output))
    process.start()
    try:
        reservation, _ = output.get(timeout=20)
        process.join(20)
        assert process.exitcode == 0
        monkeypatch.setattr('trader.brain.llm.ROOT', tmp_path / 'installed-checkout')
        installed = BrainLLM(cfg)
        assert installed.budget_left('research') == 0
        assert installed._admit({'prompt': 'second'}, False, 'research') is None
        assert len(json.loads(ledger.read_text())['_reservations']) == 1
        installed._settle(reservation, 123)
        assert BrainLLM(cfg).budget_left('research') == 477
        assert not (tmp_path / 'isolated-checkout' / 'data').exists()
        assert not (tmp_path / 'installed-checkout' / 'data').exists()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        output.close()
        output.join_thread()


def test_alias_uses_same_lock_and_preserves_ledger_target(tmp_path):
    ledger = tmp_path / 'usage.json'
    ledger.write_text('{}')
    alias = tmp_path / 'alias.json'
    alias.symlink_to(ledger)
    first, second = BrainLLM(config(alias)), BrainLLM(config(ledger))
    assert first._usage_path == second._usage_path == ledger
    reservation, _ = first._admit({'prompt': 'offline'}, False, 'research')
    assert alias.is_symlink()
    assert second.budget_left('research') == 0
    second._settle(reservation, 10)
    assert first.budget_left('research') == 590
    assert json.loads(alias.read_text()) == json.loads(ledger.read_text())


@pytest.mark.parametrize('path', ['', 'relative.json', 123, False])
def test_invalid_override_does_not_create_an_empty_budget(path):
    cfg = config('/unused')
    cfg['brain']['usage_ledger_path'] = path
    with pytest.raises(ValueError, match='must_be_absolute'):
        BrainLLM(cfg)


def test_installation_override_reaches_worker_configuration(tmp_path, monkeypatch):
    cfg_file = tmp_path / 'config.yaml'
    cfg_file.write_text('brain: {}\n')
    ledger = tmp_path / 'shared-usage.json'
    monkeypatch.setenv('LUFFY_BRAIN_USAGE_PATH', str(ledger))
    cfg = load_config(str(cfg_file))
    assert cfg['brain']['usage_ledger_path'] == str(ledger)


def test_no_override_preserves_installed_default(tmp_path, monkeypatch):
    monkeypatch.setattr('trader.brain.llm.ROOT', tmp_path)
    cfg = config('/unused')
    del cfg['brain']['usage_ledger_path']
    assert BrainLLM(cfg)._usage_path == tmp_path / 'data' / 'brain_usage.json'
