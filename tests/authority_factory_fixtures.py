"""Stage-5 handoff: referee-passed candidate -> first-live eligibility.

Every test runs on a temporary journal. The candidate is built exactly as
research phase 3 stores one (ledger combo row, one registered gate1 look in
research_tests, candidate gate JSON); nothing reads the production database.
"""
import hashlib
import json
import pathlib
import re
import sqlite3

import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.strategy import factory_handoff as F
from trader.strategy.spec import ExitSpec, StrategySpec


T0 = 1_790_000_000_000             # probation start (ms)
DAY = 86_400_000
INPUTS = {"ohlcv", "funding", "oi", "ls_ratio", "ls_account_ratio", "taker",
          "basis", "xs", "btc", "ref"}


def _iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


@pytest.fixture
def cfg(monkeypatch):
    # Lifecycle fixtures explicitly register synthetic cost observations.
    # Production/unknown-cost acceptance uses load_config directly.
    from tests.authority_cost_fixtures import register_test_cost_evidence
    register_test_cost_evidence(monkeypatch)
    return load_config()


def _candidate(j, state="referee_passed", p=0.001, alpha=0.0025,
               look=True, gate3=True, g1_p=None):
    from trader.research import vocab
    from trader.research.combo import Combination, evaluated_record
    from trader.research.ledger import Ledger
    led = Ledger(j)
    led.ensure()
    led.record_gauges("4h", {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5,
                                 "p90": 1.0, "n": 9000, "finite_frac": 0.99,
                                 "usable": True}
                             for e in vocab.expressions("4h")})
    part = vocab.parts_for("4h", led.gauges("4h"))[0]
    c = Combination((part,), "4h", "fixed")
    led.record_result({"hash": c.hash, "tf": "4h", "geo": "fixed", "k": 1,
                       "parts": list(c.keys), "trades": 300,
                       "portfolio": {"total_pct": 40.0, "max_dd_pct": 20.0},
                       "testable": True, "verdict": "scored"},
                      "survivor", "r", {})
    if look:
        led.record_test(c.hash, "4h", "fixed", "gate1", p, alpha, False,
                        {"t": 1, "evaluated": evaluated_record(c)})
    led.set_candidate(
        c.hash, "4h", "fixed", state, rank=40.0,
        gate1={"p": p if g1_p is None else g1_p, "alpha": alpha, "t": 1,
               "reason": "fixture"},
        gate3={"passed": gate3, "reason": "fixture book"})
    return c.hash


def _journal(tmp_path, **kw):
    j = Journal(tmp_path / "j.db")     # carries trades.entry_identity_json
    return j, _candidate(j, **kw)


def _upsert(j, spec_d):
    """The existing paper install primitive (Journal.upsert_spec)."""
    j.upsert_spec(StrategySpec.from_dict(spec_d), state="paper",
                  origin="research")


def _install(j, version_id, at_ms=T0):
    """What the Kernel's exact-version handoff does: install the frozen
    spec unchanged, then bind the install (starts probation)."""
    rec = F.load_version(j, version_id)
    _upsert(j, rec["spec"])
    F.record_exact_install(j, version_id, at_ms=at_ms)
    return rec


def _identity(strategy_id, spec_hash, status="VERIFIED"):
    """A trade-entry-identity.v1 body as trade_provenance.entry_identity
    writes it for a spec strategy (fields probation reads)."""
    return json.dumps({"schema_version": "trade-entry-identity.v1",
                       "status": status, "strategy_id": strategy_id,
                       "kind": "spec", "spec_sha256": spec_hash,
                       "reason": "signal_spec_hash_matches_loaded_spec"},
                      sort_keys=True)


_EXACT = object()


