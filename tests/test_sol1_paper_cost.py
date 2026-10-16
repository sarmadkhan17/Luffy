"""Paper cost contract (SOL1 closure), through the real PaperExecutor.

No TEST-ONLY cost authority is registered here: the estimate is the exact
version's own Backtest PASS cost model, replayed by the production validator.
Funding comes from the existing public funding evidence with an injected fetch;
nothing touches a network, venue, provider or production database.
"""
import hashlib
import json
import types

import numpy as np
import pandas as pd
import pytest

from trader.core.config import load_config
from trader.core.types import Action, Decision, Snapshot
from trader.engine import paper_cost_evidence as C
from trader.engine.paper import PaperExecutor
from trader.observability import funding_events as FE
from trader.strategy import factory_handoff as F
from trader.strategy import qualification as Q
from trader.strategy import quant_contract as QC
from trader.strategy.spec import StrategySpec
from tests.authority_factory_fixtures import DAY, T0
from tests.test_sol1_strategy_lifecycle import (
    SCORED, TF, _ev, _exit_id, _journal, _source, _spec)

H4 = 4 * 3_600_000


def cost_cfg():
    return load_config()


def qualification(spec, cfg, risk=None):
    probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": TF})
    risk = risk if risk is not None else cfg["risk"]
    raw = {k: risk.get(k) for k in QC._CFG_KEYS}
    run = {"contract": "quant-contract.v1", "spec_digest": "a" * 64,
           "data_digest": "b" * 64, "config_digest": QC._digest(raw),
           "engine_digest": "d" * 64, "identity": "e" * 64}
    return Q.build(spec_id=spec.id, timeframe=TF,
                   evaluated_spec_sha256=F._frozen_spec(probe)[1],
                   declared_universe=probe.universe,
                   declared_regime_filter=list(probe.regime_filter),
                   exit_semantics_id=_exit_id(), ev=_ev(), th=Q.thresholds(cfg),
                   run=run, risk_cfg=risk)


def installed(tmp_path, cfg, spec=None):
    j = _journal(tmp_path)
    spec = spec or _spec()
    q = qualification(spec, cfg)
    out = F.create_version(j, cfg, _source(spec, q), at_ms=T0 - DAY)
    from tests.authority_factory_fixtures import _install
    _install(j, out["version_id"], at_ms=T0)
    return j, out, q


def bars(n, start_ms, tail=()):
    """TEST-ONLY synthetic 4h candles with full market-provenance metadata:
    a steady ramp, then `tail` rows of (open, high, low, close)."""
    from trader.data import market_provenance as P
    ts = [start_ms + i * H4 for i in range(n + len(tail))]
    close = list(np.linspace(100.0, 110.0, n))
    rows = [(c, c * 1.002, c * 0.998, c) for c in close] + [tuple(t) for t in tail]
    df = pd.DataFrame({"ts": pd.to_datetime(ts, unit="ms", utc=True),
                       "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                       "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                       "volume": 1000.0})
    at = ts[-1] + H4
    return P.annotate(df, instrument_id="binance_usdm:futures:BTCUSDT",
                      source="https://offline.example/fapi/v1/klines", kind="candle",
                      received_ms=at, timeframe=TF)


def iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def snapshot(df, symbol="BTC/USDT", market="futures"):
    last_open = int(pd.Timestamp(df["ts"].iloc[-1]).timestamp() * 1000)
    at = last_open + H4
    return Snapshot(symbol, iso(at), float(df["close"].iloc[-1]), {TF: df},
                    regime="RANGING", market_type=market)


def enter(paper, j, version, df, symbol="BTC/USDT", market="futures"):
    from trader.strategy.compile import compile_spec
    snap = snapshot(df, symbol, market)
    spec = StrategySpec.from_dict(version["spec"])
    why = []
    sig = compile_spec(spec).to_evaluator()(None, snap, diagnostic=why.append)
    assert sig is not None and sig.action == Action.BUY, why
    dec = Decision("d" + str(len(df)), "c", symbol, Action.BUY, 1., .5, .8, [], [])
    dec.strategy_signals = [dict(strategy_id=spec.id, action="BUY", params=sig.params)]
    return paper.enter(dec, snap, spec.id, reference_equity=10000., state="ACTIVE"), snap




