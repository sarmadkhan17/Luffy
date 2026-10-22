"""LUFFY-CURRENT-TRUTH-CONTRACT-R1 review corrections (1–8), adversarial.

Temp journals, fake feeds/venue doubles only; no network, no production DB.
"""
import json
import math
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from trader.core import truth
from trader.core.journal import Journal
from trader.core.types import ControlState
from trader.dashboard import current_truth, owner_api, valuation
from trader.engine.risk import RiskManager, policy_from_config
from tests.owner_frontend_fixture import (RISK_FIXTURE_CFG, account_observation,
                                          news_guard_state, risk_assessment)
from tests.test_current_truth_contract import (RISK_CFG,
                                               _kernel, _kv, _obs, _q,
                                               _trade)


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "j.db")


# ── 1. strict future: no skew anywhere ─────────────────────────────────────
@pytest.mark.parametrize("ahead", [0.01, 0.5, 1.0, 5.0])
def test_owner_readers_never_accept_any_future_time(journal, tmp_path, ahead):
    now = _now()
    fut = now + timedelta(seconds=ahead)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "heartbeat_luffy.json").write_text(
        json.dumps({"timestamp": fut.timestamp(), "state": "ACTIVE"}))
    assert owner_api.read_heartbeat(tmp_path, now)[0]["freshness"] == "invalid"
    _kv(journal, "supervisor_status", {"outcome": "SAFE", "updated_at": fut.isoformat(),
                                       "checks": {"venue_protection": True}})
    sup = owner_api.read_supervisor(journal, now)[0]
    assert sup["freshness"] == "invalid" and sup["venue_protection"] is None
    from tests.owner_frontend_fixture import protection_snapshot, store_protection_snapshot
    with journal._tx() as c:
        store_protection_snapshot(c, protection_snapshot(fut))
    snap, _ = owner_api.read_protection_snapshot(journal, now)
    assert snap["freshness"] == "invalid"
    _kv(journal, "account_observation", account_observation(fut))
    assert current_truth.read_account(journal, now)[0]["freshness"] == "invalid"
    _kv(journal, "risk_assessment", risk_assessment(fut))
    assert current_truth.read_risk(journal, now)[0]["current"]["status"] == "UNAVAILABLE"
    _kv(journal, "news_guard_state", news_guard_state(fut))
    assert current_truth.read_news_guard(journal, now)[0]["status"] == "RETIRED"


def test_truth_has_no_skew_constant_left():
    assert not hasattr(truth, "CLOCK_SKEW_S")
    assert not hasattr(owner_api, "CLOCK_SKEW_S")


# ── 2. News ────────────────────────────────────────────────────────────────


# ── 3. Risk ────────────────────────────────────────────────────────────────
def _risk_kernel(journal, monkeypatch, equity=1000.0):
    return _kernel(journal, monkeypatch,
                   [(equity, "venue_total_margin_balance" if equity else None, [])])


def _assess(k, state=ControlState.ACTIVE, before=None):
    _, status = k._risk_step()
    if before:
        before(k)
    k._record_risk_assessment(state, status, True, "")
    return json.loads(k.journal.kv_get("risk_assessment"))


def _open(journal, tid, **cols):
    base = {"id": tid, "symbol": f"{tid}/USDT", "side": "long", "amount": 1.0,
            "entry_price": 10.0, "notional_usdt": 10.0, "stop_loss": 9.5,
            "opened_at": _now().isoformat(), "status": "open"}
    base.update(cols)
    with journal._tx() as c:
        c.execute(f"INSERT INTO trades({','.join(base)}) VALUES "
                  f"({','.join('?' * len(base))})", tuple(base.values()))


@pytest.mark.parametrize("field,value", [("notional_usdt", None), ("notional_usdt", -5.0),
                                         ("notional_usdt", "abc"), ("notional_usdt", 0.0),
                                         ("stop_loss", "abc"), ("stop_loss", -1.0)])
