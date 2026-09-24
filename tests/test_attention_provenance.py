"""Code provenance for the automatic registry-backed Attention path."""

import hashlib
from pathlib import Path

import pytest

from trader.observability.attention import code_manifest
from trader.observability.collector_health import code_hash


REGISTRY_MODULES = (
    "data/registry_provider.py",
    "data/binance_usdm_registry.py",
    "core/instrument_registry.py",
    "observability/registry_selector.py",
    "observability/selection_persistence.py",
    "core/journal.py",
)
ROOT = Path(__file__).resolve().parents[1] / "trader"


def test_registry_path_modules_have_actual_byte_hashes_in_attention_manifest():
    manifest = code_manifest()
    for name in REGISTRY_MODULES:
        assert manifest[name] == hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


@pytest.mark.parametrize("name", REGISTRY_MODULES)
def test_registry_module_byte_change_changes_both_attention_hashes(name, monkeypatch):
    before_manifest = code_manifest()
    before_collector = code_hash()
    target = ROOT / name
    original_read_bytes = Path.read_bytes

    def changed_read_bytes(path):
        data = original_read_bytes(path)
        return data + b"\n# provenance mutation\n" if path == target else data

    monkeypatch.setattr(Path, "read_bytes", changed_read_bytes)
    after_manifest = code_manifest()
    assert after_manifest[name] != before_manifest[name]
    assert {k: v for k, v in after_manifest.items() if k != name} == {
        k: v for k, v in before_manifest.items() if k != name
    }
    assert code_hash() != before_collector
