"""Final entry boundary vs control-state transitions (SDD v3.2 §27).

Once FROZEN / RECOVERY / HALTED is persisted, no new entry may be submitted;
an entry already past the shared fence finishes first and the transition
waits. Cross-process cases use spawned processes and the real flock fence.
"""
import multiprocessing as mp
import threading
import time

import pytest
from ccxt import RequestTimeout

from trader.core.journal import Journal
from trader.core.types import Action, ControlState, Decision
from trader.engine import protective
from trader.engine.control_fence import control_fence, fence_path
from trader.engine.recovery import KEY
from trader.engine.state import ControlStateMachine
from tests.test_entry_recovery import Venue, enter, setup  # noqa: F401

A, F, H, R = (ControlState.ACTIVE, ControlState.FROZEN,
              ControlState.HALTED, ControlState.RECOVERY)
SPAWN = mp.get_context("spawn")


def _entries(ex):
    return [s for s in ex.sent
            if not s[4].get("reduceOnly") and "stopLossPrice" not in s[4]]


def _events(j, event):
    return j.query("SELECT id, detail, to_state FROM control_events "
                   "WHERE event=? ORDER BY id", (event,))


# ── spawned-process bodies (module level for the spawn start method) ─────
def _child_transition(db, path, started):
    sm = ControlStateMachine(Journal(db))
    started.set()
    for s in path:
        sm.set(ControlState(s), "other_process")


def _child_hold_fence_then_persist(db, state, held, release):
    j = Journal(db)
    with control_fence(j):
        held.set()
        release.wait(10)
        j.kv_set("control_state", state)


def _child_hold_fence(db, held, release):
    with control_fence(Journal(db)):
        held.set()
        release.wait(10)


# ── 1-3, 5: persisted state is authoritative at the final boundary ────────
@pytest.mark.parametrize("path,reason", [
    ((F, R), "state=RECOVERY: entries blocked"),
    ((F,), "state=FROZEN: entries blocked"),
    ((H,), "state=HALTED"),
])
def test_blocking_state_persisted_by_other_process_stops_entry(setup, path, reason):
    ex, j, e, d = setup
    kernel_sm = ControlStateMachine(j)
    assert kernel_sm.state == A                   # stale cycle cached ACTIVE
    started = SPAWN.Event()
    p = SPAWN.Process(target=_child_transition,
                      args=(str(j.db_path), [s.value for s in path], started))
    p.start(); p.join(30)
    assert p.exitcode == 0
    assert kernel_sm.state == A                   # still stale in-process

    assert enter(e, d) is None
    assert _entries(ex) == []                     # create_order never called
    assert d.skip_reason == reason
    assert not e.recovery_pending()               # no bogus intent left
    assert j.kv_get(KEY, "null") == "null"
    assert _events(j, "execution_recovery") == []
    assert not j.open_trades()


def test_active_still_enters(setup):
    ex, j, e, d = setup
    assert enter(e, d) is not None
    assert len(_entries(ex)) == 1
    assert not e.recovery_pending()


def test_unreadable_persisted_state_fails_closed(setup):
    ex, j, e, d = setup
    j.kv_set("control_state", "BOGUS")
    assert enter(e, d) is None
    assert _entries(ex) == [] and not e.recovery_pending()
    assert "entries blocked" in d.skip_reason


