"""Live-contract fixture for the owner frontend: a temporary ROOT with a seeded
journal, heartbeat and vault, a fake Owner Interface gateway and a fake chat
engine. Used by tests/test_owner_frontend_api.py and by the browser test server
(tests/owner_frontend_server.py). Never touches the real data/ or IPC socket.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trader.owner.contract import OwnerResult, Status, refused

TOKEN = "fixture-token"

NOTES = {
    "10 Theories/Mechanisms/Aggressor Flow.md": """---
type: market-mechanism
status: active
claim: Aggressive flow imbalance can reveal short-term pressure.
relations:
  supports:
    - Absorption Test
  contradicts:
    - Missing Concept
  works_in:
    - Liquid Active Market
---

# Aggressor Flow

See [[Absorption Test]].
""",
    "10 Theories/Mechanisms/Absorption Test.md": """---
type: market-mechanism
status: active
claim: Passive liquidity absorbs aggressive flow.
relations:
  fails_in:
    - Liquid Active Market
---

# Absorption Test
""",
    "40 Regimes/Liquid Active Market.md": """---
type: regime
status: active
---

# Liquid Active Market
""",
    "MOC.md": "# Map\n\n[[Aggressor Flow]] and [[Nowhere Note]].\n",
}


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _venue_symbol(sym, side, qty, px):
    stop = round(px * 0.97, 2)
    return {"symbol": sym, "venue_symbol": sym + ":USDT", "side": side, "quantity": qty,
            "entry_price": px, "journal_trade_id": "t-" + sym[:3].lower(),
            "journal_side": side, "journal_amount": qty, "quantity_agrees": True,
            "journal_stop_id": "algo-1",
            "expected_stop": stop, "stop_present": True, "stop_ids": ["algo-1"],
            "stop_id": "algo-1", "stop_side": "sell" if side == "long" else "buy",
            "reduce_only": True, "order_type": "STOP_MARKET", "stop_kind": "algo",
            "covered_quantity": qty, "trigger_price": stop, "tick_size": "0.01",
            "precision_status": "VALID", "precision_valid": True,
            "stop_id_matches_journal": True, "match_reason": "matched",
            "rearm_evidence": "NONE", "verified": True, "reasons": []}


def protection_snapshot(at, **over) -> dict:
    """A kernel protection snapshot (schema 2) of the fixture's two protected positions."""
    snap = {"schema": 2, "generation": {"boot": 1, "seq": 1},
            "source": "kernel protection monitor (read-only venue snapshot)",
            "checked_at": at.isoformat(), "completed_at": at.isoformat(),
            "stale_after_s": 120.0, "status": "VERIFIED",
            "checks": {"venue_positions": True, "observation_consistent": True,
                       "reconciliation": True, "venue_protection": True,
                       "precision_known": True},
            "complete_listing": True, "listing_reason": None,
            "observation": {"positions_stable": True, "journal_stable": True,
                            "supervisor_pass_overlap": False, "window_ms": 310.0},
            "control_state_observed": "FROZEN", "position_count": 2,
            "symbols": [_venue_symbol("BTC/USDT", "long", 0.01, 60000.0),
                        _venue_symbol("ETH/USDT", "short", 0.5, 3000.0)],
            "cleanliness": {"status": "CLEAN", "coverage": "position_symbols", "items": [],
                            "items_truncated": False, "unread_symbols": []},
            "reasons": [], "venue_requests": 5, "venue_request_names": [],
            "rate": None, "duration_ms": 412.0, "mutations": 0}
    snap.update(over)
    return snap


def store_protection_snapshot(conn, snap: dict) -> None:
    """Write the one evidence row as the kernel's publisher would (same schema)."""
    from trader.engine.protection_snapshot import BOOTS, TABLE, _SCHEMA_SQL
    for stmt in _SCHEMA_SQL:
        conn.execute(stmt)
    conn.execute(f"DELETE FROM {TABLE}")
    conn.execute(f"INSERT OR IGNORE INTO {BOOTS}(boot, started_at, pid) VALUES (?, 'fixture', 0)",
                 (snap["generation"]["boot"],))
    conn.execute(f"INSERT INTO {TABLE}(slot, boot, seq, value) VALUES (1, ?, ?, ?)",
                 (snap["generation"]["boot"], snap["generation"]["seq"], json.dumps(snap)))


