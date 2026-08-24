"""Luffy dashboard — FastAPI + GraphQL + WS push. Run: python -m trader.dashboard.server

Read-only against the journal except control mutations, which only write
*intent* (kv flags) — the kernel remains the single writer of truth.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from ..core.config import ROOT, load_config
from ..core.journal import Journal
from ..api.graphql_schema import make_graphql_router

log = logging.getLogger("dashboard")
WEB = Path(__file__).parent / "web"


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or load_config()
    journal = Journal(str(ROOT / "data" / "luffy.db"))
    app = FastAPI(title="Luffy")
    token = os.environ.get("DASH_TOKEN", "luffy")

    def authed(request) -> bool:
        q = request.query_params.get("token")
        h = request.headers.get("x-luffy-token")
        return token in (q, h) or token == "luffy"

    app.include_router(make_graphql_router(journal))

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (WEB / "index.html").read_text()

    @app.get("/api/summary", dependencies=[])
    async def summary():
        eq = journal.query(
            "SELECT * FROM equity ORDER BY ts DESC LIMIT 2")
        opens = journal.open_trades()
        hb_age = None
        try:
            hb_path = ROOT / "data" / "heartbeat_luffy.json"
            import time as t
            hb_age = round(t.time() - json.loads(hb_path.read_text())["timestamp"])
        except Exception:
            pass
        snap = _account_snapshot()
        assets = snap.get("assets", {})
        assets_total = snap.get("assets_total", 0)
        return {
            "control_state": journal.kv_get("control_state", "ACTIVE"),
            "market_type": journal.kv_get("market_type", "futures"),
            "heartbeat_age_s": hb_age,
            "equity": eq[0]["equity"] if eq else 0,
            "equity_prev": eq[1]["equity"] if len(eq) > 1 else None,
            "open_positions": opens,
            "assets": assets, "assets_total": assets_total,
            "today": _today_stats(journal),
        }

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                payload = {
                    "equity": journal.query(
                        "SELECT equity FROM equity ORDER BY ts DESC LIMIT 1"),
                    "open_trades": journal.open_trades(),
                    "recent_decisions": journal.query(
                        "SELECT ts,symbol,action,score,executed,skip_reason "
                        "FROM decisions ORDER BY ts DESC LIMIT 12"),
                }
                await ws.send_text(json.dumps(payload, default=str))
                await asyncio.sleep(3)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug(f"ws closed: {e}")

    _feed_cache: list = []

    @app.get("/api/klines")
    async def klines(symbol: str = "BTC/USDT", tf: str = "15m", limit: int = 300):
        from ..data.feed import DataFeed, make_exchange
        try:
            if not _feed_cache:
                _feed_cache.append(DataFeed(make_exchange("futures")))
            df = _feed_cache[0].fetch_ohlcv(symbol, tf, limit=limit,
                                            min_bars=1)
            if df is None:
                return {"candles": []}
            return {"candles": [
                {"time": int(r.ts.timestamp()), "open": float(r.open),
                 "high": float(r.high), "low": float(r.low),
                 "close": float(r.close), "volume": float(r.volume)}
                for r in df.itertuples()]}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=502)

    @app.get("/api/logs")
    async def logs(lines: int = 60):
        p = ROOT / "logs" / "luffy.log"
        if not p.exists():
            return {"tail": []}
        content = p.read_text(errors="replace").splitlines()[-lines:]
        return {"tail": content}

    @app.middleware("http")
    async def token_guard(request, call_next):
        # GraphQL mutations & api: require token unless running open (default)
        if request.url.path.startswith(("/api/", "/ws")) and not authed(request):
            return JSONResponse({"error": "bad token"}, status_code=401)
        return await call_next(request)

    return app


_ASSET_CACHE = {"ts": 0.0, "data": {}}


def _account_snapshot() -> dict:
    """Cross-asset wallet view (display truth). Cached 60s."""
    import hashlib
    import hmac as _hmac
    import time as _t

    import requests
    now = _t.time()
    if now - _ASSET_CACHE["ts"] < 60:
        return _ASSET_CACHE["data"]
    out = {}
    try:
        from ..core.config import Env
        key, secret = Env.binance_keys()
        q = f"timestamp={int(now*1000)}&recvWindow=10000"
        sig = _hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
        r = requests.get("https://demo-fapi.binance.com/fapi/v3/account",
                         params=q + f"&signature={sig}",
                         headers={"X-MBX-APIKEY": key}, timeout=8).json()
        assets = {x["asset"]: float(x["walletBalance"])
                  for x in r.get("assets", [])
                  if abs(float(x.get("walletBalance") or 0)) > 1e-9}
        px_btc = 0.0
        try:
            px_btc = float(requests.get(
                "https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT",
                timeout=6).json()["price"])
        except Exception:
            pass
        usd = {}
        for k, v in assets.items():
            if k in ("USDT", "USDC"):
                usd[k] = round(v, 2)
            elif k == "BTC" and px_btc:
                usd[k] = round(v * px_btc, 2)
            else:
                usd[k] = f"{v:.6f}"
        out = {"margin_equity": round(float(r.get("totalMarginBalance") or 0), 2),
               "assets": usd,
               "assets_total": round(sum(v for v in usd.values()
                                         if isinstance(v, (int, float))), 2)}
    except Exception as e:
        log.debug(f"account snapshot failed: {e}")
    _ASSET_CACHE.update(ts=now, data=out)
    return out


def _today_stats(journal: Journal) -> dict:
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    taken = journal.query(
        "SELECT COUNT(*) n FROM decisions WHERE executed=1 AND ts LIKE ?",
        (f"{day}%",))[0]["n"]
    skipped = journal.query(
        "SELECT COUNT(*) n FROM decisions WHERE executed=0 AND action!='HOLD' "
        "AND ts LIKE ?", (f"{day}%",))[0]["n"]
    pnl_rows = journal.query(
        "SELECT COALESCE(SUM(realized_pnl),0) s FROM trades WHERE closed_at LIKE ?",
        (f"{day}%",))
    return {"taken": taken, "skipped": skipped,
            "realized_pnl_today": round(pnl_rows[0]["s"], 2)}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    app = create_app(cfg)
    import uvicorn
    host = cfg["dashboard"]["host"]
    port = int(cfg["dashboard"]["port"])
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
