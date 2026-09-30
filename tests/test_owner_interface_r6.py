"""R6: a delivery's failed reservation is not a terminal outcome for its ID.

Faults are restricted to a temp journal and one delivery. The interleaving
is gated with Events, not sleeps: A reads absence, B reserves/claims/finishes,
A's write fails, A reconciles, then B can finish and its exact result replays.
"""
from contextlib import contextmanager
from threading import Event, Thread, current_thread
import time

import pytest

from trader.owner.contract import OwnerResult, Status, refused
from trader.owner.service import OwnerService
from trader.owner.adapters import telegram
from tests.test_owner_interface import req
from tests.test_owner_interface_r5 import _svc
from tests.test_owner_recovery_risk_guard import tg_update


def reservation_race(tmp_path, monkeypatch, stage="reserved", unreadable=False):
    journal, service = _svc(tmp_path)
    request = req("halt", "test-reservation-race-r6")
    paused, release = Event(), Event()
    results, errors = [], []
    existing, tx, query = service._existing, journal._tx, journal.query
    failed = False
    looked = False

    def lookup(r, principal):
        nonlocal looked
        answer = existing(r, principal)
        if current_thread() is delivery and not looked:
            looked = True
            assert answer is None
            paused.set()
            assert release.wait(10), "B did not release A"
        return answer

    @contextmanager
    def fail_one_write():
        nonlocal failed
        if current_thread() is delivery and not failed:
            failed = True
            raise OSError("injected reservation write failure in A")
        with tx() as conn:
            yield conn

    def read(sql, *args, **kwargs):
        if unreadable and failed and current_thread() is delivery and "owner_requests" in sql:
            raise OSError("injected reconciliation read failure in A")
        return query(sql, *args, **kwargs)

    def run_a():
        try:
            results.append(service.execute(request))
        except BaseException as exc:
            errors.append(exc)

    delivery = Thread(target=run_a)
    monkeypatch.setattr(service, "_existing", lookup)
    monkeypatch.setattr(journal, "_tx", fail_one_write)
    monkeypatch.setattr(journal, "query", read)
    delivery.start()
    reservation = final = intake = None
    try:
        assert paused.wait(5), "A did not reach the no-row lookup"
        if stage != "absent":
            reservation = service.reserve(request)
            assert not isinstance(reservation, OwnerResult)
        if stage == "claimed":
            intake = service._claim(request, reservation, time.time())
        elif stage == "completed":
            final = service.execute(request, reservation=reservation)
        elif stage == "refused":
            final = service.settle_refusal(request, reservation, "owner_interface_busy")
    finally:
        release.set()
        delivery.join(10)
    assert not delivery.is_alive()
    assert not errors, errors
    assert failed and len(results) == 1
    answer = results[0]
    if unreadable or stage == "absent":
        assert answer.status == Status.OUTCOME_UNKNOWN, answer
        assert answer.disposition == "OUTCOME_UNKNOWN"
    elif stage in ("reserved", "claimed"):
        if answer.status != Status.IN_PROGRESS:
            raise AssertionError(answer)
        assert answer.disposition == "IN_PROGRESS"
    else:
        assert answer.to_wire() == final.with_(replayed=True).to_wire()
    if stage == "reserved":
        assert journal.kv_get("control_state") is None
        final = service.execute(request, reservation=reservation)
    elif stage == "claimed":
        final = service._complete(request, reservation.principal, intake,
                                  service._execute(request, reservation.principal))
    if final is not None:
        assert service.execute(request).to_wire() == final.with_(replayed=True).to_wire()
        transitions = journal.query("SELECT * FROM control_events WHERE event='state_change'")
        assert len(transitions) == (0 if stage == "refused" else 1)
        assert journal.kv_get("control_state") == (None if stage == "refused" else "HALTED")
    else:
        assert journal.kv_get("control_state") is None
    return answer


@pytest.mark.parametrize("stage", ["reserved", "claimed", "completed", "refused", "absent"])
def test_reservation_write_failure_reconciles_same_id(tmp_path, monkeypatch, stage):
    reservation_race(tmp_path, monkeypatch, stage)


def test_reservation_write_and_reconciliation_read_failure_is_unknown(tmp_path, monkeypatch):
    reservation_race(tmp_path, monkeypatch, unreadable=True)


