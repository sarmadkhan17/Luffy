"""Copying owned files preserves frozen identities and original export bytes."""
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from trader.core import installation_paths as I
from trader.observability import population as P, learning as L
from tests.test_attention_learning import publish
from tests.test_pit_population import config, NOW
from tests.test_population_rollforward import second, opened, DAY


def bindings(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT key,payload FROM population_meta WHERE key='binding' OR key LIKE 'binding:%' ORDER BY key").fetchall()


def test_copy_without_old_paths_preserves_capture_identity(tmp_path, monkeypatch):
    old = tmp_path / 'old'; old.mkdir()
    cfg = config(old)
    cfg['local_ledgers'] = {'forecast': str(old / 'learning.db')}
    (old / 'pit_population.json').write_text(json.dumps(cfg))
    publish(old / 'attention.db', NOW)
    L.step(old / 'attention.db', old / 'learning.db', NOW, cfg)
    before = bindings(old / 'learning.db')
    evidence = {p.relative_to(old): p.read_bytes() for p in old.glob('exports/*/*.json')}
    frozen = {name: (old / name).read_bytes() for name in ('declaration.json', 'freeze.json', 'pit_population.json')}
    new = tmp_path / 'new'; shutil.copytree(old, new)
    old.rename(tmp_path / 'original-unavailable')
    monkeypatch.setattr(I, 'ROOT', new)
    monkeypatch.setenv('LUFFY_LEGACY_ROOTS', json.dumps([str(old)]))
    moved = P.configured(new)
    assert moved['local_ledgers']['forecast'] == str(new / 'learning.db')
    L.step(new / 'attention.db', new / 'learning.db', NOW + 1, moved)
    assert bindings(new / 'learning.db') == before
    assert all((new / p).read_bytes() == content for p, content in evidence.items())
    assert all((new / name).read_bytes() == content for name, content in frozen.items())
    captured = P.capture(moved, NOW + 2)
    assert captured['freeze'] == json.loads(frozen['freeze.json'])
    assert captured['dataset']['declaration'] == json.loads(frozen['declaration.json'])
    wrong = dict(moved, export_directory=str(new / 'different-exports'))
    with sqlite3.connect(new / 'learning.db') as db, pytest.raises(ValueError, match='population_binding_conflict'):
        P.Producer(db, wrong, 'forecast', NOW + 3)


def test_relocated_second_window_still_refuses_changed_freeze(tmp_path, monkeypatch):
    old = tmp_path / 'old'; old.mkdir()
    first = config(old); cfg = second(old, first)
    with sqlite3.connect(old / 'learning.db') as db:
        opened(db, first, NOW)
        opened(db, cfg, NOW + DAY)
    (old / 'pit_population.json').write_text(json.dumps(cfg))
    before = bindings(old / 'learning.db')
    new = tmp_path / 'new'; shutil.copytree(old, new)
    old.rename(tmp_path / 'original-unavailable')
    monkeypatch.setattr(I, 'ROOT', new)
    monkeypatch.setenv('LUFFY_LEGACY_ROOTS', json.dumps([str(old)]))
    moved = P.configured(new)
    with sqlite3.connect(new / 'learning.db') as db:
        opened(db, moved, NOW + DAY + 1)
    assert bindings(new / 'learning.db') == before
    receipt = Path(moved['receipt']); value = json.loads(receipt.read_text())
    value['code_manifest'] = {'changed': True}
    receipt.write_text(json.dumps(value))
    with sqlite3.connect(new / 'learning.db') as db, pytest.raises(ValueError):
        P.Producer(db, moved, 'forecast', NOW + DAY + 2)
    assert bindings(new / 'learning.db') == before


def test_mapping_cannot_escape_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(I, 'ROOT', tmp_path / 'new')
    monkeypatch.setenv('LUFFY_LEGACY_ROOTS', '["/old/luffy"]')
    with pytest.raises(ValueError, match='installation_path_escape'):
        I.owned_path('/old/luffy/../../outside')
    monkeypatch.setenv('LUFFY_LEGACY_ROOTS', '[]')
    assert not I.same_relocated_path('/old/luffy/data', tmp_path / 'new/data')