def fetch_of(events=(), calls=None):
    """TEST-ONLY stand-in for the public funding endpoint."""
    def fetch(url, timeout_s=None, max_bytes=None):
        if calls is not None:
            calls.append(url)
        body = json.dumps([dict(symbol="BTCUSDT", fundingTime=t, fundingRate="0.0001",
                                markPrice="110.0") for t in events]).encode()
        return types.SimpleNamespace(status=200, body=body)
    return fetch


NOW = 2_000_000_000_000


class Run:
    """Drives the real PaperExecutor one closed trade at a time."""

    def __init__(self, tmp_path, cfg, spec=None, risk=None, qualify=True):
        self.cfg = cfg
        self.j = _journal(tmp_path)
        spec = spec or _spec()
        q = qualification(spec, cfg, risk) if qualify else None
        if q is None:
            probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": TF})
            q = Q.build(spec_id=spec.id, timeframe=TF,
                        evaluated_spec_sha256=F._frozen_spec(probe)[1],
                        declared_universe=probe.universe,
                        declared_regime_filter=list(probe.regime_filter),
                        exit_semantics_id=_exit_id(), ev=_ev(), th=Q.thresholds(cfg),
                        run={"contract": "quant-contract.v1", "spec_digest": "a" * 64,
                             "data_digest": "b" * 64, "config_digest": "c" * 64,
                             "engine_digest": "d" * 64, "identity": "e" * 64})
        out = F.create_version(self.j, cfg, _source(spec, q), at_ms=T0 - DAY)
        from tests.authority_factory_fixtures import _install
        _install(self.j, out["version_id"], at_ms=T0)
        self.vid = out["version_id"]
        self.v = F.load_version(self.j, self.vid)
        self.qualification = q
        self.market = spec.markets[0]
        self.paper = PaperExecutor(self.j, cfg, venue_environment="production")
        self.start = (T0 + 5 * DAY) // H4 * H4
        self.tail = []
        self.ids = []

    def frame(self):
        return bars(60, self.start, self.tail)

    def ref(self):
        return float(self.frame()["close"].iloc[-1])

    def trade(self, outcome="win"):
        df = self.frame()
        tid, _ = enter(self.paper, self.j, self.v, df, market=self.market)
        ref = float(df["close"].iloc[-1])
        opened = self.row(tid)
        tp, sl = opened["take_profit"], opened["stop_loss"]
        self.tail.append((ref, tp + 0.5, ref, tp) if outcome == "win"
                         else (ref, ref + 0.05, sl - 0.5, ref))
        closed = self.paper.manage(snapshot(self.frame(), market=self.market))
        assert closed == [tid], (closed, tid)
        c = self.tail[-1][3]
        self.tail.append((c, c * 1.01, c * 0.9995, c * 1.005))
        self.ids.append(tid)
        return tid

    def row(self, tid):
        return dict(self.j.query("SELECT * FROM versioned_paper_trades WHERE id=?", (tid,))[0])

    def receipt(self, tid):
        rows = self.j.query(f"SELECT * FROM {C.TABLE} WHERE trade_id=?", (tid,))
        return json.loads(rows[0]["canonical_json"]) if rows else None

    def complete(self, events=(), fetch=None, limit=8):
        return self.paper.complete_costs(fetch=fetch or fetch_of(events),
                                         clock=lambda: NOW, limit=limit)

    def assess(self, day=30):
        return F.evaluate_probation(self.j, self.cfg, self.vid, at_ms=T0 + day * DAY)


@pytest.fixture
def cfg():
    return cost_cfg()