def add_history(journal, n: int, *, start_days: float = 3.0) -> None:
    """n older closed trades (H0000/USDT newest … oldest), one per hour."""
    base = datetime.now(timezone.utc) - timedelta(days=start_days)
    with journal._tx() as c:
        for i in range(n):
            opened = base - timedelta(hours=i)
            c.execute("INSERT INTO trades(id,decision_id,symbol,side,amount,entry_price,"
                      "exit_price,notional_usdt,leverage,strategy_id,strategy_name,exec_mode,"
                      "opened_at,closed_at,realized_pnl,close_reason,status) VALUES "
                      "(?,?,?,'long',1,10,11,10,2,'spec:donchian','spec:donchian','live',?,?,"
                      "1.0,'trail','closed')",
                      (f"h-{i:04d}", f"d-{i:04d}", f"H{i:04d}/USDT", _iso(opened),
                       _iso(opened + timedelta(minutes=30))))


def insert_trade(journal, trade_id: str, symbol: str, opened_at: str,
                 status: str = "open") -> None:
    with journal._tx() as c:
        c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,notional_usdt,leverage,"
                  "strategy_name,exec_mode,opened_at,status) VALUES "
                  "(?,?,'long',1,10,10,2,'spec:donchian','live',?,?)",
                  (trade_id, symbol, opened_at, status))


def reopen_trade(journal, trade_id: str) -> None:
    with journal._tx() as c:
        c.execute("UPDATE trades SET status='open', closed_at=NULL WHERE id=?", (trade_id,))


def close_trade(journal, trade_id: str) -> None:
    with journal._tx() as c:
        c.execute("UPDATE trades SET status='closed', closed_at=?, realized_pnl=-3.25, "
                  "close_reason='fixture close' WHERE id=?",
                  (_iso(datetime.now(timezone.utc)), trade_id))


def account_observation(at, value=5164.5, **over) -> dict:
    """A kernel _risk_step account_observation record (fresh venue read at `at`)."""
    t = at.isoformat()
    return {"schema": 2, "currency": "USDT", "attempted_at": t, "recorded_at": t,
            "risk_input": value, "authoritative": True, "attempt_errors": [],
            "source": "fixture", "status": "FRESH", "value": value,
            "basis": "venue_total_margin_balance", "observed_at": t,
            "successful_read_at": t, "successful_value": value,
            "successful_basis": "venue_total_margin_balance", "fallback": None,
            "consecutive_failures": 0, "completion_relation": "ok", "reason": None,
            **over}


def news_guard_state(at, truth="QUIET", **over) -> dict:
    """A NewsGuard record as the kernel publishes it (QUIET or ARMED)."""
    t = at.isoformat()
    armed = truth == "ARMED"
    rec = {"active": armed, "why": "2 impact headlines (1 severe)" if armed else "feed quiet",
           "truth": truth, "attempted_at": t, "fetched_at": t, "assessed_at": t,
           "succeeded_at": t, "newest_publication_at": t, "oldest_publication_at": t,
           "hits": 2 if armed else 0, "severe": 1 if armed else 0,
           "dated_hits": 2 if armed else 0, "dated_severe": 1 if armed else 0,
           "headlines": ["[severe] Exchange hacked"] if armed else [],
           "items_total": 5, "items_considered": 5, "dated_in_window": 5,
           "undated_items": 0, "malformed_dates": 0, "future_dated_items": 0,
           "failure_codes": [], "failures": [], "min_headlines": 2, "window_hours": 3.0,
           "refresh_s": 600.0, "stale_after_s": 1260.0,
           "dampening": {"applied": armed, "threshold_add": 0.08, "score_mult": 0.75,
                         "enforcement": "dampen_only"},
           "ts": t, "published_at": t}
    rec.update(over)
    return rec


