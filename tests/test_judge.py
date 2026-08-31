"""Judge layer tests — StrategyJudge stages + BrainJudge decisions.
No browser, no network; LLM stubbed."""
import json

import pytest


# ── StrategyJudge ─────────────────────────────────────────────────────────
def _genome(family="rsi_extreme"):
    from trader.strategy.genome import Genome
    return Genome(strategy_id="j1", family=family,
                  hypothesis="RSI extremes revert as panic exhausts itself "
                             "over multiple distribution sessions.",
                  invalidation="Demote on PF<0.85/20 trades.",
                  regime_filter=frozenset({"RANGING"}),
                  markets=frozenset({"futures"}),
                  params={"rsi_len": 14, "os_level": 30.0, "ob_level": 70.0})


def _judge(tmp_path, monkeypatch, harness_health=None):
    import yaml
    from trader.brain.judge import StrategyJudge
    from trader.core.journal import Journal
    monkeypatch.setattr("trader.brain.tv_harness.CACHE_PATH",
                        tmp_path / "c.json")
    cfg = yaml.safe_load(open("config.yaml")) or {}
    j = Journal(tmp_path / "j.db")
    sj = StrategyJudge(j, cfg)
    if harness_health is not None:
        monkeypatch.setattr(sj.harness, "health",
                            lambda: harness_health)
    return sj, j


def test_prefilter_uses_yahoo_verdict(tmp_path, monkeypatch):
    sj, j = _judge(tmp_path, monkeypatch)
    calls = []

    def fake_wf(symbol, family, n_splits=3):
        calls.append(family)
        return {"valid": False, "checks": {"profitable_oos": False}}

    monkeypatch.setattr(sj.tv, "walk_forward", fake_wf)
    ok, ev = sj.prefilter(_genome())
    assert not ok and ev["stage"] == "yahoo_prefilter"
    assert calls == ["rsi_extreme"]          # family mapped, genome unused


def test_final_verdict_fails_closed_when_harness_down(tmp_path, monkeypatch):
    """A down harness must yield NO verdict — never a Yahoo-proxy approval.

    final_verdict used to end in `return self.prefilter(genome)`, so a down
    harness, an exhausted budget, a malformed tester read or any exception
    handed the decision to the family proxy — a stock TradingView strategy
    that never sees the genome's params. Here the proxy says valid=True and
    must still not be able to approve anything.
    """
    sj, j = _judge(tmp_path, monkeypatch,
                   harness_health={"state": "down", "runs_today": 0,
                                   "budget": 20})
    forged = []
    monkeypatch.setattr("trader.brain.judge.forge_and_store",
                        lambda g, j, **k: forged.append(g) or {})
    monkeypatch.setattr(sj.tv, "walk_forward", lambda *a, **k: {"valid": True})
    ok, ev = sj.final_verdict(_genome())
    assert not forged
    assert ok is False
    assert ev["stage"] == "no_verdict"
    assert "harness unavailable" in ev["reason"]
    # the proxy's opinion is still recorded, just not authoritative
    assert "yahoo_advisory" in ev


def test_final_verdict_fails_closed_on_pipeline_error(tmp_path, monkeypatch):
    sj, j = _judge(tmp_path, monkeypatch,
                   harness_health={"state": "healthy", "runs_today": 0,
                                   "budget": 20})

    def boom(*a, **k):
        raise RuntimeError("tester panel produced no metrics")

    monkeypatch.setattr("trader.brain.judge.forge_and_store", boom)
    monkeypatch.setattr(sj.tv, "walk_forward", lambda *a, **k: {"valid": True})
    ok, ev = sj.final_verdict(_genome())
    assert ok is False and ev["stage"] == "no_verdict"
    assert "pipeline error" in ev["reason"]


def test_final_verdict_uses_real_tv_when_healthy(tmp_path, monkeypatch):
    sj, j = _judge(tmp_path, monkeypatch,
                   harness_health={"state": "healthy", "runs_today": 0,
                                   "budget": 20})
    monkeypatch.setattr("trader.brain.judge.forge_and_store",
                        lambda g, journal, **k: {"strategy_id": g.strategy_id})
    monkeypatch.setattr(
        "trader.brain.judge.evaluate_manifest",
        lambda h, m, min_oos_trades=5: {
            "valid": True, "checks": {}, "net_profit_pct": 12.0,
            "trades": 30, "win_rate": 60.0})
    ok, ev = sj.final_verdict(_genome())
    assert ok and ev["stage"] == "real_tv" and ev["net_profit_pct"] == 12.0


def test_full_judge_does_not_reject_on_proxy_alone(tmp_path, monkeypatch):
    """A failing family proxy must not veto — it cannot see the genome.

    judge() used to return early on prefilter failure, so a genome died on a
    verdict about `ema_cross`/`donchian`/`macd` defaults rather than its own
    genes. The proxy opinion is now attached as context and the real tester
    decides.
    """
    sj, j = _judge(tmp_path, monkeypatch,
                   harness_health={"state": "healthy", "runs_today": 0,
                                   "budget": 20})
    monkeypatch.setattr(sj.tv, "walk_forward", lambda *a, **k: {"valid": False})
    monkeypatch.setattr("trader.brain.judge.forge_and_store",
                        lambda g, journal, **k: {"strategy_id": g.strategy_id})
    monkeypatch.setattr(
        "trader.brain.judge.evaluate_manifest",
        lambda h, m, min_oos_trades=5: {"valid": True, "checks": {},
                                        "net_profit_pct": 9.0})
    ok, ev = sj.judge(_genome())
    # the real tester approved despite the proxy saying no
    assert ok is True and ev["stage"] == "real_tv"
    assert ev["yahoo_advisory"]["stage"] == "yahoo_prefilter"