def test_one_futures_trade_has_no_receipt_until_its_funding_is_proven(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    tid = r.trade("win")
    assert r.receipt(tid) is None                        # pending, not guessed
    assert r.row(tid)["status"] == "closed"
    assert r.complete() == [tid]
    assert r.receipt(tid)["net_pnl_status"] == "ESTABLISHED"
    assert r.complete() == []                            # nothing left; idempotent


def test_estimated_costs_are_the_recorded_model_applied_per_leg(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    tid = r.trade("win")
    r.complete()
    rec, row = r.receipt(tid), r.row(tid)
    model = QC.cost_model(cfg["risk"])
    atr = json.loads(json.loads(row["entry_identity_json"])["exit_state"])["policy"]["entry_atr"]
    q, entry, exit_ = row["amount"], row["entry_price"], row["exit_price"]
    slip = model["slippage_atr_frac"] * atr
    expected_slippage = 2 * slip * q
    expected_commission = model["taker_fee_pct"] / 100 * ((entry + slip) * q + (exit_ - slip) * q)
    d = rec["dimensions"]
    assert d["slippage"]["amount"] == pytest.approx(expected_slippage, rel=1e-12)
    assert d["commission"]["amount"] == pytest.approx(expected_commission, rel=1e-12)
    assert d["funding"]["status"] == "NOT_APPLICABLE"
    assert d["funding"]["reason"] == "PROVEN_NO_FUNDING_EVENT_IN_INTERVAL"
    gross = (exit_ - entry) * q
    assert rec["gross_pnl"] == pytest.approx(gross)
    assert rec["known_costs"] == pytest.approx(expected_slippage + expected_commission)
    assert rec["net_pnl"] == pytest.approx(gross - expected_slippage - expected_commission)
    # the exact model, its source and the assumptions are recorded, labelled estimates
    for kind in ("commission", "slippage"):
        sid = rec["source_ids"][kind]
        src = json.loads(r.j.query(f"SELECT canonical_json FROM {C.SOURCES} WHERE source_id=?",
                                   (sid,))[0]["canonical_json"])
        assert src["method"] == C.ESTIMATE_METHOD
        p = src["provenance"]
        assert p["label"] == C.ESTIMATE_LABEL and p["cost_model"] == model
        assert p["qualification_id"] == r.qualification["qualification_id"]
        assert p["entry_atr"] == atr and p["assumptions"] and p["calculation"]
    assert d["commission"]["legs"]["entry"]["inputs"]["classification"] == "taker"
    # funding evidence is the existing public proof and is not also modelled
    assert "funding" in rec["source_ids"]
    assert QC.cost_model(cfg["risk"])["funding_rate_8h_flat_fallback"] not in (
        d["funding"].get("amount"),)
    # and the stored receipt re-verifies against the live journal
    C.verify(r.j, r.row(tid), r.v, F.verify_install(r.j, r.v, current=False))


def run_outcomes(r, outcomes, **kw):
    for o in outcomes:
        r.trade(o)
    done = []
    while True:
        batch = r.complete(**kw)
        if not batch:
            return done
        done += batch


def test_ten_genuine_trades_with_estimated_net_costs_pass_probation(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    run_outcomes(r, ["win"] * 7 + ["loss"] * 3)
    a = r.assess()
    assert a["status"] == F.P_SATISFIED and a["request_id"]
    assert F.state_of(r.j, r.vid) == F.APPROVAL_REQUIRED
    p = json.loads(r.j.query("SELECT canonical_json FROM strategy_probation_receipts")[0]["canonical_json"])
    assert p["stats"]["trades"] == 10 and p["stats"]["paper_cost_basis"] == "NET_WITH_COMPLETE_COST_EVIDENCE"
    assert p["policy"]["min_trades"] == 10
    for t in p["trades"]:
        ev = t["cost_evidence"]
        assert ev["net_pnl_status"] == "ESTABLISHED" and ev["known_costs_complete"] is True
    # the net figure the gate used is gross less the evidenced estimate
    assert p["stats"]["pnl"] == pytest.approx(
        sum(t["realized_pnl"] - t["cost_evidence"]["known_costs"] for t in p["trades"]))
    assert p["stats"]["pnl"] < p["gross_stats"]["pnl"]


def test_nine_trades_do_not_pass(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    run_outcomes(r, ["win"] * 7 + ["loss"] * 2)
    a = r.assess()
    assert a["status"] == F.P_INSUFFICIENT and a["request_id"] is None
    assert F.state_of(r.j, r.vid) == F.SHADOW


def test_ten_trades_failing_win_rate_do_not_pass(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    run_outcomes(r, ["win"] * 3 + ["loss"] * 7)
    assert r.assess()["status"] == F.P_NOT_SATISFIED
    assert F.state_of(r.j, r.vid) == F.SHADOW


def test_costs_can_turn_a_gross_pass_into_a_net_failure(tmp_path, cfg):
    """4 wins / 6 losses: gross profit factor clears 1.15, the evidenced costs
    take the net figure below it, so the gate really runs on net."""
    r = Run(tmp_path, cfg, risk={**cfg["risk"], "taker_fee_pct": 1.0, "slippage_atr_frac": 0.5})
    run_outcomes(r, ["win"] * 4 + ["loss"] * 6)
    a = r.assess()
    p = json.loads(r.j.query("SELECT canonical_json FROM strategy_probation_receipts")[0]["canonical_json"])
    assert p["gross_stats"]["profit_factor"] >= 1.15
    assert p["stats"]["profit_factor"] < 1.15
    assert a["status"] == F.P_NOT_SATISFIED and a["request_id"] is None


def test_pending_cost_evidence_blocks_the_assessment_it_is_not_skipped(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    for o in ["win"] * 7 + ["loss"] * 3:
        r.trade(o)                                     # closed, funding not yet proven
    a = r.assess()
    assert a["status"] == F.P_COST_INCOMPLETE and a["request_id"] is None
    p = json.loads(r.j.query("SELECT canonical_json FROM strategy_probation_receipts")[0]["canonical_json"])
    assert len(p["missing_cost_evidence"]) == 10
    assert F.state_of(r.j, r.vid) == F.SHADOW


def test_a_failed_funding_fetch_leaves_the_trade_pending_and_is_retried(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    tid = r.trade("win")

    def down(url, timeout_s=None, max_bytes=None):
        raise OSError("offline")
    assert r.complete(fetch=down) == []
    assert r.receipt(tid) is None
    assert r.complete() == [tid]


def test_a_funding_event_inside_the_interval_is_refused_never_priced(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    tid = r.trade("win")
    row = r.row(tid)
    mid = (r.row(tid)["entry_bar_ms"] + 2 * H4)
    opened = int(pd.Timestamp(row["opened_at"]).timestamp() * 1000)
    closed = int(pd.Timestamp(row["closed_at"]).timestamp() * 1000)
    assert r.complete(events=[(opened + closed) // 2]) == [tid]
    rec = r.receipt(tid)
    assert rec["net_pnl_status"] == "UNAVAILABLE" and rec["net_pnl"] is None
    assert rec["dimensions"]["funding"]["status"] == "UNAVAILABLE"
    assert "funding" not in rec["source_ids"]           # no invented basis, no estimate
    # estimates exist, but a partial figure is never used as net
    assert rec["dimensions"]["commission"]["status"] == "ESTABLISHED"


def test_a_boundary_funding_event_is_refused(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    tid = r.trade("win")
    opened = int(pd.Timestamp(r.row(tid)["opened_at"]).timestamp() * 1000)
    r.complete(events=[opened])
    assert r.receipt(tid)["net_pnl_status"] == "UNAVAILABLE"


def test_refused_trades_are_reported_and_do_not_count_toward_ten(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    run_outcomes(r, ["win"] * 7 + ["loss"] * 2)
    tid = r.trade("win")
    opened = int(pd.Timestamp(r.row(tid)["opened_at"]).timestamp() * 1000)
    r.complete(events=[opened])                          # the tenth trade is refused
    a = r.assess()
    assert a["status"] == F.P_INSUFFICIENT                # nine counted, not ten
    p = json.loads(r.j.query("SELECT canonical_json FROM strategy_probation_receipts ORDER BY recorded_at_ms")[0]["canonical_json"])
    ex = [e for e in p["excluded_trades"] if e["reason"] == "cost_evidence_refused"]
    assert [e["id"] for e in ex] == [tid] and ex[0]["receipt_sha256"]
    assert p["stats"]["trades"] == 9
    # an eleventh, fully evidenced trade then makes ten counted trades
    r.trade("loss")
    r.complete()
    assert r.assess(day=31)["status"] == F.P_SATISFIED


def test_spot_trade_is_finalized_at_close_with_no_funding(tmp_path, cfg):
    r = Run(tmp_path, cfg, spec=_spec(markets=["spot"]))
    tid = r.trade("win")
    rec = r.receipt(tid)                                  # no complete_costs needed
    assert rec["net_pnl_status"] == "ESTABLISHED"
    assert rec["dimensions"]["funding"]["status"] == "NOT_APPLICABLE"
    assert rec["dimensions"]["funding"]["reason"] == "SPOT_HAS_NO_POSITION_FUNDING"
    assert r.paper.pending_cost_trades() == []


def test_a_pass_without_a_recorded_cost_model_gives_no_estimate(tmp_path, cfg):
    r = Run(tmp_path, cfg, spec=_spec(markets=["spot"]), qualify=False)
    tid = r.trade("win")
    rec = r.receipt(tid)
    assert rec["net_pnl_status"] == "UNAVAILABLE" and rec["net_pnl"] is None
    for kind in ("commission", "slippage"):
        assert rec["dimensions"][kind]["status"] == "UNAVAILABLE"
        assert kind not in rec["source_ids"]
    with pytest.raises(ValueError, match="qualification_cost_model_missing"):
        C.estimate_context(r.j, r.row(tid), r.v)
    run_outcomes(r, ["win"] * 9)
    a = r.assess()
    assert a["status"] == F.P_INSUFFICIENT                # every trade refused, none counted
    assert F.state_of(r.j, r.vid) == F.SHADOW


def test_an_estimate_built_from_another_model_is_not_accepted(tmp_path, cfg, monkeypatch):
    real = C.estimate_context

    def cheaper(journal, trade, version):
        model, atr, qid = real(journal, trade, version)
        return {**model, "taker_fee_pct": 0.0, "slippage_atr_frac": 0.0}, atr, qid
    monkeypatch.setattr(C, "estimate_context", cheaper)
    r = Run(tmp_path, cfg, spec=_spec(markets=["spot"]))
    tid = r.trade("win")
    monkeypatch.setattr(C, "estimate_context", real)
    assert r.receipt(tid)["net_pnl_status"] == "ESTABLISHED"   # internally consistent...
    with pytest.raises(ValueError, match="cost_estimate_inputs_differ"):
        C.verify(r.j, r.row(tid), r.v, F.verify_install(r.j, r.v, current=False))
    ev = F._paper_cost_evidence(r.j, r.row(tid), r.v)          # ...but never usable
    assert ev["net_pnl_status"] == "UNAVAILABLE" and not ev.get("receipt_sha256")


def test_an_atr_that_is_not_the_trades_own_is_not_accepted(tmp_path, cfg, monkeypatch):
    real = C.estimate_context
    monkeypatch.setattr(C, "estimate_context",
                        lambda j, t, v: (lambda m, a, q: (m, a * 2, q))(*real(j, t, v)))
    r = Run(tmp_path, cfg, spec=_spec(markets=["spot"]))
    tid = r.trade("win")
    monkeypatch.setattr(C, "estimate_context", real)
    with pytest.raises(ValueError, match="cost_estimate_inputs_differ"):
        C.verify(r.j, r.row(tid), r.v, F.verify_install(r.j, r.v, current=False))


def test_a_finalized_receipt_is_immutable_and_never_recomputed(tmp_path, cfg):
    r = Run(tmp_path, cfg, spec=_spec(markets=["spot"]))
    tid = r.trade("win")
    before = r.j.query(f"SELECT * FROM {C.TABLE}")
    sources = r.j.query(f"SELECT * FROM {C.SOURCES}")
    with pytest.raises(Exception):
        with r.j._tx() as db:
            db.execute(f"UPDATE {C.TABLE} SET canonical_json='x'")
    with pytest.raises(Exception):
        with r.j._tx() as db:
            db.execute(f"DELETE FROM {C.SOURCES}")
    # re-finalizing with a different model conflicts rather than rewriting
    row, inst = r.row(tid), F.verify_install(r.j, r.v, current=False)
    model, atr, qid = C.estimate_context(r.j, row, r.v)
    with pytest.raises(Exception):
        with r.j._tx() as db:
            C.finalize_estimated(db, row, r.v, inst, ({**model, "taker_fee_pct": 9.0}, atr, qid))
    assert r.j.query(f"SELECT * FROM {C.TABLE}") == before
    assert r.j.query(f"SELECT * FROM {C.SOURCES}") == sources


def _resealed(rec, mutate):
    import copy
    rec = copy.deepcopy(rec)
    rec.pop("qualification_id")
    mutate(rec)
    rec["qualification_id"] = Q.digest(rec)
    return rec


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r["cost_model"].update(taker_fee_pct=0.0), "qualification_cost_model_differs"),
    (lambda r: r["cost_config"].update(taker_fee_pct=0.0), "qualification_cost_config_not_run_config"),
    (lambda r: r["cost_model"].update(basis="ACTUAL"), "qualification_cost_model_differs"),
    (lambda r: r.pop("cost_config"), "qualification_cost_model_missing"),
])
def test_a_resealed_cost_model_is_not_the_runs_cost_model(cfg, mutate, code):
    q = qualification(_spec(), cfg)
    Q.verify(q)
    assert Q.cost_model_of(q) == QC.cost_model(cfg["risk"])
    with pytest.raises(Q.QualificationRefused) as e:
        Q.verify(_resealed(q, mutate))
    assert e.value.code == code