def risk_assessment(at, status="PASS", control="FROZEN", **over) -> dict:
    """A kernel cycle risk_assessment record (RISK_FIXTURE_CFG limits)."""
    from trader.engine.risk import policy_from_config
    pol = policy_from_config(RISK_FIXTURE_CFG)
    blocks = status == "BLOCK"
    eq, peak, day = 5164.5, (6886.0 if blocks else 5227.2), 5185.2
    dd = round(max(0.0, (peak - eq) / peak) * 100, 2)
    dp = round((eq - day) / day * 100, 2)

    def con(name, limit, observed, result="pass", unit="pct"):
        return {"name": name, "limit": limit, "observed": observed, "result": result,
                "unit": unit, "basis": "fixture"}
    rec = {"schema": 2, "assessed_at": at.isoformat(), "status": status,
           "reasons": ["halt_drawdown_block"] if blocks else [],
           "constraints": [
               con("risk_baseline", "ok", "ok", unit="state"),
               con("halt_drawdown", 20.0, dd, "block" if blocks else "pass"),
               con("daily_loss_breaker", -6.0, dp),
               con("max_open_positions", 8, 2, unit="count"),
               con("portfolio_heat", 15.0, 1.5), con("total_margin", 70.0, 9.0),
               con("per_symbol_risk_cap", 8.0, None, "applies_at_entry"),
               con("per_position_margin", 20.0, None, "applies_at_entry")],
           "risk_state": "ok", "drawdown_pct": dd, "daily_pnl_pct": dp,
           "halt_breached": blocks,
           "baseline": {"peak_equity": peak, "day_start_equity": day, "day_key": "d"},
           "equity": {"value": eq, "status": "FRESH", "basis": "venue_total_margin_balance",
                      "observed_at": at.isoformat(), "age_at_assessment_s": 0.0,
                      "fresh_at_assessment": True, "authoritative": True},
           "book": {"source": "journal open trades", "read_at": at.isoformat(),
                    "positions": 2, "malformed": []},
           "policy": {"digest": pol["digest"], "risk_manager_identity": "fixture",
                      "effective": pol["effective"], "limits": pol["limits"]},
           "control": {"state": control, "entries_permitted_by_control": control == "ACTIVE",
                       "applicability": "fixture"},
           "entry_gate": {"allowed": control == "ACTIVE", "blocked_reason":
                          None if control == "ACTIVE" else f"state={control}",
                          "basis": "fixture"},
           "source": "fixture"}
    rec.update(over)
    return rec


RISK_FIXTURE_CFG = {"risk": {"risk_per_trade_pct": 1.0, "portfolio_heat_cap_pct": 15,
                             "per_symbol_risk_cap_pct": 8, "max_open_positions": 8,
                             "max_daily_loss_pct": 6, "halt_drawdown_pct": 20,
                             "leverage": 5, "stop_loss_atr_mult": 2.0,
                             "min_notional_usdt": 5}}


def seed_truth(c, now, scenario: str = "normal") -> None:
    """The kernel's current-truth records: account observation, News Guard
    and Risk assessment (stale in the stale scenario)."""
    age = timedelta(hours=1) if scenario == "stale" else timedelta(seconds=20)
    for key, rec in (("account_observation", account_observation(now - age)),
                     ("news_guard_state", news_guard_state(now - age)),
                     ("risk_assessment", risk_assessment(now - age))):
        c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES(?,?)",
                  (key, json.dumps(rec)))


