"""Channel-neutral authorization: authenticated channel identity → principal → grants.

Who authenticates what:
- the channel itself is authenticated at the kernel boundary (in-kernel for
  Telegram; a per-channel IPC key for dashboard / OpenClaw / WhatsApp / CLI —
  see ipc.py), so a client cannot claim another channel;
- the adapter authenticates the owner on its transport (Telegram sender id,
  dashboard session, provider-verified sender) and passes that *identity*;
- the kernel resolves (channel, identity) → principal here. Requests never
  carry a principal, so none can be asserted. Display names are never
  identities. No credentials live in this table.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contract import CONTROL_OPERATIONS, OPERATIONS, READ_OPERATIONS

#: owner actor class recorded on control transitions (engine.state.OWNER_ACTORS)
_ACTOR_BY_CHANNEL = {"dashboard": "dashboard"}
_DEFAULT_ACTOR = "operator"


@dataclass(frozen=True)
class Principal:
    id: str
    operations: frozenset
    channels: frozenset

    def may(self, operation: str, channel: str) -> bool:
        return operation in self.operations and channel in self.channels


DEFAULT_PRINCIPALS = {
    "owner": Principal("owner", frozenset(OPERATIONS),
                       frozenset({"telegram", "dashboard", "openclaw", "whatsapp"})),
    # the local shell: reads plus the pre-existing `kernel --panic`; nothing else
    "local-operator": Principal("local-operator",
                                frozenset(READ_OPERATIONS + ("panic",)),
                                frozenset({"cli"})),
}
DEFAULT_IDENTITIES = {
    "dashboard": {"session": "owner"},        # the dashboard's single owner session
    "cli": {"local": "local-operator"},
    "telegram": {},                           # owner sender ids; see Kernel._owner
    "whatsapp": {},                           # NOT_CONFIGURED
    "openclaw": {},                           # NOT_CONFIGURED
}


def actor_for(channel: str) -> str:
    return _ACTOR_BY_CHANNEL.get(channel, _DEFAULT_ACTOR)


class Authorizer:
    def __init__(self, principals: dict | None = None, identities: dict | None = None):
        self.principals = dict(principals or DEFAULT_PRINCIPALS)
        self.identities = {ch: dict(m) for ch, m in (identities or DEFAULT_IDENTITIES).items()}

    @classmethod
    def from_config(cls, cfg: dict | None) -> "Authorizer":
        section = ((cfg or {}).get("owner_interface") or {})
        principals = dict(DEFAULT_PRINCIPALS)
        for pid, spec in (section.get("principals") or {}).items():
            ops = frozenset(spec.get("operations") or ())
            if not ops <= set(OPERATIONS):
                raise ValueError(f"principal {pid}: unknown operations {sorted(ops - set(OPERATIONS))}")
            principals[str(pid)] = Principal(str(pid), ops,
                                             frozenset(spec.get("channels") or ()))
        identities = {ch: dict(m) for ch, m in DEFAULT_IDENTITIES.items()}
        for ch, mapping in (section.get("identities") or {}).items():
            identities.setdefault(str(ch), {}).update(
                {str(k): str(v) for k, v in (mapping or {}).items()})
        return cls(principals, identities)

    def bind(self, channel: str, identity: str, principal: str) -> None:
        if principal not in self.principals:
            raise ValueError(f"unknown principal {principal!r}")
        self.identities.setdefault(channel, {})[str(identity)] = principal

    def resolve(self, channel: str, identity) -> str | None:
        if identity is None:
            return None
        return (self.identities.get(channel) or {}).get(str(identity))

    def authorize(self, channel: str, identity: str, operation: str
                  ) -> tuple[str | None, str | None]:
        """(principal, None) if allowed, else (principal | None, refusal reason)."""
        principal = self.resolve(channel, identity)
        if principal is None:
            return None, "identity_not_bound"
        p = self.principals.get(principal)
        if p is None:
            return principal, "unknown_principal"
        if not p.may(operation, channel):
            return principal, ("operation_not_permitted" if operation in CONTROL_OPERATIONS
                               else "read_not_permitted")
        return principal, None
