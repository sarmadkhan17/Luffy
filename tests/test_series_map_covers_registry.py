"""Every derivative a feature can require must be loadable, or it is invisible.

`spec_evidence._SERIES_FOR` maps a spec's `data_requires` name to the series
stored in derivs.db, and `load_derivs` silently skips any name it does not
know. `ls_account_ratio` — the 334-day Coinalyze series — was registered as a
feature on 2026-09-03 and never added to the map. So a spec built on it:

  * loaded nothing, so its features were NaN and it took zero trades;
  * was not even listed as NEEDED by `missing_data`, which filters on the
    same map — so it read as honestly testable and was scored and refused,
    where the invariant says an absent series is UNTESTED, never a score;
  * could never signal live, because the kernel loads through the same map.
"""
from types import SimpleNamespace

import pandas as pd

import trader.strategy.features_deriv  # noqa: F401  (registers deriv features)
import trader.strategy.features_xs  # noqa: F401  (registers xs features)
from trader.strategy import spec_evidence
from trader.strategy.features import FEATURES


def test_every_required_series_in_the_registry_is_loadable():
    needed = {r for f in FEATURES.values() for r in f.requires if r != "ohlcv"}
    unmapped = sorted(needed - set(spec_evidence._SERIES_FOR))
    assert not unmapped, (
        f"features require {unmapped} but load_derivs cannot load them — "
        f"every spec using them evaluates to NaN and takes zero trades")


class _Feed:
    def __init__(self, have):
        self.have = have

    def load(self, symbol, series):
        if series not in self.have:
            return None
        return pd.DataFrame({"ts": [1_788_000_000_000], "value": [1.7]})


def test_the_account_ratio_loads():
    out = spec_evidence.load_derivs("BTC/USDT", ["ohlcv", "ls_account_ratio"],
                                    _Feed({"ls_account_ratio"}))
    assert "ls_account_ratio" in out


def test_an_absent_account_ratio_is_untested_not_scored():
    spec = SimpleNamespace(data_requires=["ohlcv", "ls_account_ratio"])
    gaps = spec_evidence.missing_data(spec, ["BTC/USDT"], _Feed(set()))
    assert gaps == {"BTC/USDT": ["ls_account_ratio: absent"]}