def seed(root: Path, journal, scenario: str = "normal") -> None:
    """Rewrite the fixture state for one scenario."""
    now = datetime.now(timezone.utc)
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    with journal._tx() as c:
        for t in ("outcomes", "trade_accounting_bookings", "votes", "equity", "trades",
                  "control_events",
                  "state_kv", "decisions", "cycles", "brain_events", "strategies"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DROP TABLE IF EXISTS protection_evidence")
    from trader.research.ledger import Ledger
    Ledger(journal).ensure()
    with journal._tx() as c:
        for t in ("research_combos", "research_batches", "research_candidates",
                  "research_tests"):
            c.execute(f"DELETE FROM {t}")
    for rel in M2_NOTES:                # seed_m2's notes do not outlive a reset
        (root / "knowledge" / rel).unlink(missing_ok=True)
    hb = data / "heartbeat_luffy.json"
    if scenario == "missing":
        hb.unlink(missing_ok=True)
        return
    age = {"normal": 20, "stale": 900}.get(scenario, 20)
    hb.write_text(json.dumps({"timestamp": time.time() - age, "state": "FROZEN"}))
    # closed "today (UTC)" even just after midnight, so realized-today stays 2.0
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    closed = max(now - timedelta(hours=1), midnight + (now - midnight) / 2)
    with journal._tx() as c:
        c.execute("INSERT INTO state_kv(key,value) VALUES('control_state','FROZEN')")
        c.execute("INSERT INTO control_events(ts,event,from_state,to_state,actor,detail) "
                  "VALUES(?, 'state_change','ACTIVE','FROZEN','operator','{}')",
                  (_iso(now - timedelta(hours=3)),))
        for i in range(48):
            ts = now - timedelta(hours=47 - i, minutes=1)
            c.execute("INSERT INTO equity VALUES (?,?,?,?)",
                      (_iso(ts), 5000 + i * 3.5, 4990.0, 2))
        if scenario == "stale":
            c.execute("UPDATE equity SET ts=? WHERE ts=(SELECT MAX(ts) FROM equity)",
                      (_iso(now - timedelta(hours=1)),))
        seed_truth(c, now, scenario)
        for tid, sym, side, amt, px, opened in (
                ("t-btc", "BTC/USDT", "long", 0.01, 60000.0, now - timedelta(hours=6)),
                ("t-eth", "ETH/USDT", "short", 0.5, 3000.0, now - timedelta(hours=5))):
            c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,notional_usdt,"
                      "leverage,stop_loss,sl_order_id,strategy_name,exec_mode,opened_at,status)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'open')",
                      (tid, sym, side, amt, px, amt * px, 3, px * 0.97, "algo-1",
                       "spec:donchian", "live", _iso(opened)))
        c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,exit_price,notional_usdt,"
                  "leverage,strategy_name,opened_at,closed_at,realized_pnl,close_reason,status)"
                  " VALUES('t-old','SOL/USDT','long',1,150,152,150,2,'spec:donchian',?,?,2.0,"
                  "'trail','closed')", (_iso(now - timedelta(days=2)), _iso(closed)))
        sup_age = {"normal": 30, "stale": 7200}.get(scenario, 30)
        c.execute("INSERT INTO state_kv(key,value) VALUES('supervisor_status',?)", (json.dumps({
            "control_state_observed": "FROZEN", "outcome": "SAFE", "stage": "COMPLETE",
            "safe_to_activate": True, "needs_owner": scenario == "stale",
            "reasons": ["owner_resume_required"] if scenario == "stale" else [],
            "checks": {"venue_positions": True, "reconciliation": True,
                       "entry_intent_resolved": True, "venue_protection": True,
                       "entries_safe": True},
            "updated_at": (now - timedelta(seconds=sup_age)).isoformat()}),))
        snap = protection_snapshot(now - timedelta(seconds=sup_age))
        if scenario == "protection_unreadable":
            snap = protection_snapshot(
                now - timedelta(seconds=sup_age), status="UNREADABLE", symbols=[],
                position_count=None, complete_listing=False, reasons=["venue_timeout"],
                checks={"venue_positions": False, "reconciliation": False,
                        "venue_protection": False})
        elif scenario == "protection_partial":
            eth = dict(snap["symbols"][1], stop_present=False, verified=False, stop_id=None,
                       reasons=["position_unprotected:ETH/USDT"])
            snap = protection_snapshot(
                now - timedelta(seconds=sup_age), status="PARTIAL",
                symbols=[snap["symbols"][0], eth], reasons=["position_unprotected:ETH/USDT"],
                checks={"venue_positions": True, "reconciliation": True,
                        "venue_protection": False})
        if scenario != "protection_missing":
            store_protection_snapshot(c, snap)
        else:
            c.execute("DROP TABLE IF EXISTS protection_evidence")
        c.execute("INSERT INTO cycles(id,ts,symbol) VALUES('c1',?,'BTC/USDT')",
                  (_iso(now - timedelta(minutes=2)),))
        c.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,executed)"
                  " VALUES('d1','c1',?,'BTC/USDT','HOLD',0,0.5,0,0)", (_iso(now - timedelta(minutes=2)),))
        c.execute("INSERT INTO strategies(id,name,kind,params,state,origin,created_at) "
                  "VALUES('s1','Donchian','spec','{}','active','analyst',?)", (_iso(now),))
        c.execute("INSERT INTO votes(cycle_id,ts,symbol,agent,side,conviction,confidence,"
                  "rationale,meta) VALUES('c1',?,'BTC/USDT','trend','long',0.4,0.6,"
                  "'fixture vote','{}')", (_iso(now - timedelta(minutes=2)),))
        # recorded lineage for t-btc: decision, strategy and one booking receipt
        c.execute("UPDATE trades SET decision_id='d1', strategy_id='s1' WHERE id='t-btc'")
        from trader.engine import booking
        booking.persist(c, "t-btc", "entry", None)
        c.execute("INSERT INTO research_combos(hash,tf,geo,k,round,label,status,verdict,reason,"
                  "median_pf,trades,created_at) VALUES('h-fixture','4h','trail',1,'singles',"
                  "'donchian_hi(100)','scored','prune','fixture: below its window control',"
                  "1.04,37,?)", (_iso(now - timedelta(hours=1)),))
        c.execute("INSERT INTO research_batches(started,finished,tf,geo,round,n,ok,elapsed_s,"
                  "error) VALUES(?,?,'4h','trail','singles',5,1,8.4,'')",
                  (_iso(now - timedelta(hours=1)), _iso(now - timedelta(minutes=59))))
    vault = root / "knowledge"
    for rel, text in NOTES.items():
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "luffy.log").write_bytes(b"line one\n\x00line two\n")


