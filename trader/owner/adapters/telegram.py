"""Telegram adapter: an update → OwnerRequest, an OwnerResult → reply text.

Authentication: the kernel's listener drops every update whose chat id is not
TELEGRAM_CHAT_ID before any handler runs. Authorization is by *sender*: the
request's identity is the Telegram user id of `message.from`, which the
kernel resolves through `owner_interface.identities.telegram` (by default the
configured chat id, i.e. the owner's private chat with the bot). Another
member of a group chat is not the owner.

Only exact slash commands map (`/halt`, `/halt@LuffyBot`); "/halting" or free
text never does. An update without a sender, message date or update id is
refused: the adapter never invents an identity or an issue time.
"""
from __future__ import annotations

from ..contract import OwnerRequest, derive_request_id
from .render import render

CHANNEL = "telegram"
COMMANDS = {"/status": "status", "/health": "health", "/freeze": "freeze",
            "/halt": "halt", "/resume": "resume", "/unhalt": "unhalt", "/panic": "panic"}


class NotOwnerCommand(ValueError):
    """The update cannot become an owner request (reason in args[0])."""


def command_of(text: str) -> str | None:
    """The owner operation for an exact slash command, else None."""
    words = (text or "").strip().lower().split()
    if not words:
        return None
    return COMMANDS.get(words[0].split("@", 1)[0])


def to_request(text: str, update: dict | None, *, chat_id: str) -> OwnerRequest:
    """Raises NotOwnerCommand with a reason when the update is unusable."""
    operation = command_of(text)
    if operation is None:
        raise NotOwnerCommand("not_a_command")
    message = (update or {}).get("message") or {}
    chat = (message.get("chat") or {}).get("id")
    if chat is None or str(chat) != str(chat_id):
        raise NotOwnerCommand("foreign_or_missing_chat")
    sender = (message.get("from") or {}).get("id")
    if sender is None or isinstance(sender, bool):
        raise NotOwnerCommand("sender_missing")
    date = message.get("date")
    if isinstance(date, bool) or not isinstance(date, (int, float)):
        raise NotOwnerCommand("message_date_missing")
    update_id, message_id = (update or {}).get("update_id"), message.get("message_id")
    if update_id is None or message_id is None:
        raise NotOwnerCommand("update_reference_missing")
    request_ref = f"{update_id}:{message_id}"
    return OwnerRequest(
        request_id=derive_request_id(CHANNEL, str(chat_id), request_ref),
        operation=operation, channel=CHANNEL, identity=str(sender),
        issued_at=float(date), request_ref=request_ref,
        meta={"command": "/" + operation})


reply_text = render
