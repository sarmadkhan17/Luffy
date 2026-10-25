"""Hourly current refresh must not rewrite a provider's entire history."""
import pandas as pd
from trader.data import ref_sources as S, references as R
from trader.data.market_provenance import ms


def test_current_reference_refresh_saves_new_bars_and_correction_overlap(monkeypatch):
    day = 86_400_000
    now = 1000 * day
    frame = pd.DataFrame({'ts': pd.to_datetime(range(day, now + 1, day), unit='ms', utc=True),
                          'close': [1.] * 1000})
    frame.attrs['raw_source_record'] = {'whole_response_retained': True}
    monkeypatch.setattr(R, 'REFS', {'stables': R.REFS['stables']})
    monkeypatch.setattr(S, 'defillama_stables', lambda get: frame.copy(deep=True))
    monkeypatch.setattr(S, '_refresh_alts', lambda *a, **k: None)
    class Store:
        def __init__(self): self.saved = []
        def _clock_ms(self): return now
        def last_ts(self, key): return now if key == 'cg_btc_d' else now - day
        def save(self, key, df, **kw):
            self.saved.append(df)
            return len(df)
    store = Store()
    result = S.refresh_all(None, [], store=store, now_ms=now, current_window_ms=31 * day)
    assert result == {'stables': 4}
    assert ms(store.saved[0]['ts']).min() == now - 3 * day
    assert store.saved[0].attrs['raw_source_record'] == frame.attrs['raw_source_record']
    assert len(frame) == 1000                       # source/history untouched
    historical = Store()
    S.refresh_all(None, [], store=historical, now_ms=now)
    assert len(historical.saved[0]) == 1000          # explicit historical caller unchanged
