"""The production fee reader: no network, no credentials leaked."""
import pytest

from scripts import read_prod_fee as rpf


def test_missing_credentials_refuses_and_names_the_variables(monkeypatch):
    monkeypatch.setattr(rpf, "ROOT", rpf.ROOT / "nonexistent")   # no .env
    monkeypatch.delenv(rpf.KEY_ENV, raising=False)
    monkeypatch.delenv(rpf.SECRET_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        rpf.credentials()
    assert rpf.KEY_ENV in str(e.value) and rpf.SECRET_ENV in str(e.value)


def test_the_kernel_key_is_never_used(monkeypatch):
    monkeypatch.setattr(rpf, "ROOT", rpf.ROOT / "nonexistent")
    monkeypatch.delenv(rpf.KEY_ENV, raising=False)
    monkeypatch.delenv(rpf.SECRET_ENV, raising=False)
    monkeypatch.setenv("BINANCE_API_KEY", "demo-key")
    monkeypatch.setenv("BINANCE_SECRET_KEY", "demo-secret")
    with pytest.raises(SystemExit):
        rpf.credentials()


def test_reads_the_declared_universe():
    syms = rpf.declared_symbols()
    assert len(syms) == 16 and "BTC/USDT" in syms


def test_measure_records_rates_without_the_secret(monkeypatch):
    def fake_get(base, path, key, secret, params=None):
        if "apiRestrictions" in path:
            return 200, {"enableReading": True, "enableFutures": False,
                         "ipRestrict": True}
        return 200, {"symbol": params["symbol"], "makerCommissionRate": "0.000200",
                     "takerCommissionRate": "0.000500"}
    monkeypatch.setattr(rpf, "_get", fake_get)
    rec = rpf.measure("k-123", "s-456", ["BTC/USDT"])
    assert rec["rates"]["BTC/USDT"] == {"maker": 0.0002, "taker": 0.0005}
    assert "s-456" not in str(rec) and "k-123" not in str(rec)
