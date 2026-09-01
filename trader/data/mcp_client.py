"""A minimal MCP client the KERNEL can use — not just the coding agent.

`.mcp.json` configures MCP servers for an interactive agent. The kernel is a
long-running daemon, so it was never able to reach any of them; "harvest from
APIs and MCPs" needed an MCP client that lives inside the process.

This is that client, over Streamable HTTP (the transport the spec recommends
and the one every hosted server exposes). It speaks JSON-RPC 2.0 by hand
against `requests` rather than pulling in the MCP SDK — the whole protocol
surface we need is initialize / tools/list / tools/call, and a trading daemon
should not grow a dependency tree for three verbs.

Responses come back as SSE (`text/event-stream`) even for a single reply, so
`_parse` walks `data:` lines and takes the first frame carrying our id.

Scope note: hosted MCP servers vary in what they expose. CoinGecko's public
server, for instance, offers `execute` (run TypeScript against a
pre-authenticated SDK) and `search_docs` — an agent-shaped interface, not a
numeric feed. That is why the numeric Harvester prefers a REST source when
one exists and falls back to MCP for what only MCP serves.
"""
from __future__ import annotations

import json
import logging
import threading

import requests

log = logging.getLogger(__name__)

PROTOCOL = "2025-06-18"
TIMEOUT = 30


class MCPError(RuntimeError):
    pass


class MCPClient:
    """One session against one Streamable-HTTP MCP server.

    Sessions are re-established on demand, so a server restart or an expired
    session id costs one retry rather than a dead harvester thread.
    """

    def __init__(self, url: str, name: str = "", headers: dict | None = None,
                 timeout: int = TIMEOUT):
        self.url = url
        self.name = name or url
        self.timeout = timeout
        self._extra = dict(headers or {})
        self._session_id: str | None = None
        self._id = 0
        self._lock = threading.Lock()

    # ── wire ─────────────────────────────────────────────────────────────
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": PROTOCOL, **self._extra}
        if self._session_id:
            h["mcp-session-id"] = self._session_id
        return h

    @staticmethod
    def _parse(body: str, want_id: int | None):
        """Pull the JSON-RPC frame for `want_id` out of an SSE body."""
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                msg = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if want_id is None or msg.get("id") == want_id:
                return msg
        # some servers answer a single call as plain JSON
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    def _post(self, payload: dict, want_id: int | None):
        r = requests.post(self.url, headers=self._headers(),
                          json=payload, timeout=self.timeout)
        if r.status_code == 404 and self._session_id:
            self._session_id = None          # stale session — caller retries
            raise MCPError("session expired")
        r.raise_for_status()
        sid = r.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        return self._parse(r.text, want_id)

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    # ── protocol ─────────────────────────────────────────────────────────
    def connect(self) -> dict:
        rid = self._next_id()
        msg = self._post({"jsonrpc": "2.0", "id": rid, "method": "initialize",
                          "params": {"protocolVersion": PROTOCOL,
                                     "capabilities": {},
                                     "clientInfo": {"name": "luffy",
                                                    "version": "1.0"}}}, rid)
        if not msg or "result" not in msg:
            raise MCPError(f"{self.name}: initialize failed: {msg}")
        # the spec requires this notification before any other request
        try:
            self._post({"jsonrpc": "2.0",
                        "method": "notifications/initialized"}, None)
        except Exception:
            pass
        return msg["result"]

    def _call(self, method: str, params: dict | None = None) -> dict:
        for attempt in (0, 1):
            try:
                if self._session_id is None:
                    self.connect()
                rid = self._next_id()
                msg = self._post({"jsonrpc": "2.0", "id": rid,
                                  "method": method,
                                  "params": params or {}}, rid)
            except MCPError:
                if attempt:
                    raise
                continue                      # session was stale; reconnect
            if not msg:
                raise MCPError(f"{self.name}: no reply to {method}")
            if "error" in msg:
                raise MCPError(f"{self.name}: {msg['error']}")
            return msg.get("result", {})
        raise MCPError(f"{self.name}: {method} failed")

    def tools(self) -> list[dict]:
        with self._lock:
            return self._call("tools/list").get("tools", [])

    def call(self, tool: str, arguments: dict | None = None) -> dict:
        with self._lock:
            return self._call("tools/call",
                              {"name": tool, "arguments": arguments or {}})

    @staticmethod
    def text_of(result: dict) -> str:
        """Concatenate the text blocks of a tools/call result."""
        return "\n".join(c.get("text", "")
                         for c in (result or {}).get("content", [])
                         if c.get("type") == "text")


def from_config(cfg: dict) -> dict[str, MCPClient]:
    """Build clients from the `mcp:` block of config.yaml.

    Only `type: http` servers are usable here — a stdio server would need a
    subprocess per kernel thread, which is not something a trading daemon
    should own.
    """
    out: dict[str, MCPClient] = {}
    for name, spec in ((cfg.get("mcp") or {}).get("servers") or {}).items():
        if not spec.get("enabled", True):
            continue
        url = spec.get("url")
        if not url:
            log.warning(f"mcp server {name}: no url, skipped")
            continue
        out[name] = MCPClient(url, name=name, headers=spec.get("headers"))
    return out
