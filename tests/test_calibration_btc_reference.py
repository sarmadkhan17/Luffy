"""Calibration reference is synthetic context, not another target market."""
import sqlite3

from trader.core.config import load_config
from trader.research.closure import calibration as C, synthetic, manifest
from tests import closure_fixtures


def test_extra_reference_preserves_target_values_and_has_its_own_receipts(tmp_path):
    ordinary, reference = tmp_path / 'ordinary.db', tmp_path / 'reference.db'
    names = synthetic.write_snapshot(ordinary, n_symbols=3, n_bars=50, seed=13)
    assert synthetic.write_snapshot(reference, n_symbols=3, n_bars=50, seed=13,
                                    include_btc_reference=True) == names
    with sqlite3.connect(ordinary) as first, sqlite3.connect(reference) as second:
        assert first.execute('SELECT * FROM candles ORDER BY symbol,ts').fetchall() == second.execute(
            "SELECT * FROM candles WHERE symbol!='BTC/USDT' ORDER BY symbol,ts").fetchall()
        assert second.execute("SELECT COUNT(*) FROM candles WHERE symbol='BTC/USDT'").fetchone()[0] == 50
        assert second.execute('SELECT COUNT(*) FROM market_revisions').fetchone()[0] == 200
        assert second.execute('SELECT COUNT(*) FROM market_revisions').fetchone()[0] == first.execute(
            'SELECT COUNT(*) FROM market_revisions').fetchone()[0] + 50


def test_reference_is_readable_at_development_cut_without_becoming_a_target(tmp_path, monkeypatch):
    original = synthetic.write_snapshot
    def with_reference(*args, **kwargs):
        return original(*args, **kwargs, include_btc_reference=True)
    monkeypatch.setattr(synthetic, 'write_snapshot', with_reference)
    bundle = closure_fixtures.build(tmp_path, name='generated-btc-reference', n_bars=1500)
    try:
        frames, _ = manifest.development_frames(bundle.snap, bundle.env)
        reference, identity = manifest.development_reference(bundle.snap, bundle.env)
        assert 'BTC/USDT' not in frames
        assert 'BTC/USDT' not in bundle.env['discovery_symbols'] + bundle.env['heldout_symbols']
        assert identity and len(reference['BTC/USDT']) > 0
        assert int(manifest._ms(reference['BTC/USDT']['ts']).max()) + synthetic.TF_MS <= bundle.env['dev_end_ms']
    finally:
        bundle.store.close()


def test_new_configured_protocol_declares_reference_without_rewriting_old_presets():
    assert C.configured_protocol(load_config(), '15m', intake_bank_path='explicit-bank.db')['include_btc_reference'] is True
    assert 'include_btc_reference' not in C.PROTOCOL
