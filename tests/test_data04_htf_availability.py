"""DATA-04 offline native/aggregate availability and retained restart proofs."""
import json

import pandas as pd
import pytest

from trader.data import market_provenance as P
from trader.data.feed import DataFeed
from trader.strategy.backtest import resample
from trader.strategy.dsl import evaluate_bool, parse
from trader.strategy.features import FeatureCtx
from trader.strategy.spec_evidence import frames_for
from tests.test_stage1_data_provenance_pit import Clock, Venue, T, H, Q, frame, receipt, persist


@pytest.mark.parametrize('tf,duration', [('1h', H), ('4h', 4*H)])
def test_native_partial_future_backfill_replay_and_reopen(tmp_path, tf, duration):
    clock = Clock(T+duration//2)
    venue = Venue([])
    path = tmp_path/'native.db'
    feed = DataFeed(exchange=venue, db_path=path, clock_ms=clock)
    persist(feed, frame(step=duration), clock.at, tf)
    assert feed.ohlcv_asof('BTC/USDT', tf, as_of_ms=clock.at) is None
    partial = feed.ohlcv_asof('BTC/USDT', tf, as_of_ms=clock.at, include_partial=True)
    assert partial.iloc[0].bar_state == 'PARTIAL'
    assert partial.iloc[0].quality == 'INCOMPLETE'
    assert P.eligible_frame(partial, tf, T+2*duration).empty
    close = T+duration
    persist(feed, frame(step=duration, value=12), close, tf)
    original = feed.ohlcv_asof('BTC/USDT', tf, as_of_ms=close).iloc[0]
    assert original.supersedes == partial.iloc[0].revision_id
    late = close+Q
    persist(feed, frame(step=duration, value=30), late, tf)
    # A previously absent event arrives only as later backfill.
    persist(feed, frame(at=T-duration, step=duration, value=99), late, tf)
    clock.at = late
    for reader in (feed, DataFeed(exchange=venue, db_path=path, clock_ms=Clock(late))):
        assert reader.ohlcv_asof('BTC/USDT', tf, as_of_ms=close-1) is None
        at_close = reader.ohlcv_asof('BTC/USDT', tf, as_of_ms=close)
        assert len(at_close) == 1 and at_close.iloc[0].revision_id == original.revision_id
        assert at_close.iloc[0].close == 12
        assert reader.ohlcv_asof('BTC/USDT', tf, as_of_ms=late-1).iloc[-1].close == 12
        latest = reader.ohlcv_asof('BTC/USDT', tf, as_of_ms=late)
        assert latest.close.tolist() == [99, 30]
        replay = reader.replay_ohlcv('BTC/USDT', tf)
        assert len(replay) == 1 and replay.iloc[0].revision_id == original.revision_id
    assert venue.calls == []


def test_sparse_asof_children_cannot_infer_coarser_timeframe(tmp_path):
    feed = DataFeed(exchange=Venue([]), db_path=tmp_path/'sparse.db', clock_ms=Clock(T+H))
    persist(feed, frame(n=4).iloc[[0, 2]], T+H)
    children = feed.ohlcv_asof('BTC/USDT', as_of_ms=T+H)
    aggregate = resample(children, '1h')
    assert aggregate.iloc[0].bar_state == 'PARTIAL'
    assert aggregate.iloc[0].quality == 'INCOMPLETE'
    assert P.eligible_frame(aggregate, '1h', T+H).empty


def test_aggregate_requires_declared_qualified_child_timeframe():
    children = receipt(frame(n=4), T+H)
    children.attrs.pop('timeframe')
    with pytest.raises(ValueError, match='source timeframe'):
        resample(children, '1h')


@pytest.mark.parametrize('missing', range(4))
def test_each_missing_child_keeps_parent_partial(missing):
    children = receipt(frame(n=4), T+H).drop(index=missing)
    aggregate = resample(children, '1h')
    assert aggregate.iloc[0].bar_state == 'PARTIAL'
    assert aggregate.iloc[0].quality == 'INCOMPLETE'
    assert P.eligible_frame(aggregate, '1h', T+2*H).empty


@pytest.mark.parametrize('quality', ['INCOMPLETE', 'VALID'])
def test_complete_grid_with_partial_child_never_final(quality):
    children = receipt(frame(n=4), T+H)
    children.loc[3, 'bar_state'] = 'PARTIAL'
    children.loc[3, 'quality'] = quality
    aggregate = resample(children, '1h')
    assert aggregate.iloc[0].bar_state == 'PARTIAL'
    assert aggregate.iloc[0].quality == 'INCOMPLETE'
    assert P.eligible_frame(aggregate, '1h', T+2*H).empty


def test_aggregate_receipt_maxima_feature_cut_and_late_revision(tmp_path):
    feed = DataFeed(exchange=Venue([]), db_path=tmp_path/'children.db', clock_ms=Clock(T+H))
    for i in range(4):
        persist(feed, frame(at=T+i*Q), T+(i+1)*Q)
    original = resample(feed.replay_ohlcv('BTC/USDT'), '1h')
    assert original.iloc[0].bar_state == 'FINAL'
    assert original.iloc[0].available_at_ms == T+H
    late = T+H+Q
    persist(feed, frame(at=T+3*Q, value=100), late)
    # A late retrieval must affect current aggregation only after its receipt.
    feed._clock_ms = Clock(late)
    for reader in (feed, DataFeed(exchange=feed.ex, db_path=tmp_path/'children.db', clock_ms=Clock(late))):
        early = resample(reader.ohlcv_asof('BTC/USDT', as_of_ms=late-1), '1h')
        later = resample(reader.ohlcv_asof('BTC/USDT', as_of_ms=late), '1h')
        assert early.iloc[0].close == 10 and later.iloc[0].close == 100
        assert later.iloc[0].available_at_ms == later.iloc[0].observed_at_ms == late
        assert P.eligible_frame(later, '1h', late-1).empty
        replay = resample(reader.replay_ohlcv('BTC/USDT'), '1h')
        assert replay.iloc[0].revision_id == original.iloc[0].revision_id
        assert json.loads(replay.iloc[0].raw_json)['constituents'] == json.loads(original.iloc[0].raw_json)['constituents']
        for cut, expected in [(late-1, False), (late, True)]:
            base = receipt(frame(at=T+H-Q), cut)
            ctx = FeatureCtx({'15m': base, '1h': later}, '15m', as_of_ms=cut)
            assert bool(evaluate_bool(parse("htf('1h', close > 50)"), ctx)[-1]) is expected


def test_aggregate_observation_clock_fences_feature_inputs():
    children = receipt(frame(n=4, value=100), T+H)
    children.loc[3, 'observed_at_ms'] = T+H+Q
    aggregate = resample(children, '1h')
    assert aggregate.iloc[0].observed_at_ms == T+H+Q
    assert P.eligible_frame(aggregate, '1h', T+H).empty


@pytest.mark.parametrize('tf,duration,count', [('1h', H, 4), ('4h', 4*H, 16)])
def test_late_missing_child_backfill_never_repairs_earlier_cut(tmp_path, tf, duration, count):
    late = T+duration+Q
    path = tmp_path/'backfill.db'
    feed = DataFeed(exchange=Venue([]), db_path=path, clock_ms=Clock(late))
    for i in range(count-1):
        persist(feed, frame(at=T+i*Q), T+(i+1)*Q)
    persist(feed, frame(at=T+(count-1)*Q), late)
    for reader in (feed, DataFeed(exchange=feed.ex, db_path=path, clock_ms=Clock(late))):
        before = resample(reader.ohlcv_asof('BTC/USDT', as_of_ms=late-1), tf)
        after = resample(reader.ohlcv_asof('BTC/USDT', as_of_ms=late), tf)
        assert before.iloc[0].bar_state == 'PARTIAL'
        assert P.eligible_frame(before, tf, late).empty
        assert after.iloc[0].bar_state == 'FINAL' and after.iloc[0].quality == 'VALID'
        assert after.iloc[0].available_at_ms == late
        assert P.eligible_frame(after, tf, late-1).empty
        replay = resample(reader.replay_ohlcv('BTC/USDT'), tf)
        assert replay.iloc[0].bar_state == 'PARTIAL'
        assert P.eligible_frame(replay, tf, late).empty


def test_real_unfinished_child_stays_partial_after_nominal_parent_close():
    children = receipt(frame(n=4), T+H-Q)
    assert children.bar_state.tolist() == ['FINAL', 'FINAL', 'FINAL', 'PARTIAL']
    aggregate = resample(children, '1h')
    assert aggregate.iloc[0].bar_state == 'PARTIAL'
    assert P.eligible_frame(aggregate, '1h', T+2*H).empty


def test_feature_frame_builder_cannot_relabel_sparse_children_as_native_htf():
    children = receipt(frame(n=12).iloc[[0, 4, 8]], T+3*H)
    higher = frames_for(children, '1h')['1h']
    assert higher.bar_state.eq('PARTIAL').all()
    assert higher.quality.eq('INCOMPLETE').all()
    assert P.eligible_frame(higher, '1h', T+4*H).empty
