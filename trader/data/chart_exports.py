"""Ingest published TradingView chart exports without recalculating indices.

The source file and chart metadata are retained as acquisition evidence.
Historical rows are as-returned at capture time, not claimed original vintages.
No browser cookies, credentials, private APIs, or local index reconstruction.
"""
from pathlib import Path
import hashlib
import io

import numpy as np
import pandas as pd

from ..core.types import TF_MS
from .references import to_ms

PUBLISHED = {
    'TOTAL': 'CRYPTOCAP:TOTAL',
    'TOTAL1': 'CRYPTOCAP:TOTAL2',
    'TOTAL2': 'CRYPTOCAP:TOTAL3',
    'BTC.D': 'CRYPTOCAP:BTC.D',
    'USDT.D': 'CRYPTOCAP:USDT.D',
}


def reference_key(symbol, timeframe):
    if symbol not in PUBLISHED.values() or timeframe not in ('1d', '4h', '1h'):
        raise ValueError('unsupported published chart or timeframe')
    base = 'tv_' + symbol.split(':', 1)[1].lower().replace('.', '_')
    return base if timeframe == '1d' else base + '_' + timeframe


def load_export(path, *, symbol, timeframe, captured_ms):
    """Parse exact exported OHLC values. No values are inferred or aggregated."""
    key = reference_key(symbol, timeframe)
    raw = Path(path).read_bytes()
    if len(raw) > 50_000_000:
        raise ValueError('chart export exceeds bounded import size')
    frame = pd.read_csv(io.BytesIO(raw))
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    required = ('time', 'open', 'high', 'low', 'close')
    if not set(required) <= set(frame.columns) or frame.columns.duplicated().any():
        raise ValueError('chart export needs unique time/open/high/low/close columns')
    stamp = frame['time']
    if pd.api.types.is_numeric_dtype(stamp):
        if len(stamp) and stamp.abs().max() > 100_000_000_000:
            raise ValueError('numeric chart time must be Unix seconds')
        ts = pd.to_datetime(stamp, unit='s', utc=True, errors='raise')
    else:
        # An explicit timezone prevents an implicit host timezone shift.
        if not stamp.astype(str).str.contains(r'(?:Z|[+-]\d\d:\d\d)$', regex=True).all():
            raise ValueError('chart timestamps require explicit timezone')
        ts = pd.to_datetime(stamp, utc=True, errors='raise')
    result = frame[list(required[1:])].apply(pd.to_numeric, errors='raise')
    if not np.isfinite(result.to_numpy()).all() or (result <= 0).any().any():
        raise ValueError('invalid chart OHLC values')
    if not (result['low'].le(result[['open','close']].min(axis=1)) &
            result['high'].ge(result[['open','close']].max(axis=1))).all():
        raise ValueError('invalid chart OHLC geometry')
    result.insert(0, 'ts', ts)
    if result['ts'].duplicated().any() or not result['ts'].is_monotonic_increasing:
        raise ValueError('chart timestamps duplicate or unordered')
    # Volume is optional for an index; missing stays missing, never zero.
    result['volume'] = pd.to_numeric(frame['volume'], errors='raise') if 'volume' in frame else np.nan
    event_ms = to_ms(result['ts'])      # resolution-independent: astype('int64') is ns, s or us by pandas version
    result = result.loc[event_ms + TF_MS[timeframe] <= int(captured_ms)].copy()
    if result.empty:
        raise ValueError('chart export has no closed bars at capture')
    result.attrs.update(source='tradingview-chart-export:' + symbol,
        request_started_ms=int(captured_ms), raw_source_record=dict(
            schema='published-chart-export.v1', symbol=symbol, timeframe=timeframe,
            csv_sha256=hashlib.sha256(raw).hexdigest(), captured_ms=int(captured_ms),
            raw_csv=raw.decode('utf-8-sig'), vintage='AS_RETURNED_AT_CAPTURE',
            source_url='https://www.tradingview.com/chart/?symbol=' + symbol))
    return key, result


def ingest_exports(entries, store, *, captured_ms):
    """Each configured published export is isolated; no missing source fallback."""
    out = {}
    for item in entries:
        symbol, timeframe = item.get('symbol'), item.get('timeframe')
        identity = str(symbol) + '@' + str(timeframe)
        try:
            key, frame = load_export(item['path'], symbol=symbol, timeframe=timeframe, captured_ms=captured_ms)
            out[identity] = dict(status='IMPORTED', rows=store.save(key, frame, now_ms=captured_ms), key=key,
                                 source_hash=frame.attrs['raw_source_record']['csv_sha256'])
        except (ValueError, KeyError, OSError, pd.errors.ParserError) as exc:
            out[identity] = dict(status='UNAVAILABLE', reason=str(exc)[:180])
    return out
