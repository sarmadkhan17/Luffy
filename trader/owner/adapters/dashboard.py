"""Dashboard adapter: an authenticated session's control click → OwnerRequest.

The dashboard process never touches control state. Its GraphQL resolver builds
a request here and sends it to the kernel over OwnerClient (IPC, dashboard
channel key). The browser creates the request id and its issue time once per
intended action and keeps both until a definitive answer, so a double-submit,
reconnect or reload maps to one execution and the kernel judges the age of
the owner's original click, not of the retry.
"""
from __future__ import annotations

import re

from ..contract import MalformedRequest, OwnerRequest

CHANNEL = "dashboard"
SESSION_IDENTITY = "session"          # DashboardAuth has one owner credential
_CLIENT_ID = re.compile(r"^[A-Za-z0-9-]{16,64}$")


def to_request(operation: str, client_request_id, issued_at_ms, *, authenticated: bool,
               args: dict | None = None) -> OwnerRequest | None:
    """None when the session is not authenticated. Raises MalformedRequest."""
    if not authenticated:
        return None
    if not isinstance(client_request_id, str) or not _CLIENT_ID.match(client_request_id):
        raise MalformedRequest("request_id must be 16-64 [A-Za-z0-9-]")
    if isinstance(issued_at_ms, bool) or not isinstance(issued_at_ms, (int, float)):
        raise MalformedRequest("issued_at_ms is required")
    op = str(operation).lower()
    return OwnerRequest(request_id=f"{CHANNEL}-{client_request_id}", operation=op,
                        channel=CHANNEL, identity=SESSION_IDENTITY,
                        issued_at=float(issued_at_ms) / 1000.0,
                        request_ref=client_request_id, args=args or {},
                        meta={"command": op})
