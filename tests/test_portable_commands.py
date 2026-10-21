from scripts import portable
from trader import runtime_identity
import json
import os
from pathlib import Path
import subprocess
import sys


def test_failed_verified_stop_does_not_touch_dashboard(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    monkeypatch.setattr(portable, 'ROOT', tmp_path)
    monkeypatch.setattr(runtime_identity, 'inspect', lambda *a: {'state': 'NONE'})
    monkeypatch.setattr(runtime_identity, 'stop_kernel', lambda *a, **kw: 17)
    monkeypatch.setattr(portable.os, 'kill', lambda *a: (_ for _ in ()).throw(AssertionError('signal after refused stop')))
    assert portable.stop(1) == 17
    assert (tmp_path / 'data/watchdog.off').exists()


def test_unconfirmed_freeze_prevents_shutdown(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    monkeypatch.setattr(portable, 'ROOT', tmp_path)
    monkeypatch.setattr(runtime_identity, 'inspect', lambda *a: {'state': 'RUNNING', 'verified': True})
    monkeypatch.setattr(portable, 'freeze_for_shutdown', lambda: False)
    monkeypatch.setattr(runtime_identity, 'stop_kernel', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('unconfirmed containment')))
    assert portable.stop(1) == 5


def test_start_uses_supported_launcher_with_exact_revision(monkeypatch):
    monkeypatch.setattr(portable.sys, 'argv', ['luffy', 'start-kernel'])
    monkeypatch.setattr(runtime_identity, 'code_revision', lambda *a: {'revision': 'a' * 40})
    calls = []
    monkeypatch.setattr(portable.subprocess, 'call', lambda args, **kw: calls.append((args, kw)) or 0)
    assert portable.main() == 0
    args, kwargs = calls[0]
    assert args == [str(portable.ROOT / 'restart.sh'), 'kernel']
    assert kwargs['env']['LUFFY_EXPECT_REVISION'] == 'a' * 40


def test_copied_launcher_owns_temporary_directory_from_any_cwd(tmp_path):
    root = tmp_path / 'copied LUFFY'; root.mkdir()
    launcher = root / 'luffy'
    launcher.write_bytes((Path(__file__).resolve().parents[1] / 'luffy').read_bytes())
    launcher.chmod(0o755)
    binary = root / 'venv/bin/python'; binary.parent.mkdir(parents=True)
    binary.write_text('#!' + sys.executable + '\nimport os,json\n'
                      'print(json.dumps({"cwd":os.getcwd(),"tmp":os.environ["TMPDIR"]}))\n')
    binary.chmod(0o755)
    run = subprocess.run([str(launcher), 'status'], cwd=tmp_path,
                         env={**os.environ, 'TMPDIR': '/old-machine/absent'},
                         capture_output=True, text=True, check=True)
    assert json.loads(run.stdout) == {'cwd': str(root), 'tmp': str(root / 'tmp')}
    assert (root / 'tmp').is_dir()
