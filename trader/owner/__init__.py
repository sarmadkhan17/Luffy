"""LUFFY Owner Interface — one typed, kernel-owned surface for owner controls.

    OpenClaw / WhatsApp · Telegram · Dashboard · local CLI
            ↓ thin adapters (trader.owner.adapters) authenticate the owner
    OwnerRequest (trader.owner.contract) — carries identity, never principal
            ↓ in-kernel call (Telegram) or authenticated local IPC (trader.owner.ipc)
    OwnerService (trader.owner.service) — runs only inside the kernel process
            ↓
    ControlStateMachine / Supervisor guarded recovery / Risk release / kernel intents

Adapters never hold Execution, Risk, Supervisor, exchange or trading-secret
authority. They authenticate their own transport, build an OwnerRequest and
render the typed OwnerResult; every safety decision stays in the kernel.
"""
from .contract import (CONTROL_OPERATIONS, OPERATIONS, READ_OPERATIONS,
                       MalformedRequest, OwnerRequest, OwnerResult, Status)

__all__ = ["CONTROL_OPERATIONS", "OPERATIONS", "READ_OPERATIONS",
           "MalformedRequest", "OwnerRequest", "OwnerResult", "Status"]