def test_risk_malformed_book_is_never_a_pass(journal, monkeypatch, field, value):
    _open(journal, "OK1")
    _open(journal, "BAD", **{field: value})
    rec = _assess(_risk_kernel(journal, monkeypatch))
    heat = {c["name"]: c for c in rec["constraints"]}
    assert heat["portfolio_heat"]["result"] == "not_evaluated"
    assert heat["total_margin"]["result"] == "not_evaluated"
    assert rec["status"] == "DEGRADED" and f"book_malformed:BAD:{field}" in rec["reasons"]
    assert rec["book"]["malformed"] == [f"BAD:{field}"]


def test_risk_missing_stop_uses_the_existing_unprotected_formula(journal, monkeypatch):
    _open(journal, "NOSTOP", stop_loss=None, notional_usdt=100.0)
    rec = _assess(_risk_kernel(journal, monkeypatch))
    heat = next(c for c in rec["constraints"] if c["name"] == "portfolio_heat")
    assert heat["result"] == "pass" and heat["observed"] == pytest.approx(0.5)  # 5% of 100/1000


def test_risk_stale_equity_input_cannot_pass(journal, monkeypatch):
    def age(k):
        old = (_now() - timedelta(seconds=900)).isoformat()
        k._account_obs = dict(k._account_obs, observed_at=old)
    rec = _assess(_risk_kernel(journal, monkeypatch), before=age)
    assert rec["status"] == "DEGRADED"
    assert "equity_input_not_fresh_authoritative" in rec["reasons"]
    assert rec["equity"]["fresh_at_assessment"] is False


def test_risk_kernel_pass_record_is_accepted_and_reproducible(journal, monkeypatch):
    rec = _assess(_risk_kernel(journal, monkeypatch))
    assert rec["status"] == "PASS" and rec["baseline"]["peak_equity"] == 1000.0
    r, err = current_truth.read_risk(journal, _now(), RISK_CFG)
    assert err is None and r["current"]["status"] == "PASS" and r["policy_match"] == "MATCH"


def _cons(**result_of):
    return [dict(c, result=result_of.get(c["name"], c["result"]))
            for c in risk_assessment(_now())["constraints"]]


@pytest.mark.parametrize("mutate,problem", [
    (lambda r: r["equity"].update(observed_at=(_now() + timedelta(minutes=1)).isoformat()),
     "equity_observed_after_assessment"),
    (lambda r: r["equity"].update(observed_at="garbage"), "equity_observed_at_malformed"),
    (lambda r: r["equity"].update(fresh_at_assessment=False),
     "pass_without_fresh_authoritative_equity"),
    (lambda r: r["equity"].update(authoritative=False),
     "pass_without_fresh_authoritative_equity"),
    (lambda r: r["equity"].update(value=None), "pass_without_fresh_authoritative_equity"),
    (lambda r: r.update(constraints=_cons(portfolio_heat="not_evaluated")),
     "pass_with_unevaluated_constraint"),
    (lambda r: r.update(constraints=_cons()[:-1]), "constraints_incomplete_or_duplicated"),
    (lambda r: r.update(constraints=_cons() + [_cons()[0]]),
     "constraints_incomplete_or_duplicated"),
    (lambda r: r.update(constraints=_cons(halt_drawdown="maybe")),
     "constraint_result_malformed:halt_drawdown"),
    (lambda r: r["constraints"][4].update(observed="x"),
     "constraint_values_malformed:portfolio_heat"),
    (lambda r: r.update(drawdown_pct=0.0), "drawdown_not_reproducible"),
    (lambda r: r.update(daily_pnl_pct=3.0), "daily_pnl_not_reproducible"),
    (lambda r: r["baseline"].update(peak_equity=None), "baseline_or_equity_malformed"),
    (lambda r: r["policy"]["effective"].update(max_positions=99), "policy_digest_mismatch"),
    (lambda r: r["policy"].update(digest="abc"), "policy_identity_missing"),
    (lambda r: r.update(status="DEGRADED", constraints=_cons(halt_drawdown="block")),
     "degraded_with_blocking_constraint"),
])
def test_risk_reader_rejects_unproven_pass(journal, mutate, problem):
    rec = risk_assessment(_now() - timedelta(seconds=5))
    mutate(rec)
    _kv(journal, "risk_assessment", rec)
    r, err = current_truth.read_risk(journal, _now())
    assert r["current"]["status"] == "UNAVAILABLE" and problem in err


