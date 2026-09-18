"""Synthetic forward forecasts; no sockets, journal, or research evaluation."""
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from trader.observability import learning as L
from trader.observability.attention import settings
from trader.observability.store import Store
from tests.test_attention_telemetry import event, frames


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network forbidden"))


@pytest.fixture
def fixture(tmp_path):
    import time
    now = int(time.time()*1000)//L.TF*L.TF + 60_000
    return tmp_path/"attention.db", tmp_path/"learning.db", now


def publish(path, now, sid="s", target_close=None):
    data = frames(6, now)
    # One selected volume anomaly alongside five ordinary/ignored assets.
    data['S0/USDT']['4h'].loc[29, 'volume'] = 100_000
    if target_close is not None:
        for item in data.values():
            df = item["4h"]
            df.loc[df.index[-1], ["open", "high", "low", "close"]] = [target_close]*4
    store = Store(path, settings())
    # Synthetic snapshots must use their own clock for age-based retention.
    with patch('trader.observability.store.time', SimpleNamespace(time=lambda: now/1000)):
        ident=dict(schema='attention-scan-identity.v1',instance_id='1'*32,seq=now)
        store.write(dict(event(sid, now, data),identity=ident))
        store.write({"kind":"causes", "scan_id":sid, "as_of_ms":now,
                     "identity":ident,"items":[{"symbol":s,"decision_id":"d_"+s} for s in data]})
    store.close()
    path.with_name("attention_health.json").write_text(json.dumps({
        "health_schema":"attention-collector-health.v2", "instance_id":"1"*32,
        "updated_ms":now, "status":"ok", "worker_alive":True, "errors":0,
        "capture_errors":0,"worker_errors":0,"dropped":0,"details_lost":0,
        "process_started_ms":0,"failure_generation":0,"fence_seq":0,"certificate":None,
        "last_error":None,"first_error_ms":None,"last_error_ms":None,
        "last_complete":{"scan_id":sid,"seq":now}}))


def episodes(path):
    with sqlite3.connect(path) as db:
        return [(json.loads(r[0]), json.loads(r[1]) if r[1] else None)
                for r in db.execute("SELECT prediction,outcome FROM episodes ORDER BY id")]


def test_register_resolve_and_learn_without_rewriting_prediction(fixture):
    source, dest, now = fixture
    publish(source, now)
    r = L.step(source, dest, now)
    assert r["registered"] == 6 and r["counts"] == {"pending":6}
    original = episodes(dest)
    assert any(not p["selected"] for p,_ in original)
    assert any(p["selected"] for p,_ in original)
    assert all(p["probability"] is None and p["decisions"] for p,_ in original)
    assert all(p["target_open_ms"] > now for p,_ in original)
    assert L.step(source,dest,now+1)["registered"] == 0
    publish(source, now+1000, "repeat")
    assert L.step(source,dest,now+1000)["registered"] == 0
    deadline = original[0][0]["target_open_ms"]+L.TF
    publish(source, deadline+1,"future",120)
    r = L.step(source,dest,deadline+1)
    assert r["resolved"] == 6
    assert sum(r["descriptive_outcomes"].values()) == 6
    resolved = [(p,o) for p,o in episodes(dest) if o]
    assert {L.encode(p) for p,_ in resolved} == {L.encode(p) for p,_ in original}
    for p,o in resolved:
        assert o["price_change_bps"] == pytest.approx((120/p["baseline_close"]-1)*10_000)
        assert o["target"]["open_ms"] == p["target_open_ms"]
        assert o["measurement"] == "observed_price_change_not_pnl"
        assert o["supports"] == ("persistence" if p["direction"]==1 else "reversal")
    publish(source,deadline+2,"revision",140)
    L.step(source,dest,deadline+2)
    assert [(p,o) for p,o in episodes(dest) if o] == resolved


@pytest.mark.parametrize("offset,reason", [(-300_001,"snapshot_stale"),
                                           (1,"snapshot_future"),
                                           (-1,"awaiting_post_activation_scan")])
def test_no_historical_stale_or_future_proposals(fixture,offset,reason):
    source,dest,now=fixture
    publish(source,now+offset)
    update_health(source, updated_ms=now)
    r=L.step(source,dest,now)
    assert r["registered"]==0 and r["reason"]==reason


