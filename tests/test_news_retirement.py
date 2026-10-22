from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from trader.dashboard.current_truth import read_news_guard


def test_retired_reader_never_reads_saved_guard_records():
    def forbidden(*a,**kw):raise AssertionError('retired guard read historical state')
    result,error=read_news_guard(SimpleNamespace(kv_get=forbidden),datetime.now(timezone.utc))
    assert result['status']=='RETIRED' and result['active'] is None
    assert result['clear'] is False and error is None


def test_news_no_longer_changes_strategy_score_or_threshold(monkeypatch):
    import trader.engine.orchestrator as module
    from tests.test_scout_upgrades import _orch,_snap,_strategy_proposal
    monkeypatch.setattr(module,'htf_trend_score',lambda _:0)
    o=_orch(.9,None)
    def forbidden():raise AssertionError('retired guard executed')
    o.news_guard=SimpleNamespace(check=forbidden)  # stale in-memory extension is inert
    d=o.decide(_snap(),_strategy_proposal(monkeypatch,.9),entry_allowed=True)
    assert d.threshold==pytest.approx(.24) and d.score==pytest.approx(.9)
    assert all('news_blackout' not in vote.get('meta',{}) for vote in d.votes)