def test_risk_reader_pass_needs_its_input_fresh_now(journal):
    rec = risk_assessment(_now() - timedelta(seconds=10))
    rec["equity"]["observed_at"] = (_now() - timedelta(seconds=400)).isoformat()
    _kv(journal, "risk_assessment", rec)
    cur = current_truth.read_risk(journal, _now())[0]["current"]
    assert cur["status"] == "STALE" and cur["last_reported"] == "PASS"
    assert "equity_source_stale" in cur["reasons"] and cur["drawdown_pct"] is None


# ── 4. account provenance ──────────────────────────────────────────────────
def test_equal_value_is_not_provenance(journal, monkeypatch):
    """A legacy row with the same value as a previous venue read stays unknown."""
    k = _kernel(journal, monkeypatch, [(1000.0, "venue_total_margin_balance", []),
                                       (None, None, ["venue_account_failed:X"])])
    k._risk_step()
    journal.log_equity(1000.0, 1000.0, 0)                    # no provenance: legacy writer
    k._risk_step()
    o = _obs(journal)
    assert o["status"] == "JOURNAL_FALLBACK" and o["observed_at"] is None
    assert o["fallback"]["row_provenance"] == "none"
    assert "fallback_row_provenance_unknown" in o["reason"]
    a, _ = current_truth.read_account(journal, _now())
    assert a["freshness"] == "unavailable"


def test_provenance_for_another_value_is_not_this_rows(journal, monkeypatch):
    k = _kernel(journal, monkeypatch, [(1000.0, "venue_total_margin_balance", []),
                                       (None, None, [])])
    k._risk_step()
    journal.log_equity(1000.0, 1000.0, 0, provenance={
        "value": 999.0, "observed_at": _now().isoformat(), "basis": "x",
        "row_kind": "venue_observation"})
    k._risk_step()
    assert _obs(journal)["observed_at"] is None


def test_log_equity_without_provenance_clears_a_stale_one(journal):
    journal.log_equity(1.0, 1.0, 0, provenance={"value": 1.0, "observed_at": "x"})
    journal.log_equity(2.0, 2.0, 0)                          # same second, no provenance
    assert journal.query("SELECT COUNT(*) n FROM equity_provenance")[0]["n"] == 0


def test_successful_read_is_timed_at_completion(journal, monkeypatch):
    k = _kernel(journal, monkeypatch, [])
    t0 = time.time()

    def slow_read():
        time.sleep(0.05)
        k._balance_read = {"basis": "venue_total_margin_balance", "errors": [],
                           "completed_at": time.time()}
        return 1000.0
    monkeypatch.setattr(k, "_fetch_balance_fresh", slow_read, raising=False)
    k._risk_step()
    o = _obs(journal)
    att, obs = datetime.fromisoformat(o["attempted_at"]), datetime.fromisoformat(o["observed_at"])
    assert (obs - att).total_seconds() >= 0.05 and att.timestamp() >= t0 - 1


