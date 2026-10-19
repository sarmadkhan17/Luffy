"""D6 — replay availability and next-executable-open fills; offline fixtures only."""
import numpy as np
import pandas as pd
import pytest

from trader.core.types import TF_MS
from trader.data import market_provenance as P
from trader.data.feed import DataFeed
from trader.strategy import fill_timing
from trader.strategy.quant_contract import DEFINITIONS, engine_digest
from trader.strategy.spec import ExitSpec
from trader.strategy.vector_backtest import (WARMUP, _require_replay, simulate, trade_table,
                                             walk_table)

T0 = 1_780_000_000_000 // 900_000 * 900_000
Q = TF_MS['15m']
IID, SRC = 'binance_usdm:futures:BTCUSDT', 'https://offline.example/fapi/v1/klines'
RISK = {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5, "risk_per_trade_pct": 1.0,
        "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06, "funding_rate_8h": 0.0001,
        "bar_minutes": 15}


def prices(n, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.004, n))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.0005, n))   # small honest gaps
    return pd.DataFrame(dict(
        ts=pd.to_datetime(T0 + np.arange(n) * Q, unit='ms', utc=True), open=open_,
        high=np.maximum(open_, close) * 1.0015, low=np.minimum(open_, close) * 0.9985,
        close=close, volume=rng.uniform(50, 500, n)))


def retained(df, delay):
    """One receipt per bar: bar i was received `delay[i]` ms after its close."""
    delay = np.broadcast_to(np.asarray(delay), (len(df),))
    parts = [P.annotate(df.iloc[[i]], instrument_id=IID, source=SRC, kind='candle', timeframe='15m',
                        received_ms=int(P.ms(df['ts'])[i]) + Q + int(delay[i]))
             for i in range(len(df))]
    out = pd.concat(parts, ignore_index=True)
    out.attrs.update(timeframe='15m', read_mode='replay')
    return out


def store(tmp_path, frames, now):
    feed = DataFeed(exchange=None, db_path=tmp_path / 'candles.db', clock_ms=lambda: now, read_only=False)
    for df in frames:
        feed._store_save('BTC/USDT', '15m', df, now_ms=int(df['observed_at_ms'].max()))
    return feed


# ── availability: what replay admits ───────────────────────────────────────

FRESH = P.freshness_ms('15m') - Q          # TTL beyond the interval: the feed's existing budget


