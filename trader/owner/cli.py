"""Local owner CLI through the kernel Owner Interface (cli channel).

    ./venv/bin/python -m trader.owner.cli status|health|panic

The `local-operator` principal may read and queue the pre-existing panic;
nothing else. No local fallback: an unreachable kernel changes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from ..core.config import ROOT, load_config
from .contract import OwnerRequest, new_request_id
from .ipc import OwnerClient, resolve_ipc_dir

CLI_OPERATIONS = ("status", "health", "panic")


def call(operation: str, cfg: dict | None = None):
    section = (cfg or load_config()).get("owner_interface") or {}
    ipc_dir = resolve_ipc_dir(section.get("ipc_dir"))
    if ipc_dir is not None and not ipc_dir.is_absolute():
        ipc_dir = ROOT / ipc_dir
    req = OwnerRequest(new_request_id("cli"), operation, "cli", "local",
                       issued_at=time.time(), request_ref="local")
    if ipc_dir is None:
        from .contract import Status, refused
        return refused(req, "owner_interface_not_configured", Status.UNAVAILABLE)
    return OwnerClient(ipc_dir, "cli").call(req)


def main() -> None:
    ap = argparse.ArgumentParser(prog="luffy-owner")
    ap.add_argument("operation", choices=CLI_OPERATIONS)
    result = call(ap.parse_args().operation)
    print(json.dumps(result.to_wire(), indent=2, default=str))
    sys.exit(0 if result.status in ("ACCEPTED", "ALREADY_SET") else 1)


if __name__ == "__main__":
    main()
