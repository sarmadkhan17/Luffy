"""Live-contract test server for the owner frontend browser tests.

The real dashboard app (auth Guard, GraphQL owner mutations, /owner-api/v1,
/ serving the LIVE build) over a temporary ROOT, with the kernel
Owner Interface and the chat LLM replaced by fakes. Test-only /__test__ routes
switch scenarios. Never touches production data, IPC or the venue.

    python -m tests.owner_frontend_server [port]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from tests.owner_frontend_fixture import (FakeChat, add_history, close_trade,  # noqa: E402
                                          insert_trade, make_app, reopen_trade, seed,
                                          seed_m2)
from trader.dashboard import auth as auth_mod  # noqa: E402


def build():
    root = Path(tempfile.mkdtemp(prefix="owner-frontend-e2e-"))
    app, journal, gateway = make_app(root, startup_mode=os.environ.get("GUI01_TEST_MODE", "LIVE"))
    fail: set[str] = set()
    # a blocking journal read of the decisions table, as slow as the production
    # journal's full scan; it must never stall the server's other requests
    # (class-level: the app reads through its own Journal instance)
    from trader.core.journal import Journal
    if os.environ.get("GUI01_TEST_MODE") == "READ_ONLY_GUI":
        from trader import runtime_identity
        (root / "data/kernel.lock").touch()
        runtime_identity.legacy_kernel_pids = lambda: []
    slow = {"decisions": 0.0}
    real_query = Journal.query

    def query(self, sql, params=()):
        if slow["decisions"] and "FROM decisions" in sql:
            time.sleep(slow["decisions"])
        return real_query(self, sql, params)
    Journal.query = query

    @app.middleware("http")
    async def inject_failure(request: Request, call_next):
        if any(request.url.path.startswith(p) for p in fail):
            return JSONResponse({"error": "injected_failure"}, status_code=500)
        return await call_next(request)

    @app.post("/__test__/state")
    async def state(request: Request):
        body = await request.json()
        if "scenario" in body:
            seed(root, journal, body["scenario"])
        if body.get("m2"):
            seed_m2(root, journal)
        if "history" in body:
            add_history(journal, int(body["history"]))
        if "insert_trade" in body:
            t = body["insert_trade"]
            insert_trade(journal, t["id"], t["symbol"], t["opened_at"],
                         t.get("status", "open"))
        if "accounting_close" in body:
            correction = body["accounting_close"]
            journal.close_trade("t-btc", 60000, correction["pnl"], "panic",
                                accounting=correction["evidence"])
        if "close_trade" in body:
            close_trade(journal, body["close_trade"])
        if "reopen_trade" in body:
            reopen_trade(journal, body["reopen_trade"])
        if "gateway" in body:
            gateway.mode = body["gateway"]
        if "chat" in body:
            FakeChat.mode = body["chat"]
        if "slow_decisions" in body:
            slow["decisions"] = float(body["slow_decisions"])
        if "fail" in body:
            fail.clear()
            fail.update(body["fail"])
        if "ttl" in body:
            auth_mod.TTL = int(body["ttl"])
        if body.get("reset_calls"):
            gateway.calls.clear()
            gateway.records.clear()
            FakeChat.calls.clear()
        return {"ok": True}

    @app.get("/__test__/calls")
    def calls():
        return {"gateway": gateway.calls, "chat": FakeChat.calls,
                "control_state": journal.kv_get("control_state"), "pid": os.getpid()}

    return app


if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4176
    uvicorn.run(build(), host="127.0.0.1", port=port, log_level="warning", access_log=False)