def test_real_balance_paths_record_basis_and_completion(journal, monkeypatch):
    from trader.kernel import Kernel
    import requests
    from trader.core import config
    k = object.__new__(Kernel)
    monkeypatch.setattr(config.Env, "binance_keys", staticmethod(lambda: ("k", "s")))
    monkeypatch.setattr(requests, "get", lambda *a, **kw: NS(
        json=lambda: {"totalMarginBalance": "1234.5"}))
    k.exchange = NS(id="binanceusdm", apiKey="k", urls={"api": {"fapiPrivateV3": "https://demo-fapi.binance.com/fapi/v3"}},
                    fetch_balance=lambda: {"USDT": {"total": 900}})
    t0 = time.time()
    assert k._fetch_balance_fresh() == 1234.5
    assert k._balance_read["basis"] == "venue_total_margin_balance"
    assert k._balance_read["completed_at"] >= t0
    monkeypatch.setattr(requests, "get", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
    assert k._fetch_balance_fresh() == 900.0
    assert k._balance_read["basis"] == "venue_wallet_usdt_total"
    assert k._balance_read["errors"] == ["venue_account_failed:OSError"]


@pytest.mark.parametrize("over,problem", [
    ({"observed_at": "2020-06-01T00:00:00+00:00",
      "successful_read_at": "2020-06-01T00:00:00+00:00", "completion_relation": "ok"},
     "completion_relation_inconsistent"),
    ({"observed_at": None, "successful_read_at": None, "completion_relation": "ok"},
     "fresh_read_without_completion_reason"),
    ({"authoritative": False}, "authoritative_contradicts_status"),
    ({"recorded_at": None}, "recorded_at_missing"),
    ({"status": "JOURNAL_FALLBACK", "authoritative": True}, "authoritative_contradicts_status"),
    ({"status": "UNAVAILABLE", "authoritative": False, "value": None},
     "unavailable_with_source_time"),
    ({"status": "JOURNAL_FALLBACK", "authoritative": False, "fallback": None},
     "fallback_row_missing"),
])
def test_account_reader_temporal_and_status_consistency(journal, over, problem):
    rec = account_observation(_now() - timedelta(seconds=5))
    rec.update(over)
    _kv(journal, "account_observation", rec)
    a, err = current_truth.read_account(journal, _now())
    assert a is None and problem in err


def test_fallback_success_after_attempt_is_impossible(journal):
    t = _now() - timedelta(seconds=30)
    rec = account_observation(t, status="JOURNAL_FALLBACK", authoritative=False,
                              observed_at=(t - timedelta(minutes=5)).isoformat(),
                              successful_read_at=(t + timedelta(seconds=1)).isoformat(),
                              recorded_at=(t + timedelta(seconds=2)).isoformat(),
                              fallback={"row_written_at": t.isoformat()})
    _kv(journal, "account_observation", rec)
    assert "successful_read_at_after_attempt" in current_truth.read_account(journal, _now())[1]


def test_series_and_graphql_expose_row_vs_source_time(journal, monkeypatch):
    k = _kernel(journal, monkeypatch, [(1000.0, "venue_total_margin_balance", []),
                                       (None, None, [])])
    k._risk_step()
    journal.log_equity(1000.0, 1000.0, 0, provenance=k._equity_provenance(1000.0))
    src = _obs(journal)["observed_at"]
    time.sleep(1.1)
    b, _ = k._risk_step()
    journal.log_equity(b, b, 0, provenance=k._equity_provenance(b))
    series, _ = owner_api.read_equity_series(journal, _now())
    kinds = [(p["row_kind"], p["source_observed_at"]) for p in series["points"]]
    assert kinds[-1] == ("fallback_reuse", src)
    from tests.test_current_truth_contract import _gql
    pts = _gql(journal, "{ equity_curve { ts row_kind source_observed_at } }")[
        "data"]["equity_curve"]
    assert [p["row_kind"] for p in pts] == ["venue_observation", "fallback_reuse"]
    assert pts[1]["source_observed_at"] == src and pts[1]["ts"] != src
    journal.log_equity(5.0, 5.0, 0)
    assert _gql(journal, "{ equity_curve { row_kind } }")["data"]["equity_curve"][-1] == \
        {"row_kind": "unknown"}


# ── 6. legacy marks provider has no provenance ────────────────────────────
def test_marks_without_quote_times_publish_nothing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.owner_frontend_fixture import TOKEN, make_app
    from trader.dashboard import server
    make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_position_quotes", None)
    app = server.create_app({"dashboard": {"startup_mode": "LIVE"},
                             "attention": {"enabled": False},
                             "owner_interface": {"enabled": True}})
    m = TestClient(app).get("/owner-api/v1/enrichment/marks",
                            headers={"x-luffy-token": TOKEN}).json()
    assert m["observed_at"] is None and m["marks"] == {} and m["total_upnl"] is None
    assert m["error"] == "marks_provenance_unavailable"
    assert m["unvalued"] == {"BTC/USDT": ["quote_time_unrecorded"]}


# ── 7. valuation ───────────────────────────────────────────────────────────
def test_zero_quantity_is_a_real_zero(journal):
    v = valuation.estimate([_trade("z", "BTC/USDT", "long", 0.0, 100.0)],
                           {"BTC/USDT": _q(150.0)})
    assert v["status"] == "COMPLETE" and v["total_upnl"] == 0.0
    assert v["positions"][0]["zero_quantity"] is True


def test_duplicate_symbol_is_valued_per_trade():
    v = valuation.estimate([_trade("a", "BTC/USDT", "long", 1.0, 100.0),
                            _trade("b", "BTC/USDT", "short", 2.0, 120.0)],
                           {"BTC/USDT": _q(110.0)})
    assert [p["upnl_estimate"] for p in v["positions"]] == [pytest.approx(10.0),
                                                            pytest.approx(20.0)]
    assert [p["signed_quantity"] for p in v["positions"]] == [1.0, -2.0]
    assert v["total_upnl"] == pytest.approx(30.0) and v["coverage"]["positions"] == 2


def test_aggregate_overflow_is_unavailable_not_infinity():
    big = 1e308
    v = valuation.estimate([_trade("a", "BTC/USDT", "long", 1.0, 1.0),
                            _trade("b", "ETH/USDT", "long", 1.0, 1.0)],
                           {"BTC/USDT": _q(big), "ETH/USDT": _q(big)})
    assert all(p["status"] == "OK" for p in v["positions"])
    assert v["status"] == "UNAVAILABLE" and v["total_upnl"] is None
    assert v["reasons"] == ["aggregate_not_finite"]
    assert json.dumps(v, allow_nan=False)


@pytest.mark.parametrize("make,reason", [
    (lambda n: {"source_ms": n * 1000, "received_at": None}, "quote_receipt_missing"),
    (lambda n: {"source_ms": n * 1000, "received_at": None},
     "quote_source_receipt_relation_unproven"),
    (lambda n: {"source_ms": None, "received_at": "garbage"}, "quote_receipt_malformed"),
    (lambda n: {"source_ms": None, "received_at": n + 30}, "quote_receipt_in_future"),
    (lambda n: {"source_ms": "abc", "received_at": n}, "quote_source_malformed"),
])
def test_quote_time_provenance_is_required(make, reason):
    quote = {"price": 1.0, "field": "last", **make(time.time())}   # times at run time
    v = valuation.estimate([_trade("a", "BTC/USDT", "long", 1.0, 1.0)], {"BTC/USDT": quote})
    assert v["status"] == "UNAVAILABLE" and reason in v["positions"][0]["reasons"]
    assert v["positions"][0]["quote"]["freshness"] != "fresh"


def test_quote_one_millisecond_after_receipt_is_invalid():
    now = time.time()
    v = valuation.estimate([_trade("a", "BTC/USDT", "long", 1.0, 1.0)],
                           {"BTC/USDT": _q(2.0, age=1.0, source_ms=(now - 1.0) * 1000 + 1,
                                           now=now)})
    assert "quote_time_after_receipt" in v["positions"][0]["reasons"]


# ── 8. policy identity ─────────────────────────────────────────────────────
def test_policy_identity_is_exact_not_rounded():
    a = policy_from_config(RISK_FIXTURE_CFG)
    tweak = {"risk": dict(RISK_FIXTURE_CFG["risk"], portfolio_heat_cap_pct=15.0000000001)}
    b = policy_from_config(tweak)
    assert a["limits"]["portfolio_heat_cap_pct"] == b["limits"]["portfolio_heat_cap_pct"]
    assert a["digest"] != b["digest"]                        # display collapses, identity not
    assert policy_from_config(RISK_FIXTURE_CFG)["digest"] == a["digest"]   # stable
    assert current_truth._policy_digest(a["effective"]) == a["digest"]


@pytest.mark.parametrize("key,value", [("portfolio_heat_cap_pct", float("nan")),
                                       ("max_daily_loss_pct", float("inf")),
                                       ("halt_drawdown_pct", "-inf")])
def test_non_finite_policy_has_no_identity(key, value):
    cfg = {"risk": dict(RISK_FIXTURE_CFG["risk"], **{key: value})}
    with pytest.raises(ValueError):
        policy_from_config(cfg)
    conf, err = current_truth.configured_policy(cfg)
    assert conf is None and err == "risk_config_unreadable:ValueError"


def test_running_manager_identity_equals_config_identity(journal):
    rm = RiskManager(RISK_FIXTURE_CFG, journal)
    assert rm.policy()["effective"] == policy_from_config(RISK_FIXTURE_CFG)["effective"]
    assert rm.policy()["digest"] == policy_from_config(RISK_FIXTURE_CFG)["digest"]


# ══════════════ FINAL OPUS CORRECTIONS: quote timing, /news, cockpit, marks ══════
def _feed_with(ticker):
    """A DataFeed whose market-data exchange and time.time are scripted."""
    from trader.data.feed import DataFeed
    # Exercise timing with a fully initialized, identity-bearing fake venue.
    # DA-01 requires identity as well as price; no transport is constructed.
    venue = NS(id='binanceusdm', urls={'api': 'https://offline.invalid'},
               market=lambda symbol: {'id': symbol.replace('/', ''), 'contract': True},
               fetch_ticker=ticker)
    return DataFeed(exchange=venue)


def test_ticker_attempt_precedes_fetch_and_receipt_follows_it(monkeypatch):
    from trader.data import feed as feed_mod
    events, clock = [], iter([1_790_000_000.0, 1_790_000_002.5])

    def tick():
        t = next(clock)
        events.append(("clock", t))
        return t

    def fetch(sym):
        events.append(("fetch", sym))
        return {"last": 7.0, "timestamp": 1_789_999_999_000}
    monkeypatch.setattr(feed_mod.time, "time", tick)
    q = _feed_with(fetch).ticker_quote("X/USDT")
    assert [e[0] for e in events] == ["clock", "fetch", "clock"]   # attempt, fetch, receipt
    assert q["attempt_started_at"] == 1_790_000_000.0 and q["received_at"] == 1_790_000_002.5
    assert q["attempt_started_at"] <= q["received_at"]
    assert q["source_ms"] == 1_789_999_999_000 and q["price"] == 7.0 and q["error"] is None


def test_ticker_failure_has_no_receipt(monkeypatch):
    from trader.data import feed as feed_mod
    monkeypatch.setattr(feed_mod.time, "time", lambda: 1_790_000_000.0)
    q = _feed_with(lambda s: (_ for _ in ()).throw(TimeoutError("t"))) \
        .ticker_quote("X/USDT")
    assert q["received_at"] is None and q["attempt_started_at"] == 1_790_000_000.0
    assert q["price"] is None and q["error"] == "ticker_fetch_failed:TimeoutError"
    v = valuation.estimate([_trade("a", "X/USDT", "long", 1.0, 1.0)], {"X/USDT": q})
    assert v["status"] == "UNAVAILABLE" and "quote_receipt_missing" in v["positions"][0]["reasons"]


def test_ticker_receipt_before_attempt_fails_closed(monkeypatch):
    from trader.data import feed as feed_mod
    clock = iter([1_790_000_010.0, 1_790_000_000.0])              # clock stepped back
    monkeypatch.setattr(feed_mod.time, "time", lambda: next(clock))
    q = _feed_with(lambda s: {"last": 7.0}).ticker_quote("X/USDT")
    assert q["price"] is None and q["error"] == "quote_receipt_before_attempt"


def test_valuation_refuses_receipt_before_attempt_and_keeps_source_independent():
    now = time.time()
    base = {"price": 2.0, "field": "last", "source_ms": (now - 5) * 1000,
            "received_at": now - 1}
    ok = valuation.estimate([_trade("a", "BTC/USDT", "long", 1.0, 1.0)],
                            {"BTC/USDT": {**base, "attempt_started_at": now - 2}})
    q = ok["positions"][0]["quote"]
    assert ok["status"] == "COMPLETE" and q["time_basis"] == "ticker_timestamp"
    assert q["age_s"] == pytest.approx(5, abs=1)                    # source, not receipt
    for att, reason in ((now - 0.5, "quote_receipt_before_attempt"),
                        ("garbage", "quote_attempt_malformed")):
        bad = valuation.estimate([_trade("a", "BTC/USDT", "long", 1.0, 1.0)],
                                 {"BTC/USDT": {**base, "attempt_started_at": att}})
        assert bad["status"] == "UNAVAILABLE" and reason in bad["positions"][0]["reasons"]
    stale = valuation.estimate([_trade("a", "BTC/USDT", "long", 1.0, 1.0)],
                               {"BTC/USDT": {**base, "source_ms": (now - 600) * 1000,
                                             "attempt_started_at": now - 2}})
    assert "quote_stale" in stale["positions"][0]["reasons"]
    after = valuation.estimate([_trade("a", "BTC/USDT", "long", 1.0, 1.0)],
                               {"BTC/USDT": {**base, "source_ms": (now - 0.5) * 1000,
                                             "attempt_started_at": now - 2}})
    assert "quote_time_after_receipt" in after["positions"][0]["reasons"]


def test_position_quotes_use_a_keyless_exchange(monkeypatch, journal):
    from trader.dashboard import server
    from trader.data import feed as feed_mod
    seen = []
    monkeypatch.delattr(server, "_quotes_feed", raising=False)
    monkeypatch.setattr(feed_mod, "make_exchange",
                        lambda *a, **kw: seen.append((a, kw)) or NS())
    monkeypatch.setattr(feed_mod, "DataFeed", lambda ex: NS(ticker_quote=lambda s: {}))
    server._position_quotes(journal)
    assert seen == [(("futures",), {"with_keys": False})]
    monkeypatch.delattr(server, "_quotes_feed", raising=False)


# ── /news icon ─────────────────────────────────────────────────────────────


# ── company cockpit Breaker ────────────────────────────────────────────────


# ── /enrichment/marks per position ─────────────────────────────────────────
def test_marks_duplicate_symbol_never_overwrites_a_position(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.owner_frontend_fixture import TOKEN, make_app
    from trader.dashboard import server
    make_app(tmp_path, monkeypatch)
    trades = [_trade("a", "BTC/USDT", "long", 1.0, 100.0),
              _trade("b", "BTC/USDT", "short", 2.0, 120.0),
              _trade("c", "ETH/USDT", "long", 1.0, 10.0)]
    monkeypatch.setattr(server, "_position_quotes", lambda j: (
        trades, {"BTC/USDT": dict(_q(110.0), attempt_started_at=time.time() - 2),
                 "ETH/USDT": _q(12.0)}))
    app = server.create_app({"dashboard": {"startup_mode": "LIVE"},
                             "attention": {"enabled": False},
                             "owner_interface": {"enabled": True}})
    m = TestClient(app).get("/owner-api/v1/enrichment/marks",
                            headers={"x-luffy-token": TOKEN}).json()
    assert m["by_trade"]["a"]["upnl"] == pytest.approx(10.0)
    assert m["by_trade"]["b"]["upnl"] == pytest.approx(20.0)
    assert m["by_trade"]["c"]["upnl"] == pytest.approx(2.0)
    assert m["marks"]["BTC/USDT"]["mark"] == 110.0 and m["marks"]["BTC/USDT"]["upnl"] is None
    assert m["marks"]["BTC/USDT"]["trade_ids"] == ["a", "b"]
    assert m["marks"]["ETH/USDT"]["upnl"] == pytest.approx(2.0)   # single: unchanged shape
    assert m["total_upnl"] == pytest.approx(32.0) and m["valuation_status"] == "COMPLETE"


def test_marks_duplicate_symbol_with_one_bad_trade_is_unvalued(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.owner_frontend_fixture import TOKEN, make_app
    from trader.dashboard import server
    make_app(tmp_path, monkeypatch)
    trades = [_trade("a", "BTC/USDT", "long", 1.0, 100.0),
              _trade("b", "BTC/USDT", "sideways", 2.0, 120.0)]
    monkeypatch.setattr(server, "_position_quotes",
                        lambda j: (trades, {"BTC/USDT": _q(110.0)}))
    app = server.create_app({"dashboard": {"startup_mode": "LIVE"},
                             "attention": {"enabled": False},
                             "owner_interface": {"enabled": True}})
    m = TestClient(app).get("/owner-api/v1/enrichment/marks",
                            headers={"x-luffy-token": TOKEN}).json()
    assert "BTC/USDT" not in m["marks"] and m["unvalued"]["BTC/USDT"] == ["side_malformed"]
    assert m["by_trade"]["a"]["status"] == "OK" and m["by_trade"]["b"]["mark"] is None
    assert m["total_upnl"] is None
