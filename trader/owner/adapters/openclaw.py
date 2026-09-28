"""OpenClaw / WhatsApp owner adapter boundary. Live provider hookup: NOT_CONFIGURED.

Inbound event (provider-neutral, validated; provider payload objects never
pass this boundary):

    {"provider": "openclaw" | "whatsapp", "message_id": str, "sender": str,
     "text": str, "timestamp": epoch seconds}

Identity: `owner_interface.identities.<provider>` maps a provider sender id
(e.g. an E.164 number) to a principal. The shipped config maps nobody, so
every sender is unauthorized until the owner binds an identity.

Conversation vs control:
- Only an exact slash command is a proposal: /status /health /freeze /halt
  /resume /unhalt. Anything else — "what would make you halt?", "freeze
  entries", "should I unhalt?" — is conversation and makes no owner request.
- Reads run immediately. A control is only proposed: the reply carries a
  one-time code (HMAC of the proposal under a per-adapter random secret) and
  only "CONFIRM <code>" from the same provider AND sender, still resolving to
  the same principal, within the TTL submits it. The pending entry is keyed
  by (provider, sender, code) and holds its own operation: a code collision
  is refused, never merged, so the displayed operation is the executed one.
  The request id derives from (provider, sender, proposal message id), so a
  redelivered proposal or confirmation executes at most once.
- An LLM never produces an operation: nothing here consults a model.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass

from ..authz import Authorizer
from ..contract import CONTROL_OPERATIONS, OwnerRequest, derive_request_id
from .render import render

PROVIDERS = ("openclaw", "whatsapp")
LIVE_PROVIDER_STATUS = "NOT_CONFIGURED"
COMMANDS = {"/status": "status", "/health": "health", "/freeze": "freeze",
            "/halt": "halt", "/resume": "resume", "/unhalt": "unhalt", "/panic": "panic"}
CODE_HEX = 10
_CONFIRM = re.compile(r"^confirm\s+([0-9a-f]{%d})$" % CODE_HEX)
_ID = re.compile(r"^[A-Za-z0-9._:@+-]{1,128}$")
MAX_TEXT = 2000


class InboundInvalid(ValueError):
    pass


@dataclass(frozen=True)
class Inbound:
    provider: str
    message_id: str
    sender: str
    text: str
    timestamp: float

    @classmethod
    def parse(cls, raw) -> "Inbound":
        if not isinstance(raw, dict):
            raise InboundInvalid("event must be an object")
        keys = {"provider", "message_id", "sender", "text", "timestamp"}
        if set(raw) != keys:
            raise InboundInvalid(f"event keys must be exactly {sorted(keys)}")
        if raw["provider"] not in PROVIDERS:
            raise InboundInvalid("unknown provider")
        for k in ("message_id", "sender"):
            if not isinstance(raw[k], str) or not _ID.match(raw[k]):
                raise InboundInvalid(f"bad {k}")
        if not isinstance(raw["text"], str) or len(raw["text"]) > MAX_TEXT:
            raise InboundInvalid("bad text")
        ts = raw["timestamp"]
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            raise InboundInvalid("bad timestamp")
        return cls(raw["provider"], raw["message_id"], raw["sender"], raw["text"], float(ts))


@dataclass(frozen=True)
class Outbound:
    """What the adapter tells the provider. `request` is set only when one was sent."""
    text: str | None
    kind: str                 # conversation | proposal | result | refused | invalid
    request: OwnerRequest | None = None


@dataclass
class _Proposal:
    provider: str
    sender: str
    principal: str
    operation: str
    message_id: str
    timestamp: float
    expires: float


class OpenClawAdapter:
    def __init__(self, authorizer: Authorizer, clients: dict, *, conversation=None,
                 confirm_ttl_s: float = 120.0, clock=time.time, max_pending: int = 32):
        """`clients`: provider → OwnerClient for that provider's IPC channel."""
        self.authorizer = authorizer
        self.clients = dict(clients)          # the only way out: IPC per channel
        self.conversation = conversation      # optional read-only chat callable
        self.confirm_ttl_s = confirm_ttl_s
        self.clock = clock
        self.max_pending = max_pending
        self._secret = secrets.token_bytes(32)
        self._pending: dict[tuple[str, str, str], _Proposal] = {}
        self._lock = threading.Lock()

    def _code(self, provider: str, sender: str, message_id: str) -> str:
        return hmac.new(self._secret, f"{provider}|{sender}|{message_id}".encode(),
                        hashlib.sha256).hexdigest()[:CODE_HEX]

    @staticmethod
    def _request(provider, sender, operation, message_id, issued_at,
                 confirm_ref=None) -> OwnerRequest:
        meta = {"command": "/" + operation}
        if confirm_ref:
            meta["confirm_ref"] = confirm_ref
        return OwnerRequest(
            request_id=derive_request_id(provider, sender, f"{provider}:{message_id}"),
            operation=operation, channel=provider, identity=sender,
            issued_at=issued_at, request_ref=message_id, meta=meta)

    def _send(self, provider: str, req: OwnerRequest) -> Outbound:
        client = self.clients.get(provider)
        if client is None:
            return Outbound("Owner interface not configured for this channel.", "refused")
        return Outbound(render(client.call(req)), "result", req)

    def handle(self, raw) -> Outbound:
        try:
            ev = Inbound.parse(raw)
        except InboundInvalid:
            return Outbound(None, "invalid")
        principal = self.authorizer.resolve(ev.provider, ev.sender)
        text = ev.text.strip().lower()
        confirm = _CONFIRM.match(text)
        operation = COMMANDS.get(text)
        if operation is None and confirm is None:
            # conversation: never an owner request, whoever the sender is
            if self.conversation is None or principal is None:
                return Outbound(None, "conversation")
            return Outbound(self.conversation(ev.text), "conversation")
        if principal is None:
            return Outbound(None, "refused")          # unknown sender: silent
        if confirm is not None:
            return self._confirm(ev, principal, confirm.group(1))
        if operation not in CONTROL_OPERATIONS:
            return self._send(ev.provider, self._request(
                ev.provider, ev.sender, operation, ev.message_id, ev.timestamp))
        code = self._code(ev.provider, ev.sender, ev.message_id)
        key = (ev.provider, ev.sender, code)
        now = self.clock()
        with self._lock:
            self._expire(now)
            held = self._pending.get(key)
            if held is not None and (held.message_id, held.operation) != (ev.message_id, operation):
                return Outbound("Confirmation code collision; send the command again.",
                                "refused")
            if held is None:
                if len(self._pending) >= self.max_pending:
                    return Outbound("Too many pending confirmations; try again shortly.",
                                    "refused")
                self._pending[key] = _Proposal(ev.provider, ev.sender, principal, operation,
                                               ev.message_id, ev.timestamp,
                                               now + self.confirm_ttl_s)
        return Outbound(f"Confirm {operation.upper()}: reply  CONFIRM {code}  within "
                        f"{int(self.confirm_ttl_s)}s. Nothing has been changed yet.",
                        "proposal")

    def _expire(self, now: float) -> None:
        for key in [k for k, p in self._pending.items() if p.expires < now]:
            del self._pending[key]

    def _confirm(self, ev: Inbound, principal: str, code: str) -> Outbound:
        now = self.clock()
        with self._lock:
            self._expire(now)
            p = self._pending.get((ev.provider, ev.sender, code))
            if p is None or p.principal != principal:
                return Outbound("No pending request with that code (expired or unknown).",
                                "refused")
            # kept until expiry: a redelivered CONFIRM maps to the same request id
        return self._send(p.provider, self._request(
            p.provider, p.sender, p.operation, p.message_id, p.timestamp,
            confirm_ref=ev.message_id))