M2_NOTES = {
    "30 Postmortems/Fixture Autopsy.md": """---
type: postmortem
day: 2026-09-20
---

# Fixture Autopsy

spec_fixture_trend opened pos_fixture01 from dec_fixture01; the search seeded
a1b2c3d4e5f60718. spec_nonexistent is not a registry id.
""",
    "20 Strategies/Fixture_Child.md": """---
type: strategy
family: ema_trend
state: paper
---

# Fixture Child

Named only by its title; no registry id is written here.
""",
}


def seed_m2(root: Path, journal) -> None:
    """M2 linked records on top of seed(): a strategy family, an executed and a
    skipped decision with votes, a closed trade, brain_events lifecycle and
    idea provenance, seeded research combos, cooldown events and state_kv.
    Includes traps: a vote of another cycle inside the vote window, a
    lifecycle event for a strategy not in the registry, and malformed JSON."""
    now = datetime.now(timezone.utc)
    at = lambda **kw: _iso(now - timedelta(**kw))        # noqa: E731
    spec = json.dumps({"timeframe": "1h", "direction": "long",
                       "provenance": {"source_kind": "harvested", "idea_id": "tv_fixture_1",
                                      "author": "Researcher"}})
    with journal._tx() as c:
        for t, col, ids in (
                ("strategies", "id", ("spec_fixture_trend", "strat_parent_fx",
                                      "strat_child_fx")),
                ("decisions", "id", ("dec_fixture01", "dec_fixture02")),
                ("cycles", "id", ("cyc_fixture01", "cyc_fixture02", "cyc_other01")),
                ("outcomes", "decision_id", ("dec_fixture01",)),
                ("trades", "id", ("pos_fixture01",)),
                ("research_combos", "hash", ("a1b2c3d4e5f60718", "b1b2c3d4e5f60718")),
                ("research_candidates", "hash", ("a1b2c3d4e5f60718",)),
                ("research_tests", "hash", ("a1b2c3d4e5f60718",))):
            c.execute(f"DELETE FROM {t} WHERE {col} IN ({','.join('?' * len(ids))})", ids)
        c.execute("DELETE FROM votes WHERE cycle_id IN ('cyc_fixture01','cyc_other01')")
        c.execute("INSERT INTO strategies(id,name,kind,params,state,origin,generation,parent_id,"
                  "created_at,state_changed_at,spec_json) VALUES('spec_fixture_trend',"
                  "'Fixture Trend Pullback','spec','{}','paper','harvested',0,'',?,?,?)",
                  (at(days=9), at(days=8), spec))
        c.execute("INSERT INTO strategies(id,name,kind,params,state,origin,generation,parent_id,"
                  "created_at) VALUES('strat_parent_fx','EMA Fixture Parent','ema_trend','{}',"
                  "'retired','seed',0,'',?)", (at(days=30),))
        c.execute("INSERT INTO strategies(id,name,kind,params,state,origin,generation,parent_id,"
                  "created_at) VALUES('strat_child_fx','EMA Fixture Child','ema_trend',"
                  "'{\"fast\": 20}','paper','mutation',1,'strat_parent_fx',?)", (at(days=20),))
        for cid, sym, mins in (("cyc_fixture01", "SOL/USDT", 300), ("cyc_fixture02", "ETH/USDT", 90),
                               ("cyc_other01", "SOL/USDT", 301)):
            c.execute("INSERT INTO cycles(id,ts,symbol,price,regime,adx,btc_trend,market_type,"
                      "mode) VALUES(?,?,?,150.0,'TRENDING_UP',31.5,'UP','futures','live')",
                      (cid, at(minutes=mins), sym))
        sig = json.dumps([{"strategy_id": "spec_fixture_trend", "strategy_name":
                           "Fixture Trend Pullback", "symbol": "SOL/USDT", "action": "BUY",
                           "confidence": 0.6, "rationale": "close > donchian_hi(100)",
                           "params": {"signal_bar_age_min": 4.2}}])
        c.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,"
                  "executed,strategy_ids,signals_json,scan_id) VALUES('dec_fixture01',"
                  "'cyc_fixture01',?,'SOL/USDT','BUY',0.61,0.5,0.6,1,'spec_fixture_trend',?,"
                  "'scan_fixture01')", (at(minutes=299), sig))
        c.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,"
                  "executed,skip_reason,strategy_ids,signals_json) VALUES('dec_fixture02',"
                  "'cyc_fixture02',?,'ETH/USDT','SELL',0.55,0.5,0.5,0,"
                  "'risk: max positions (3/3)','spec_ghost','not json')", (at(minutes=89),))
        for cid, agent, side in (("cyc_fixture01", "trend", "long"),
                                 ("cyc_fixture01", "flow", "flat"),
                                 ("cyc_other01", "trend", "short")):
            c.execute("INSERT INTO votes(cycle_id,ts,symbol,agent,side,conviction,confidence,"
                      "rationale,meta) VALUES(?,?,'SOL/USDT',?,?,0.3,0.5,'fixture','{}')",
                      (cid, at(minutes=300), agent, side))
        c.execute("INSERT INTO outcomes(decision_id,cycle_id,symbol,ts,action,entry_price,"
                  "resolved_at,fwd_ret_1h,fwd_ret_4h) VALUES('dec_fixture01','cyc_fixture01',"
                  "'SOL/USDT',?,'BUY',150.0,?,0.004,0.012)", (at(minutes=299), at(minutes=59)))
        c.execute("INSERT INTO trades(id,decision_id,symbol,side,amount,entry_price,exit_price,"
                  "notional_usdt,strategy_id,strategy_name,exec_mode,opened_at,closed_at,"
                  "realized_pnl,close_reason,status,mfe_r,mae_r,initial_risk) VALUES("
                  "'pos_fixture01','dec_fixture01','SOL/USDT','long',2,150,156.25,300,"
                  "'spec_fixture_trend','spec:spec_fixture_trend','live',?,?,12.5,'trail',"
                  "'closed',1.8,-0.4,6.0)", (at(minutes=298), at(minutes=30)))
        brain = [
            (at(days=10), "harvest_idea", "tv_fixture_1",
             {"title": "Fixture pullback idea", "source": "tradingview",
              "url": "https://example.invalid/idea"}),
            (at(days=9), "idea_consumed", "tv_fixture_1",
             {"outcome": "admitted", "spec": "spec_fixture_trend", "stream": "strategy"}),
            (at(days=9), "spec_admitted", "spec_fixture_trend", {"name": "Fixture Trend"}),
            (at(days=2), "spec_rejected", "spec_ghost", {"name": "Ghost"}),
            (at(days=1), "postmortem", "book",
             {"verdicts": {"spec_fixture_trend": "CONSISTENT"},
              "book": [{"id": "spec_fixture_trend", "live_trades": 1,
                        "health": {"verdict": "CONSISTENT", "trades": 1}}]}),
        ]
        for ts, kind, subject, detail in brain:
            c.execute("INSERT INTO brain_events(ts,kind,subject,detail) VALUES(?,?,?,?)",
                      (ts, kind, subject, json.dumps(detail)))
        c.execute("INSERT INTO brain_events(ts,kind,subject,detail) VALUES(?,'keep_verdict',"
                  "'spec_fixture_trend','{broken')", (at(hours=12),))
        for h, parent, trig, verdict in (("a1b2c3d4e5f60718", "", "seed:spec_fixture_trend",
                                          "grow"),
                                         ("b1b2c3d4e5f60718", "a1b2c3d4e5f60718", "", "prune")):
            c.execute("INSERT INTO research_combos(hash,tf,geo,k,round,parent,trigger,parts,"
                      "label,status,verdict,reason,median_pf,trades,ablation,result,created_at)"
                      " VALUES(?,'4h','trail',1,'seeded',?,?,'[\"donchian_hi(100)\"]',"
                      "'donchian seed','scored',?,'fixture',1.12,41,'{\"trail\": -0.1}',"
                      "'{\"verdict\": \"grow\"}',?)", (h, parent, trig, verdict, at(minutes=20)))
        c.execute("INSERT INTO research_candidates(hash,tf,geo,state,rank,reason,updated_at) "
                  "VALUES('a1b2c3d4e5f60718','4h','trail','queued',1.0,'fixture',?)",
                  (at(minutes=19),))
        c.execute("INSERT INTO research_tests(hash,tf,geo,gate,p,alpha_t,rejected,braked,at) "
                  "VALUES('a1b2c3d4e5f60718','4h','trail','gate1',0.2,0.01,0,0,?)",
                  (at(minutes=18),))
        for i in range(3):
            c.execute("INSERT INTO control_events(ts,event,actor,detail) VALUES(?,"
                      "'signal_cooldown','luffy','{}')", (at(minutes=10 - i),))
        c.execute("INSERT INTO control_events(ts,event,actor,detail) VALUES(?,'reconcile',"
                  "'luffy','{\"position_count\": 2}')", (at(minutes=15),))
        c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES('risk_state',?)",
                  (json.dumps({"peak_equity": 5200.0, "day_start_equity": 5150.0,
                               "day_key": now.strftime("%Y-%m-%d")}),))
        c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES('rent_state','{bad')")
    vault = root / "knowledge"
    for rel, text in M2_NOTES.items():
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


