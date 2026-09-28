"""Telegram owner binding for a group chat (config.yaml owner_interface.identities).

Production TELEGRAM_CHAT_ID is a group, so the chat id alone never identifies
the owner. An owner command needs BOTH the configured chat and the bound
sender id (message.from.id). Everything else is refused without execution.
Driven through the kernel's real Telegram handler and OwnerService, with the
owner_interface identities read from the repository config.
"""
import json
from pathlib import Path

import pytest
import yaml

from trader.core.journal import Journal
from tests.test_kernel_boot_recovery import _kernel, _last_event, _protected
from tests.test_owner_recovery import HookVenue
from tests.test_owner_recovery_risk_guard import tg_update

ROOT = Path(__file__).resolve().parents[1]
OWNER = 1807747201            # evidenced: control_events #11493/#11500 (telegram /resume)
GROUP = -1001234567890        # stand-in for the configured group TELEGRAM_CHAT_ID
OTHER_GROUP = -1009999999999
STRANGER = 555000111
BASE = "https://api.telegram.org/botTEST"
COMMANDS = ("/status", "/health", "/freeze", "/halt", "/resume", "/unhalt", "/panic")


def _section():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    section = dict(cfg["owner_interface"])
    section["enabled"] = False          # no IPC socket in tests
    return section


@pytest.fixture
def kernel(tmp_path, monkeypatch):
    journal, venue = Journal(tmp_path / "j.db"), HookVenue()
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.cfg = {**k.cfg, "owner_interface": _section()}
    k.boot()
    k.notifier.chat_id = str(GROUP)
    k._owner()                              # the kernel's OwnerService (creates its tables)
    return k, journal


def send(k, cmd, update):
    replies = []
    k._handle_tg_command(cmd, BASE, update=update, reply=replies.append)
    k._owner().flush_audit()               # read / refusal rows are batched
    return replies


def _snapshot(journal):
    return {"state": journal.kv_get("control_state"),
            "panic": journal.kv_get("panic_requested"),
            "last_event": _last_event(journal),
            "requests": journal.query("SELECT COUNT(*) n FROM owner_requests")[0]["n"]}


def _audit(journal):
    return [dict(r) for r in journal.query("SELECT * FROM owner_audit ORDER BY id")]


def test_config_binds_exactly_the_owner_sender():
    section = _section()
    assert section["identities"]["telegram"] == {str(OWNER): "owner"}
    assert section["identities"]["whatsapp"] == {} and section["identities"]["openclaw"] == {}


def test_group_chat_id_is_never_bound_as_an_identity(kernel):
    k, _ = kernel
    ids = k._owner().authorizer.identities["telegram"]
    assert ids == {str(OWNER): "owner"}
    assert str(GROUP) not in ids


@pytest.mark.parametrize("cmd", COMMANDS)
def test_owner_sender_in_configured_group_is_authorized(kernel, cmd):
    k, journal = kernel
    send(k, cmd, tg_update(sender=OWNER, chat=GROUP))
    rows = [r for r in _audit(journal) if r["operation"] == cmd[1:]]
    assert len(rows) == 1
    row = rows[0]
    assert row["principal"] == "owner" and row["identity"] == str(OWNER)
    assert row["channel"] == "telegram"
    assert "identity_not_bound" not in row["reasons"]
    if cmd == "/freeze":
        assert journal.kv_get("control_state") == "FROZEN"
    if cmd == "/halt":
        assert journal.kv_get("control_state") == "HALTED"
    if cmd == "/panic":
        assert json.loads(journal.kv_get("panic_requested"))["intent_event_id"]


@pytest.mark.parametrize("cmd", COMMANDS)
def test_other_sender_in_configured_group_is_denied(kernel, cmd):
    k, journal = kernel
    before = _snapshot(journal)
    replies = send(k, cmd, tg_update(sender=STRANGER, chat=GROUP))
    after = _snapshot(journal)
    assert after["state"] == before["state"] and after["panic"] == before["panic"]
    assert after["requests"] == before["requests"]          # nothing reserved
    rows = [r for r in _audit(journal) if r["operation"] == cmd[1:]]
    assert rows and all(r["status"] == "REFUSED" and "identity_not_bound" in r["reasons"]
                        and r["principal"] is None for r in rows)
    assert len(replies) == 1
    changes = journal.query("SELECT COUNT(*) n FROM control_events WHERE id>? "
                            "AND event IN ('state_change','state_hold','panic')",
                            (before["last_event"],))[0]["n"]
    assert changes == 0


@pytest.mark.parametrize("sender", [OWNER, STRANGER])
@pytest.mark.parametrize("cmd", COMMANDS)
def test_wrong_group_is_denied_whatever_the_sender(kernel, cmd, sender):
    k, journal = kernel
    before = _snapshot(journal)
    replies = send(k, cmd, tg_update(sender=sender, chat=OTHER_GROUP))
    assert _snapshot(journal) == before
    assert _audit(journal) == [] and replies == []           # dropped silently


@pytest.mark.parametrize("cmd", COMMANDS)
def test_missing_sender_is_denied(kernel, cmd):
    k, journal = kernel
    update = tg_update(sender=OWNER, chat=GROUP)
    del update["message"]["from"]
    before = _snapshot(journal)
    replies = send(k, cmd, update)
    assert _snapshot(journal) == before and _audit(journal) == []
    assert replies == ["🔒 not executed — sender_missing."]


def test_forged_metadata_cannot_claim_the_owner(kernel):
    """Only message.from.id is the identity: owner ids placed anywhere else in
    the payload (sender_chat, forward origin, reply, top-level or text
    arguments) never authorize a stranger."""
    k, journal = kernel
    before = _snapshot(journal)
    for cmd in COMMANDS:
        update = tg_update(sender=STRANGER, chat=GROUP)
        msg = update["message"]
        msg["sender_chat"] = {"id": OWNER}
        msg["forward_from"] = {"id": OWNER}
        msg["forward_origin"] = {"type": "user", "sender_user": {"id": OWNER}}
        msg["reply_to_message"] = {"from": {"id": OWNER}, "chat": {"id": GROUP}}
        msg["via_bot"] = {"id": OWNER}
        msg["from"]["username"] = "owner"
        update["principal"] = "owner"
        update["identity"] = str(OWNER)
        send(k, f"{cmd} identity={OWNER} principal=owner", update)
    after = _snapshot(journal)
    assert after["state"] == before["state"] and after["panic"] == before["panic"]
    assert after["requests"] == before["requests"]
    rows = _audit(journal)
    assert len(rows) == len(COMMANDS)
    assert all(r["identity"] == str(STRANGER) and r["principal"] is None
               and "identity_not_bound" in r["reasons"] for r in rows)


def test_owner_redelivery_replays_and_executes_once(kernel):
    k, journal = kernel
    update = tg_update(sender=OWNER, chat=GROUP)
    send(k, "/freeze", update)
    mark = _last_event(journal)
    send(k, "/freeze", update)                     # the same update, delivered again
    assert journal.kv_get("control_state") == "FROZEN"
    assert journal.query("SELECT COUNT(*) n FROM owner_requests")[0]["n"] == 1
    assert journal.query("SELECT COUNT(*) n FROM control_events WHERE id>? AND "
                         "event IN ('state_change','state_hold')", (mark,))[0]["n"] == 0
    rows = [r for r in _audit(journal) if r["operation"] == "freeze"]
    assert len(rows) == 2 and rows[0]["request_id"] == rows[1]["request_id"]