# ── 4: entry holding the fence finishes before the transition persists ───
@pytest.mark.parametrize("path", [(F,), (F, R)])
def test_entry_in_flight_serializes_before_transition(setup, path):
    ex, j, e, d = setup
    db = str(j.db_path)
    child = {}
    observed = {}
    real_create = ex.create_order

    def create_order(symbol, typ, side, amount, params=None):
        params = params or {}
        if not params.get("reduceOnly") and "stopLossPrice" not in params \
                and "p" not in child:
            started = SPAWN.Event()
            child["p"] = SPAWN.Process(target=_child_transition,
                                       args=(db, [s.value for s in path], started))
            child["p"].start()
            assert started.wait(30)
            time.sleep(0.5)                       # child is now blocked on set()
            observed["child_alive"] = child["p"].is_alive()
            observed["state"] = Journal(db).kv_get("control_state", "ACTIVE")
        return real_create(symbol, typ, side, amount, params=params)

    ex.create_order = create_order
    assert enter(e, d) is not None                # first entry submitted
    child["p"].join(30)
    assert child["p"].exitcode == 0

    # The transition could not persist while the entry held the fence.
    assert observed == {"child_alive": True, "state": "ACTIVE"}
    # Submission recorded first, blocking state persisted second.
    submitted = [r["id"] for r in _events(j, "execution_recovery")
                 if '"entry_submitted"' in r["detail"]]
    changes = _events(j, "state_change")
    assert submitted and changes
    assert submitted[0] < changes[0]["id"]
    assert j.kv_get("control_state") == path[-1].value

    # No subsequent entry is allowed.
    d2 = Decision("d2", "c", "BTC/USDT", Action.BUY, 1., .5, .8, [], [])
    j.log_decision(d2)
    assert enter(e, d2) is None
    assert len(_entries(ex)) == 1
    assert "entries blocked" in d2.skip_reason


def test_transition_holding_fence_blocks_entry_until_persisted(setup):
    ex, j, e, d = setup
    held, release = SPAWN.Event(), SPAWN.Event()
    p = SPAWN.Process(target=_child_hold_fence_then_persist,
                      args=(str(j.db_path), "FROZEN", held, release))
    p.start()
    assert held.wait(30)
    out = {}
    t = threading.Thread(target=lambda: out.update(pos=enter(e, d)))
    t.start()
    time.sleep(0.3)
    assert t.is_alive() and _entries(ex) == []    # waiting on the fence
    release.set()
    t.join(30); p.join(30)
    assert out["pos"] is None and _entries(ex) == []
    assert d.skip_reason == "state=FROZEN: entries blocked"
    assert not e.recovery_pending()


def test_set_takes_the_same_fence_file(setup):
    _, j, _, _ = setup
    assert fence_path(j) == j.db_path.with_name(j.db_path.name + ".control.lock")
    ControlStateMachine(j).set(F, "test")
    assert fence_path(j).exists()


# ── 6: exits, protection and reconciliation never wait on the fence ───────
def test_exit_protection_and_recovery_run_in_recovery_while_fence_held(setup):
    ex, j, e, d = setup
    # One ambiguous entry needing reconciliation + protection.
    ex.entry_error = RequestTimeout("ambiguous")
    assert enter(e, d) is None and e.recovery_pending()
    ex.entry_error = None
    _sm = ControlStateMachine(j); _sm.set(F, "test"); _sm.set(R, "test")

    held, release = SPAWN.Event(), SPAWN.Event()
    p = SPAWN.Process(target=_child_hold_fence,
                      args=(str(j.db_path), held, release))
    p.start()
    try:
        assert held.wait(30)

        def work():
            e.recover_entries()                    # places protective stop
            e.recover_entries()                    # verifies stop, adopts
            protective.place_stop(ex, "BTC/USDT", "sell", 2., 90.)
            e.close(j.open_trades()[0], 100., "manual")
        t = threading.Thread(target=work)
        t.start(); t.join(15)
        assert not t.is_alive(), "exit/recovery path waited on entry fence"
    finally:
        release.set(); p.join(30)

    assert not e.recovery_pending()
    reduce_only = [s for s in ex.sent if s[4].get("reduceOnly")]
    assert any("stopLossPrice" in s[4] for s in reduce_only)     # protection
    assert any("stopLossPrice" not in s[4] for s in reduce_only)  # exit
    assert len(_entries(ex)) == 1                                # no new entry
    assert j.kv_get("control_state") == "RECOVERY"