class FakeGateway:
    """Stands in for the kernel Owner Interface behind the dashboard gateway.

    Honors the contract the frontend depends on: request-id idempotency
    (a repeated id replays its recorded result), guarded recovery (resume and
    unhalt only ever report CONTAINED here), and fail-closed availability.
    """

    def __init__(self, journal):
        self.journal = journal
        self.calls: list[dict] = []
        self.records: dict[str, OwnerResult] = {}
        self.mode = "up"            # up | down | unknown_once | slow
        self.lock = threading.Lock()

    def __call__(self, operation, request_id, issued_at_ms, request, args):
        from trader.owner.adapters import dashboard as owner_dashboard
        from trader.owner.contract import MalformedRequest
        self.calls.append({"operation": operation, "request_id": request_id,
                           "issued_at_ms": issued_at_ms})
        try:
            req = owner_dashboard.to_request(operation, request_id, issued_at_ms,
                                             authenticated=True, args=args)
        except MalformedRequest:
            return refused(None, "malformed_request")
        if self.mode == "down":
            return refused(req, "kernel_unavailable", Status.UNAVAILABLE)
        if self.mode == "slow":
            time.sleep(1.5)
        with self.lock:
            if req.request_id in self.records:
                return self.records[req.request_id].with_(replayed=True)
            state = self.journal.kv_get("control_state")
            if operation in ("status", "health"):
                return OwnerResult(req.request_id, operation, Status.ACCEPTED, principal="owner",
                                   channel="dashboard", control_state_before=state,
                                   control_state_after=state,
                                   data={"control_state": state, "heartbeat_age_s": 12.0,
                                         "supervisor": {"outcome": "SAFE"},
                                         "owner_interface": {"recovery_in_progress": False}})
            if self.mode == "unknown_once":
                self.mode = "up"
                return refused(req, "kernel_timeout_outcome_unknown", Status.OUTCOME_UNKNOWN)
            after = {"freeze": "FROZEN", "halt": "HALTED", "panic": "FROZEN"}.get(operation)
            if after:
                status = Status.ALREADY_SET if after == state and operation != "panic" \
                    else Status.ACCEPTED
                self.journal.kv_set("control_state", after)
                result = OwnerResult(req.request_id, operation, status, principal="owner",
                                     channel="dashboard", control_state_before=state,
                                     control_state_after=after, audit_event_ids=(len(self.calls),))
            elif operation in ("resume", "unhalt"):
                result = OwnerResult(req.request_id, operation, Status.CONTAINED, principal="owner",
                                     channel="dashboard", control_state_before=state,
                                     control_state_after=state,
                                     reasons=("owner_resume_required",),
                                     supervisor_outcome="NEEDS_OWNER")
            else:
                result = refused(req, "unsupported_operation")
            self.records[req.request_id] = result
            return result


