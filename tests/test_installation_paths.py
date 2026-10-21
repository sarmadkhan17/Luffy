"""Relocation changes installation paths, never trading configuration."""
from pathlib import Path

import pytest

from trader.core import config
from trader.observability import preflight


def test_explicit_installation_overrides_preserve_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'load_dotenv', lambda *a: None)
    path = tmp_path / 'config.yaml'
    path.write_text('dashboard: {host: old-host, startup_mode: LIVE}\n'
                    'owner_interface: {ipc_dir: /old/run}\n'
                    'research: {referee: false, handoff: false}\n')
    monkeypatch.setenv('LUFFY_DASHBOARD_HOST', '127.0.0.1')
    monkeypatch.setenv('LUFFY_OWNER_IPC_DIR', 'private/owner')
    monkeypatch.setenv('LUFFY_RECOVERY_ROOT', '/new/recovery')
    result = config.load_config(str(path))
    assert result['dashboard'] == dict(host='127.0.0.1', startup_mode='LIVE')
    assert result['owner_interface']['ipc_dir'] == 'private/owner'
    assert result['deployment']['recovery_root'] == '/new/recovery'
    assert result['research'] == dict(referee=False, handoff=False)


@pytest.mark.parametrize('variable', ['LUFFY_DASHBOARD_HOST', 'LUFFY_OWNER_IPC_DIR',
                                      'LUFFY_RECOVERY_ROOT'])
def test_empty_installation_override_refused(tmp_path, monkeypatch, variable):
    monkeypatch.setattr(config, 'load_dotenv', lambda *a: None)
    path = tmp_path / 'config.yaml'; path.write_text('{}')
    monkeypatch.setenv(variable, ' ')
    with pytest.raises(ValueError, match=variable):
        config.load_config(str(path))


def test_relocated_recovery_still_requires_separate_filesystem(tmp_path):
    other = tmp_path / 'recovery'; other.mkdir()
    with pytest.raises(ValueError, match='recovery_filesystem_not_separate'):
        preflight._filesystem(tmp_path, other)