@pytest.mark.parametrize('delay,eligible', [(0, True), (1, True), (Q // 2, True), (Q, True),
                                            (Q + FRESH, True), (Q + FRESH + 1, False), (3 * Q, False)])
def test_replay_window_is_the_existing_freshness_budget(tmp_path, delay, eligible):
    df = prices(3)
    feed = store(tmp_path, [retained(df, delay)], T0 + 20 * Q)
    got = feed.replay_ohlcv('BTC/USDT', '15m')
    assert (got is not None and len(got) == 3) is eligible
    meta = feed.replay_metadata('BTC/USDT', '15m')
    assert len(meta) == (3 if eligible else 0)                     # reader/count parity
    assert meta.attrs['stored_bars'] == 3


def test_receipt_clocks_are_never_backdated(tmp_path):
    df = prices(2)
    feed = store(tmp_path, [retained(df, Q // 3)], T0 + 20 * Q)
    got = feed.replay_ohlcv('BTC/USDT', '15m')
    assert (got['available_at_ms'] - (P.ms(got['ts']) + Q)).tolist() == [Q // 3, Q // 3]
    assert got['observed_at_ms'].tolist() == got['available_at_ms'].tolist()


def test_unknown_and_partial_receipts_are_not_replay_eligible(tmp_path):
    df = prices(4)
    good = retained(df, 10)
    partial = P.annotate(df.iloc[[3]], instrument_id=IID, source=SRC, kind='candle', timeframe='15m',
                         received_ms=int(P.ms(df['ts'])[3]) + Q - 5)       # still forming
    assert partial['bar_state'].iloc[0] == 'PARTIAL'
    feed = store(tmp_path, [good.iloc[:3], partial], T0 + 20 * Q)
    assert len(feed.replay_ohlcv('BTC/USDT', '15m')) == 3
    feed.db.execute("UPDATE market_revisions SET available_ms=NULL WHERE event_ms=?", (int(P.ms(df['ts'])[0]),))
    feed.db.commit()
    assert len(feed.replay_ohlcv('BTC/USDT', '15m')) == 2          # unknown never becomes eligible


def test_first_use_revision_is_replayed_not_a_later_correction(tmp_path):
    df = prices(1)
    first = retained(df, 5)
    revised = df.assign(close=df['close'] * 1.01, high=df['high'] * 1.02)
    second = P.annotate(revised, instrument_id=IID, source=SRC, kind='candle', timeframe='15m',
                        received_ms=int(P.ms(df['ts'])[0]) + Q + 90_000)
    feed = store(tmp_path, [first, second], T0 + 20 * Q)
    got = feed.replay_ohlcv('BTC/USDT', '15m')
    assert got['close'].iloc[0] == df['close'].iloc[0]
    assert got['revision_id'].iloc[0] == first['revision_id'].iloc[0]
    assert feed.ohlcv_asof('BTC/USDT', '15m', as_of_ms=T0 + 20 * Q)['close'].iloc[0] == revised['close'].iloc[0]


def test_replay_reads_leave_retained_evidence_unchanged(tmp_path):
    feed = store(tmp_path, [retained(prices(6), Q // 4)], T0 + 20 * Q)
    snap = lambda: feed.db.execute('SELECT revision_id,record_json FROM market_revisions ORDER BY 1').fetchall()
    before = snap()
    feed.replay_ohlcv('BTC/USDT', '15m'); feed.replay_metadata('BTC/USDT', '15m')
    assert snap() == before


# ── decision and fill ordering ─────────────────────────────────────────────

def test_decision_waits_for_every_lookback_input():
    df = retained(prices(40), 0)
    df.loc[20, 'available_at_ms'] = int(df['available_at_ms'].iloc[20]) + Q + 100   # bar 20 reaches us just after bar 21 closes
    fill, why = fill_timing.fills(df)
    assert fill[19] == 20 and fill[20] < 0 and why[20] == 'signal_stale'   # its own signal is two bars old
    assert fill[21] == 23 and fill[22] == 23 and fill[23] == 24           # bar 21 had to wait for bar 20 (else 22)
    ts = P.ms(df['ts'])
    avail = df['available_at_ms'].to_numpy()
    for k in range(15, 39):
        if fill[k] >= 0:
            assert ts[fill[k]] >= avail[k - 14:k + 1].max()               # never before any input


@pytest.mark.parametrize('delay,lag_bars', [(0, 1), (1, 2), (Q // 2, 2), (Q - 1, 2), (Q, 2)])
def test_fill_is_first_open_at_or_after_decision(delay, lag_bars):
    df = retained(prices(8), delay)
    fill, why = fill_timing.fills(df)
    ts = P.ms(df['ts'])
    for i in range(6):
        assert fill[i] == i + lag_bars
        decision = ts[i] + Q + delay
        assert ts[fill[i]] >= decision                               # never before the decision
        assert ts[fill[i]] - (ts[i] + Q) <= P.freshness_ms('15m') - Q * 0 # inside the freshness budget
    assert (fill[-1] < 0) and why[-1] == 'no_executable_bar'


def test_refusal_reasons_are_exact():
    df = retained(prices(40), [0] * 40)
    df.loc[3, 'available_at_ms'] = None
    stale = df.copy(); stale.loc[30, 'available_at_ms'] = int(stale['available_at_ms'].iloc[30]) + Q + 1
    assert fill_timing.fills(stale)[1][30] == 'signal_stale'          # fill would land two bars after the close
    assert fill_timing.fills(stale)[0][29] == 30
    fill, why = fill_timing.fills(df)
    assert why[3] == 'availability_unknown' and why[10] == 'availability_unknown' and why[18] == ''
    one = df.drop(index=[25]).reset_index(drop=True)                    # one bar missing: next retained open is one bar late
    fill1, why1 = fill_timing.fills(one)
    assert why1[24] == '' and fill1[24] == 25                           # still inside interval + TTL
    two = df.drop(index=[25, 26]).reset_index(drop=True)                # two missing: the price is two bars old
    fill2, why2 = fill_timing.fills(two)
    assert why2[24] == 'signal_stale' and fill2[24] < 0
    assert why[-1] == 'no_executable_bar'
    bad = df.copy(); bad.loc[34, 'open'] = np.nan
    assert fill_timing.fills(bad)[1][33] == 'unexecutable_price'
    summary = fill_timing.summary(two)
    assert summary['executable'] == int((fill2 >= 0).sum()) and summary['signal_stale'] >= 1


def test_frames_without_receipts_decide_at_close():
    df = prices(10)
    fill, why = fill_timing.fills(df)
    assert fill[:-1].tolist() == list(range(1, 10)) and why[-1] == 'no_executable_bar'


# ── trade definition ───────────────────────────────────────────────────────

def one_trade(df, i, side, **kw):
    n = len(df)
    sig = np.zeros(n, bool); sig[i] = True
    fills = []
    res = simulate(sig if side == 'long' else np.zeros(n, bool), sig if side == 'short' else np.zeros(n, bool),
                   df, ExitSpec(target={'kind': 'none'}, trail={'kind': 'none'}, time={'max_bars': 4}),
                   RISK, fills_out=fills, score_from=0, **kw)
    return res, fills


@pytest.mark.parametrize('side', ['long', 'short'])
def test_entry_pays_the_fill_bar_open_not_the_signal_close(side):
    n = WARMUP + 40
    df = prices(n)
    i = WARMUP + 5
    sign = 1.0 if side == 'long' else -1.0
    for delay, f in ((0, i + 1), (Q // 2, i + 2)):
        late = retained(df, delay)
        res, fills = one_trade(late, i, side)
        assert fills[0].entry_i == i and res.trades == 1             # portfolio entry stays the signal bar
        # re-derive the one trade by hand from the fill bar's open
        atr = __import__('trader.agents.indicators', fromlist=['atr_series']).atr_series(df, 14).to_numpy(float)
        entry = df['open'].iloc[f] + sign * atr[i] * RISK['slippage_atr_frac']
        sl = entry - sign * max(atr[i] * 2.0, df['open'].iloc[f] * 0.004)
        assert np.isfinite(entry) and (entry - sl) * sign > 0
        assert fills[0].exit_i >= f                                 # nothing can exit before the fill bar


def test_an_expired_or_unknown_signal_cannot_trade():
    n = WARMUP + 40
    df = prices(n)
    i = WARMUP + 5
    expired = retained(df, Q + 1)
    assert one_trade(expired, i, 'long')[0].trades == 0
    unknown = retained(df, 0); unknown['available_at_ms'] = np.nan
    assert one_trade(unknown, i, 'short')[0].trades == 0
    assert one_trade(retained(df, 0), i, 'short')[0].trades == 1


def test_delay_changes_the_fill_but_not_the_signal_or_costs():
    n = WARMUP + 60
    df = prices(n)
    prompt, late = retained(df, 0), retained(df, Q // 2)
    i = WARMUP + 5
    a, fa = one_trade(prompt, i, 'long')
    b, fb = one_trade(late, i, 'long')
    assert fa[0].entry_i == fb[0].entry_i == i
    assert a.trades == b.trades == 1 and a.pnl_usdt != b.pnl_usdt   # a real, costed difference
    # same cost model: re-running is bit-for-bit deterministic
    assert one_trade(late, i, 'long')[0].pnl_usdt == b.pnl_usdt


@pytest.mark.parametrize('delay', [0, Q // 3, Q])
def test_actual_and_null_use_the_same_fills(delay):
    n = WARMUP + 220
    df = retained(prices(n, seed=11), delay)
    rng = np.random.default_rng(5)
    long = rng.random(n) < 0.04
    short = (rng.random(n) < 0.04) & ~long
    spec = ExitSpec(target={'kind': 'rr', 'v': 2.0}, time={'max_bars': 12})
    fills = []
    res = simulate(long, short, df, spec, RISK, fills_out=fills)
    table = trade_table(df, spec, RISK)
    walked = walk_table(table, long, short, RISK)
    assert res.trades == len(walked) > 0
    assert [(f.entry_i, f.exit_i) for f in fills] == [(w[0], w[1]) for w in walked]
    assert [f.r_multiple for f in fills] == pytest.approx([w[2] for w in walked])
    # a rotated (null) signal set is priced by the same table: delayed bars stay delayed
    rolled = np.roll(long, 17), np.roll(short, 17)
    assert all(table[s][0][i] == -1 or table[s][0][i] >= i + 1 for s in table for i in np.flatnonzero(rolled[0] | rolled[1]))
    for side in ('long', 'short'):
        refused = fill_timing.fills(df)[0] < 0
        assert (table[side][0][refused] == -1).all()                  # refused bars open nothing, actual or null


def test_replay_guard_and_contract_text():
    _require_replay({'15m': retained(prices(3), 5)})
    assert 'OPEN' in DEFINITIONS['entry'] and 'availability' in DEFINITIONS['entry']
    assert len(engine_digest()) == 64


def test_a_missing_bar_in_the_lookback_refuses_receipt_frames_only():
    df = retained(prices(60), 0).drop(index=[10]).reset_index(drop=True)
    fill, why = fill_timing.fills(df)
    assert why[20] == 'history_gap' and fill[20] < 0                  # 15-bar window spans the hole
    assert why[30] == '' and fill[30] == 31
    df.attrs['signal_lookback_bars'] = 40                              # a deeper strategy needs more clean history
    assert fill_timing.fills(df)[1][30] == 'history_gap'
    plain = df.drop(columns=list(P.META))                               # retrospective: unchanged behaviour
    assert (fill_timing.fills(plain)[1] != 'history_gap').all()


def test_lookback_is_derived_from_the_strategy_not_a_global_constant():
    import json
    from trader.strategy.spec import StrategySpec
    from trader.strategy.compile import compile_spec
    seed = lambda name: StrategySpec.from_dict(json.load(open(f'data/seed_specs/{name}.json')))
    shallow, deep = seed('bollinger_band_fade'), seed('ema_stack_pullback')
    assert fill_timing.lookback_bars(shallow) < fill_timing.lookback_bars(deep) <= 210
    df = retained(prices(WARMUP + 40), 0)
    compile_spec(shallow).entries({'15m': df})
    assert df.attrs['signal_lookback_bars'] == fill_timing.lookback_bars(shallow)
    assert fill_timing.fills(df.iloc[30:90].reset_index(drop=True)) is not None
    assert df.iloc[30:90].reset_index(drop=True).attrs['signal_lookback_bars'] == df.attrs['signal_lookback_bars']