class FakeChat:
    calls: list[str] = []
    mode = "ok"                     # ok | fail | fallback | slow | mentions | echo

    def __init__(self):
        self.consulted: list[dict] = []

    def handle(self, message, history):
        FakeChat.calls.append(message)
        if FakeChat.mode == "echo":                 # the reply is the message
            return message
        if FakeChat.mode == "mentions":
            self.consulted = [{"tool": "get_trades", "arguments": '{"limit": 5}',
                               "error": None, "rows": 1}]
            return ("Fixture Trend Pullback opened pos_fixture01; spec_nonexistent and "
                    "tv_fixture_1 are not records here.")
        if FakeChat.mode == "fail":
            raise RuntimeError("llm transport")
        if FakeChat.mode == "fallback":
            from trader.chat.agent import FALLBACK
            return FALLBACK
        if FakeChat.mode == "slow":
            time.sleep(2.0)
        return f"Fixture backend reply to: {message[:60]}"


def make_app(root: Path, monkeypatch=None, scenario: str = "normal"):
    """(app, journal, gateway) with every production seam replaced."""
    import os
    from trader.core.journal import Journal
    from trader.dashboard import server, owner_api

    journal = Journal(str(root / "data" / "luffy.db"))
    seed(root, journal, scenario)
    gateway = FakeGateway(journal)

    def setattr_(obj, name, value):
        if monkeypatch is not None:
            monkeypatch.setattr(obj, name, value)
        else:
            setattr(obj, name, value)

    setattr_(server, "ROOT", root)
    from trader.knowledge import vault as vaultmod
    setattr_(vaultmod, "VAULT", root / "knowledge")
    setattr_(server, "_owner_gateway", lambda cfg, auth: gateway)
    setattr_(server, "_position_marks",
             lambda j: {"BTC/USDT": {"mark": 61000.0, "upnl": 10.0}})
    setattr_(server, "_position_quotes", lambda j: (j.open_trades(), {
        "BTC/USDT": {"symbol": "BTC/USDT", "price": 61000.0, "field": "last",
                     "source_ms": None, "received_at": time.time(), "error": None}}))
    setattr_(server, "_account_snapshot", lambda: (_ for _ in ()).throw(AssertionError("venue")))
    real_install = owner_api.install

    def install(app, **kw):
        kw.setdefault("chat_factory", FakeChat)
        kw.setdefault("dist", Path(__file__).resolve().parents[1] / "frontend" / "dist")
        return real_install(app, **kw)
    setattr_(owner_api, "install", install)
    if monkeypatch is not None:
        monkeypatch.setenv("DASH_TOKEN", TOKEN)
    else:
        os.environ["DASH_TOKEN"] = TOKEN
    app = server.create_app({"attention": {"enabled": False},
                             "owner_interface": {"enabled": True}})
    return app, journal, gateway