def _trades(j, strategy_id, pnls, start_ms, prefix="t", spec_hash=None,
            identity=_EXACT):
    """Closed trades; with the provenance column, each carries the entry
    identity of `spec_hash` (or `identity` verbatim, None = NULL)."""
    has = F.trade_identity_available(j)
    installs = j.query("SELECT * FROM strategy_version_installs WHERE strategy_id=?", (strategy_id,))
    install = dict(installs[0]) if len(installs) == 1 else None
    with j._tx() as c:
        # Explicit TEST-ONLY reference-price simulation provenance for fixtures.
        columns = {r[1] for r in c.execute('PRAGMA table_info(trades)')}
        for name, typ in (('reference_price','REAL'),('exit_reference_price','REAL'),
                          ('fill_basis','TEXT'),('exit_fill_basis','TEXT')):
            if name not in columns:
                c.execute(f'ALTER TABLE trades ADD COLUMN {name} {typ}')
        for i, pnl in enumerate(pnls):
            evidence = None
            geometry = None
            o = start_ms + (i + 1) * 3_600_000
            idn = _identity(strategy_id, spec_hash) if identity is _EXACT \
                else identity
            if identity is _EXACT and install and spec_hash:
                body = json.loads(idn)
                body.update(version_id=install['version_id'], install_id=install['install_id'], exec_mode='paper')
                version = F.load_version(j, install['version_id'])
                if spec_hash == version['spec_hash']:
                    from trader.strategy import exit_policy as E
                    from trader.engine import paper_exit_evidence as X
                    from trader.core.types import TF_MS
                    spec = StrategySpec.from_dict(version['spec'])
                    step = TF_MS[spec.timeframe]
                    policy, initial = E.initialize(spec.exit, 100, 1, 'long', o-step, step)
                    body.update(exit_semantics_id=policy.semantics_id, exit_state=E.encode(policy, initial))
                    # Explicit TEST-ONLY observations replay the real contract.
                    px = policy.target+1 if pnl > 0 else policy.stop-1
                    amount = pnl/(px-100)
                    observation = E.Observation(o, px, px, px, px)
                    result = E.advance(policy, initial, observation, amount)
                    evidence = X.start(f'{prefix}{i}', body, policy, initial, amount)
                    X.observe(evidence, policy, result, observation)
                    geometry = (amount, policy.stop, policy.target, px, policy.initial_r, result.reason, o+step)
                idn = json.dumps(body, sort_keys=True)
            cols = ("id, symbol, side, amount, entry_price, strategy_id, "
                    "exec_mode, opened_at, closed_at, realized_pnl, status")
            vals = [f"{prefix}{i}", "BTC/USDT", "long", 1.0, 100.0,
                    strategy_id, "paper", _iso(o), _iso(o + 1_800_000), pnl,
                    "closed"]
            if has:
                cols += ", entry_identity_json"
                vals.append(idn)
            if geometry:
                amount, sl, tp, px, risk, reason, cl = geometry
                vals[3] = amount
                vals[8] = _iso(cl)
                cols += ',stop_loss,take_profit,exit_price,initial_risk,close_reason'
                vals += [sl,tp,px,risk,reason]
                cols += ',market_type,reference_price,exit_reference_price,fill_basis,exit_fill_basis'
                vals += ['futures',100,px,'TEST-ONLY reference simulation','TEST-ONLY reference simulation']
            c.execute(f"INSERT INTO trades ({cols}) VALUES "
                      f"({','.join('?' * len(vals))})", vals)
            if evidence:
                X.ensure(c)
                X.record(c, evidence)


PASSING = [10.0] * 9 + [-5.0] * 6          # 15 trades, WR 0.6, PF 3.0


def _to_approval(j, cfg, h=None):
    h = h or j.query("SELECT hash FROM research_candidates")[0]["hash"]
    v = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0 - DAY)
    rec = _install(j, v["version_id"])
    _trades(j, rec["strategy_id"], PASSING, T0, spec_hash=v["spec_hash"])
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    assert p["status"] == F.P_SATISFIED
    return v, p


def _approved(j, cfg):
    v, p = _to_approval(j, cfg)
    d = F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                actor="operator", decided_at_ms=T0 + 31 * DAY)
    return v, p, d