def test_missing_target_never_substitutes_a_nearby_price(fixture):
    source,dest,now=fixture
    publish(source,now);r=L.step(source,dest,now)
    deadline=r["recent"][0]["deadline_ms"]
    publish(source,deadline+L.TF,"hole",120)
    with sqlite3.connect(source) as db:
        db.execute("DELETE FROM scan_versions WHERE scan_id='hole' AND version_id IN "
                   "(SELECT id FROM versions WHERE open_ms=?)",(deadline-L.TF,))
    r=L.step(source,dest,deadline+L.TF)
    assert r["resolved"]==0 and r["registered"]==0
    assert r["counts"]=={"pending":6}
    r=L.step(source,dest,deadline+L.PROTOCOL["grace_ms"]+1)
    assert r["counts"]=={"unavailable":6}
    assert not r["descriptive_outcomes"]


def test_unhealthy_collector_refuses_forecasts_and_logs_reason(fixture):
    source,dest,now=fixture
    publish(source,now)
    update_health(source, status="error",errors=1,worker_errors=1,failure_generation=1,last_error="WorkerError")
    r=L.step(source,dest,now)
    assert r["reason"]=="collector_failing" and not r["counts"]
    with sqlite3.connect(dest) as db:
        assert json.loads(db.execute("SELECT detail FROM diagnostics").fetchone()[0])["reason"]==r["reason"]


def test_neutral_outcomes_and_capacity_are_explicit(fixture,monkeypatch):
    source,dest,now=fixture
    publish(source,now)
    monkeypatch.setitem(L.PROTOCOL,"max_episodes",1)
    r=L.step(source,dest,now)
    assert r["registered"]==1 and r["reason"]=="ledger_capacity_reached"
    p=episodes(dest)[0][0];deadline=p["target_open_ms"]+L.TF
    publish(source,deadline+1,"neutral",p["baseline_close"])
    r=L.step(source,dest,deadline+1)
    assert r["resolved"]==1
    assert episodes(dest)[0][1]["supports"]=="unresolved"


def test_owner_view_shows_staleness_and_omits_bulk_inputs(tmp_path):
    path=tmp_path/"health.json"
    assert L.read_health(path,1)=={"status":"unavailable"}
    path.write_text(json.dumps({"status":"ok","updated_ms":1000,"counts":{"pending":1},
        "recent":[{"symbol":"X","status":"pending","deadline_ms":2000,
                   "prediction":{"direction":1,"selected":False,"scan_id":"s",
                                 "input_window":["large payload"]},"outcome":None}]}))
    view=L.read_health(path,1001)
    assert view["status"]=="ok" and view["recent"][0]["scan_id"]=="s"
    assert "input_window" not in L.encode(view)
    assert L.read_health(path,601001)["status"]=="stale"


def test_future_availability_cannot_supply_prediction_baseline(fixture):
    source,dest,now=fixture
    publish(source,now)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE versions SET first_seen_ms=?",(now+1,))
    assert L.step(source,dest,now)["registered"]==0


def test_registration_respects_commit_margin(fixture):
    source,dest,now=fixture
    boundary=now//L.TF*L.TF+L.TF-1000
    publish(source,boundary)
    r=L.step(source,dest,boundary)
    assert r["registered"]==0 and r["skipped"]=={"registration_boundary_guard":6}


def test_malformed_target_is_missing_not_a_price_outcome(fixture):
    source,dest,now=fixture
    publish(source,now);r=L.step(source,dest,now)
    deadline=r["recent"][0]["deadline_ms"]
    publish(source,deadline+1,"invalid",120)
    with sqlite3.connect(source) as db:
        for vid,payload in db.execute("SELECT id,payload FROM versions WHERE open_ms=?",(deadline-L.TF,)).fetchall():
            bar=json.loads(payload);bar["volume"]=-1
            db.execute("UPDATE versions SET payload=? WHERE id=?",(json.dumps(bar),vid))
    r=L.step(source,dest,deadline+1)
    assert r["resolved"]==0 and r["counts"]=={"pending":6}


def update_health(path, **fields):
    path=path.with_name('attention_health.json')
    value=json.loads(path.read_text());value.update(fields)
    path.write_text(json.dumps(value))
