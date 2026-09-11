"""A reference nobody records is a reference that goes stale, then NaN."""
import inspect

from trader.core.config import load_config


def test_the_kernel_runs_a_reference_recorder():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert "_reference_recorder" in src and '"ref-recorder"' in src


def test_the_alt_index_members_exclude_btc():
    r = load_config()["references"]
    assert r["enabled"] is True
    members = r["alts_members"]
    assert len(members) == 35 and "BTC/USDT" not in members
