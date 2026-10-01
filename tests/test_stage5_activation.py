import json
import sqlite3
from types import SimpleNamespace

import pytest

from trader.observability import stage5_activation as A


def window(data, now, end):
    (data / 'stage5-activation.json').write_text(json.dumps(dict(started_ms=now-1, deadline_ms=end)))


def test_expired_window_does_not_fetch_or_extend_on_restart(tmp_path, monkeypatch):
    window(tmp_path, 100, 100)
    before = (tmp_path / 'stage5-activation.json').read_bytes()
    monkeypatch.setattr(A.F, 'urllib_fetch', lambda *a, **k: pytest.fail('unexpected public fetch'))
    for _ in range(2):
        A.run(tmp_path, lambda: False, clock=lambda: 100)
    assert (tmp_path / 'stage5-activation.json').read_bytes() == before
    assert json.loads((tmp_path / 'stage5-public/status.json').read_text())['reason'] == 'WINDOW_EXPIRED'


def test_public_storage_ceiling_blocks_before_fetch(tmp_path, monkeypatch):
    window(tmp_path, 100, 200)
    directory = tmp_path / 'stage5-public'
    directory.mkdir()
    (directory / 'public.db').write_bytes(b'x' * 64)
    monkeypatch.setattr(A, 'MAX_BYTES', 64)
    monkeypatch.setattr(A.F, 'urllib_fetch', lambda *a, **k: pytest.fail('unexpected public fetch'))
    A.run(tmp_path, lambda: False, clock=lambda: 100)
    assert json.loads((directory / 'public.status.json').read_text())['reason'] == 'PUBLIC_STORAGE_BOUND_REACHED'


def test_two_symbols_collect_public_receipts_without_exposure_authority(tmp_path, monkeypatch):
    now = 1_790_000_000_000
    window(tmp_path, now, now+10000)
    calls = []
    def fetch(url, **kwargs):
        assert url.startswith('https://fapi.binance.com/fapi/v1/depth?')
        assert set(kwargs) == {'timeout_s', 'max_bytes'}
        calls.append(url)
        return SimpleNamespace(status=200, body=json.dumps(dict(lastUpdateId=1, E=now, T=now,
            bids=[['99','2']], asks=[['100','2']])).encode())
    def funding(symbol, start, end, *, max_pages):
        assert end-start == 8*3600*1000 and max_pages == 1
        calls.append(symbol)
        return dict(symbol=symbol, open_ms=start, close_ms=end, events=[])
    monkeypatch.setattr(A.F, 'urllib_fetch', fetch)
    status = tmp_path / 'stage5-public/status.json'
    A.run(tmp_path, status.exists, clock=lambda: now, funding_lookup=funding)
    assert len(calls) == 4
    assert all(v == 'RUNNING' for v in json.loads(status.read_text())['outcomes'].values())
    with sqlite3.connect(tmp_path / 'stage5-public/public.db') as db:
        assert db.execute('SELECT kind,count(*) FROM observations GROUP BY kind ORDER BY kind').fetchall() == [
            ('public_funding_interval', 2), ('supplemental_public_depth', 2)]
        for text, in db.execute('SELECT body FROM observations'):
            assert 'position_notional' not in text and 'apiKey' not in text


def test_kernel_paper_close_automatically_records_probation_without_live_authority(tmp_path, monkeypatch):
    import pandas as pd
    from trader.kernel import Kernel
    from trader.engine.paper import PaperExecutor
    from trader.core.types import ControlState
    from trader.strategy import factory_handoff as H
    from tests.test_versioned_paper_execution import setup, frames, snap, decision, append
    from tests.test_strategy_factory_handoff import T0, DAY
    monkeypatch.setattr('time.time', lambda: (T0+120*DAY)/1000)
    j, cfg, version = setup(tmp_path)
    runner = PaperExecutor(j, cfg, venue_environment='production')
    runner.capture = SimpleNamespace(book=lambda *a: None, funding=lambda *a: None)
    df = frames(version)
    df.ts += pd.Timedelta(hours=4)
    initial = snap(df)
    tid = runner.enter(decision(version, initial), initial, version['strategy_id'], reference_equity=5000, state=ControlState.ACTIVE)
    trade = runner.open_positions()[0]
    df = append(df, trade['take_profit'], high=max(trade['entry_price'],trade['take_profit'])+.1,
                low=min(trade['entry_price'],trade['take_profit'])-.1)
    kernel = Kernel.__new__(Kernel)
    kernel.journal, kernel.cfg, kernel._paper = j, cfg, runner
    kernel.state_machine = SimpleNamespace(state=ControlState.FROZEN, manages_exits=lambda: True)
    assert kernel._manage_paper(snap(df)) == [tid]
    receipts = j.query('SELECT status FROM strategy_probation_receipts')
    assert receipts == [{'status': H.P_COST_INCOMPLETE}]
    assert H.state_of(j, version['version_id']) == H.SHADOW
    assert H.live_entry_block(j, version['strategy_id']) is not None