@pytest.mark.parametrize("mutant", ["immediate_refusal", "skip_durable_reservation"])
def test_negative_control_reservation_failure(tmp_path, monkeypatch, mutant):
    def broken(self, request, principal):
        return self._finish_refusal(request, principal, "persistence_unavailable",
                                    Status.REFUSED if mutant == "immediate_refusal"
                                    else Status.OUTCOME_UNKNOWN)
    monkeypatch.setattr(OwnerService, "_reconcile_reservation_failure", broken)
    # An observable RESERVED row must produce IN_PROGRESS, not REFUSED or a
    # guessed UNKNOWN that skips reconciliation. The assertion checks status.
    with pytest.raises(AssertionError) as caught:
        reservation_race(tmp_path, monkeypatch)
    assert isinstance(caught.value.args[0], OwnerResult)
    assert caught.value.args[0].status == (Status.REFUSED if mutant == "immediate_refusal"
                                         else Status.OUTCOME_UNKNOWN)


def check_telegram_unknown(render):
    update = tg_update()
    original = telegram.to_request("/halt", update, chat_id="1")
    retry = telegram.to_request("/halt", update, chat_id="1")
    assert retry.to_wire() == original.to_wire()
    unknown = refused(original, "persistence_unavailable", Status.OUTCOME_UNKNOWN)
    text = render(unknown)
    assert "nothing changed" not in text.lower(), text
    assert "unknown" in text.lower(), text
    assert "same request" in text.lower(), text


def test_telegram_unknown_preserves_identity_and_never_claims_nothing_changed():
    check_telegram_unknown(telegram.reply_text)


def test_negative_control_telegram_unknown_rendering():
    with pytest.raises(AssertionError, match="Nothing changed"):
        check_telegram_unknown(lambda result: "Nothing changed")


def test_signed_ipc_reservation_write_race(tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path
    from threading import Lock
    from trader.owner.ipc import OwnerClient, OwnerIPCServer
    from tests.test_owner_interface import dreq

    journal, service = _svc(tmp_path)
    request = dreq("halt", "dashboard-r6-ipc-race")
    read_absence, resume_a, reserved_b, resume_b = (Event() for _ in range(4))
    selection = Lock()
    delayed, failed = None, False
    existing, tx, claim = service._existing, journal._tx, service._claim

    def lookup(r, principal):
        nonlocal delayed
        answer = existing(r, principal)
        with selection:
            first = delayed is None
            if first:
                delayed = current_thread()
        if first:
            assert answer is None
            read_absence.set()
            assert resume_a.wait(10)
        return answer

    @contextmanager
    def fail_a():
        nonlocal failed
        if current_thread() is delayed and not failed:
            failed = True
            raise OSError("injected IPC delivery A reservation failure")
        with tx() as conn:
            yield conn

    def pause_claim(*args):
        reserved_b.set()
        assert resume_b.wait(10)
        return claim(*args)

    monkeypatch.setattr(service, "_existing", lookup)
    monkeypatch.setattr(journal, "_tx", fail_a)
    monkeypatch.setattr(service, "_claim", pause_claim)
    with tempfile.TemporaryDirectory(prefix="oi-r6-") as directory:
        ipc_dir = Path(directory) / "ipc"
        server = OwnerIPCServer(service, ipc_dir).start()
        client = OwnerClient(ipc_dir, "dashboard")
        a, b = [], []
        ta = Thread(target=lambda: a.append(client.call(request)))
        tb = Thread(target=lambda: b.append(client.call(request)))
        try:
            ta.start()
            assert read_absence.wait(5)
            tb.start()
            assert reserved_b.wait(5)
            resume_a.set()
            ta.join(10)
            assert a[0].status == Status.IN_PROGRESS
            assert journal.kv_get("control_state") is None
            resume_b.set()
            tb.join(10)
            assert b[0].status == Status.ACCEPTED
            assert journal.kv_get("control_state") == "HALTED"
            assert client.call(request).to_wire() == b[0].with_(replayed=True).to_wire()
        finally:
            resume_a.set()
            resume_b.set()
            if ta.ident is not None:
                ta.join(10)
            if tb.ident is not None:
                tb.join(10)
            server.stop()