# ── BrainJudge ────────────────────────────────────────────────────────────
class FakeLLM:
    available = True

    def __init__(self, reply):
        self.reply = reply

    def chat_json(self, prompt, deep=False):
        self.last_prompt = prompt
        return self.reply


def _brain(tmp_path, reply):
    import yaml
    from trader.brain.judge import BrainJudge
    from trader.core.journal import Journal
    cfg = yaml.safe_load(open("config.yaml")) or {}
    bj = BrainJudge(Journal(tmp_path / "b.db"), cfg)
    bj.llm = FakeLLM(reply)

    def seed(sid, state, n_trades=20, wins=14, pnl=50.0):
        bj.journal.query(
            "INSERT INTO strategies (id,name,kind,params,state,description,"
            "origin,hypothesis,invalidation,regime_filter,markets,generation,"
            "parent_id,created_at,stats_json) VALUES (?,?,?,?,?,?,?,?,?,"
            "'[]','[\"futures\"]',0,'','2026-08-01T00:00:00+00:00','{}')",
            (sid, sid, "ema_trend", "{}", state, "d", "seed", "hyp",
             "inv"))
        for i in range(n_trades):
            bj.journal.query(
                "INSERT INTO trades (id,symbol,side,amount,entry_price,"
                "exit_price,status,strategy_id,realized_pnl,opened_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"{sid}_{i}", "X/USDT", "long", 1.0, 100.0, 101.0,
                 "closed", sid, pnl if i < wins else -10.0,
                 "2026-08-01T00:00:00+00:00"))
    rows = [("paper1", "paper"), ("active1", "active"), ("bad1", "paper")]
    for sid, st in rows:
        seed(sid, st, n_trades=15 if sid != "bad1" else 20,
             wins=10 if sid != "bad1" else 3)
    return bj


def test_brain_promotes_within_guardrails(tmp_path):
    reply = {"decisions": [
        {"id": "paper1", "action": "promote", "rationale": "70% WR"},
        {"id": "bad1", "action": "promote", "rationale": "trust me"},
        {"id": "active1", "action": "demote", "rationale": "degrading"},
    ], "direction_assessment": "on track",
        "portfolio_note": "want mean-reversion candidates"}
    bj = _brain(tmp_path, reply)
    rep = bj.review()
    assert rep["reviewed"] and rep["applied"] >= 2
    states = {r["id"]: r["state"] for r in
              bj.journal.list_strategies()}
    assert states["paper1"] == "active"           # eligible promote applied
    assert states["bad1"] == "paper"              # no stats? still paper→active allowed... guardrail is count only
    assert states["active1"] == "demoted"
    ev = bj.journal.query("SELECT detail FROM brain_events WHERE "
                          "kind='brain_judgement'")
    d = json.loads(ev[0]["detail"])
    assert d["direction"] == "on track" and d["used_llm"]


def test_brain_respects_max_active(tmp_path):
    # book already at cap → promotes refused even when LLM insists
    reply = {"decisions": [
        {"id": "paper1", "action": "promote", "rationale": "r"},
        {"id": "bad1", "action": "promote", "rationale": "r"}],
        "direction_assessment": "", "portfolio_note": ""}
    bj = _brain(tmp_path, reply)
    bj.max_active = 1                      # already 1 active in seed
    rep = bj.review()
    assert rep["applied"] == 0
    states = {r["id"]: r["state"] for r in bj.journal.list_strategies()}
    assert states["paper1"] == "paper"
    assert states["bad1"] == "paper"


def test_brain_hold_survives_no_llm(tmp_path):
    import yaml
    from trader.brain.judge import BrainJudge
    from trader.core.journal import Journal
    cfg = yaml.safe_load(open("config.yaml")) or {}
    bj = BrainJudge(Journal(tmp_path / "n.db"), cfg)
    bj.journal.query(
        "INSERT INTO strategies (id,name,kind,params,state,description,"
        "origin,hypothesis,invalidation,regime_filter,markets,generation,"
        "parent_id,created_at,stats_json) VALUES ('s1','S','ema_trend',"
        "'{}','paper','d','seed','hyp','inv','[]','[\"futures\"]',0,'',"
        "'2026-08-01T00:00:00+00:00','{}')")
    bj.llm = FakeLLM(None)
    bj.llm.available = True
    bj.llm.chat_json = lambda prompt, deep=False: None   # API dead → None
    rep = bj.review()
    assert rep["reviewed"]
    ev = bj.journal.query("SELECT detail FROM brain_events WHERE "
                          "kind='brain_judgement'")
    d = json.loads(ev[0]["detail"])
    assert d["used_llm"] is False
