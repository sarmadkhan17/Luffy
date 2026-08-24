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

    def authed_from(q, h) -> bool:
        return token in (q, h) or token == "luffy"

    def authed(request) -> bool:
        return authed_from(request.query_params.get("token"),
                           request.headers.get("x-luffy-token"))

    def _nocache(response):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    app.include_router(make_graphql_router(journal))

    @app.get("/", response_class=HTMLResponse)
    async def index():
        from fastapi import Response
        html = (WEB / "index.html").read_text()
        return _nocache(Response(html, media_type="text/html"))

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
        q = dict(ws.query_params).get("token")
        h = ws.headers.get("x-luffy-token")
        if not (token in (q, h) or token == "luffy"):
            await ws.close(code=4401)
            return
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

    @app.get("/api/vault/tree")
    async def vault_tree():
        from ..knowledge.vault import VAULT
        items = []
        for p in sorted(VAULT.rglob("*.md")):
            rel = str(p.relative_to(VAULT))
            items.append({"folder": str(p.parent.relative_to(VAULT)),
                          "name": p.stem, "path": rel,
                          "size": p.stat().st_size})
        return {"root": "knowledge", "files": items}

    @app.get("/api/vault/file")
    async def vault_file(path: str):
        from ..knowledge.vault import VAULT
        target = (VAULT / path).resolve()
        if not str(target).startswith(str(VAULT.resolve())) or                 target.suffix != ".md" or not target.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"path": path, "content": target.read_text(errors="replace")}

    @app.get("/api/vault/graph")
    async def vault_graph():
        """Nodes = notes, edges = wikilinks (+ family→theory synapses).
        This is the shape of Luffy's memory."""
        import re
        from ..knowledge.vault import VAULT, THEORY_NOTES

        FAMILY_THEORY = {
            "ema_trend": "Behavioral Momentum",
            "breakout_retest": "Behavioral Momentum",
            "vwap_fade": "Statistical Mean Reversion",
            "sweep_reversal": "Auction Market Theory",
            "rotation_momo": "Cross-Asset Rotation",
        }
        FOLDER_COLOR = {
            "10 Theories": "#3498db", "20 Strategies": "#2ecc71",
            "30 Postmortems": "#e74c3c", "40 Regimes": "#f1c40f",
            "50 Daily": "#7a8593", "": "#9b59b6",
        }
        files = {}
        for p in sorted(VAULT.rglob("*.md")):
            rel = str(p.relative_to(VAULT))
            folder = str(p.parent.relative_to(VAULT)) \
                if p.parent != VAULT else ""
            files[rel] = {"stem": p.stem, "folder": folder,
                          "text": p.read_text(errors="replace")}

        stems = {v["stem"]: rel for rel, v in files.items()}

        def resolve(link: str) -> str | None:
            link = link.split("|")[0].split("#")[0].strip()
            if link in stems:
                return stems[link]
            for rel, v in files.items():
                if v["stem"] == link:
                    return rel
            return None

        nodes, edges, seen = [], [], set()
        for rel, v in files.items():
            nodes.append({"id": rel, "label": v["stem"],
                          "group": v["folder"],
                          "color": FOLDER_COLOR.get(v["folder"], "#888")})
            for m in re.findall(r"\[\[([^\]]+)\]\]", v["text"]):
                tgt = resolve(m)
                if tgt and tgt != rel:
                    key = tuple(sorted((rel, tgt)))
                    if key not in seen:
                        seen.add(key)
                        edges.append({"from": rel, "to": tgt})
            # family synapse: strategy genome → its parent theory
            fm = re.search(r"family:\s*(\w+)", v["text"])
            if fm and fm.group(1) in FAMILY_THEORY:
                th_rel = stems.get(FAMILY_THEORY[fm.group(1)])
                if th_rel and th_rel != rel:
                    key = tuple(sorted((rel, th_rel)))
                    if key not in seen:
                        seen.add(key)
                        edges.append({"from": rel, "to": th_rel,
                                      "synapse": True})
        # orphan theories still pulse: ensure theory notes exist as nodes even
        # without links (they always will — seeded)
        return {"nodes": nodes, "edges": edges}

    @app.get("/api/doctrine")
    async def doctrine():
        from ..core.config import ROOT
        p = ROOT / "data" / "doctrine.json"
        if not p.exists():
            return {"version": 0, "beliefs": []}
        return json.loads(p.read_text())

    _autopsy_lock = {"busy": False}

    @app.post("/api/chat")
    async def chat(body: dict):
        from ..chat.engine import ChatEngine
        msg = (body.get("message") or "").strip()
        if not msg:
            return JSONResponse({"reply": "say something?"}, status_code=400)
        history = body.get("history") or []

        def _run():
            return ChatEngine(journal, cfg).handle(msg, history)
        import asyncio
        reply = await asyncio.get_event_loop().run_in_executor(None, _run)
        return {"reply": reply}

    @app.post("/api/brain/autopsy")
    async def run_autopsy():
        if _autopsy_lock["busy"]:
            return JSONResponse({"ran": False,
                                 "reason": "autopsy already running"},
                                status_code=409)
        _autopsy_lock["busy"] = True
        try:
            import asyncio
            from ..brain.theorist import Theorist
            loop = asyncio.get_event_loop()
            rep = await loop.run_in_executor(
                None, lambda: Theorist(journal, cfg).run_autopsy())
            return rep
        except Exception as e:
            return JSONResponse({"ran": False, "reason": str(e)},
                                status_code=500)
        finally:
            _autopsy_lock["busy"] = False

    @app.get("/api/brain/last_autopsy")
    async def last_autopsy():
        rows = journal.query(
            "SELECT ts,detail FROM brain_events WHERE kind='autopsy' "
            "ORDER BY id DESC LIMIT 1")
        if not rows:
            return {"ts": None}
        try:
            detail = json.loads(rows[0]["detail"])
        except Exception:
            detail = {"summary": rows[0]["detail"]}
        return {"ts": rows[0]["ts"], **detail}

    @app.get("/api/review_status")
    async def review_status():
        closed = journal.query(
            "SELECT COUNT(*) n FROM trades WHERE status='closed'")[0]["n"]
        last_brain = journal.query(
            "SELECT ts,kind FROM brain_events ORDER BY id DESC LIMIT 1")
        return {
            "closed_trades": closed,
            "review_trigger_trades": 8,
            "trades_until_review": max(0, 8 - closed),
            "last_brain_event": last_brain[0] if last_brain else None,
        }

    @app.get("/api/logs")
    async def logs(lines: int = 60):
        p = ROOT / "logs" / "luffy.log"
        if not p.exists():
            return {"tail": []}
        content = p.read_text(errors="replace").splitlines()[-lines:]
        return {"tail": content}

    class TokenGuard:                    # raw ASGI: never touches websockets
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and scope["path"].startswith("/api/"):
                qs = dict(pair.split("=", 1) for pair in
                          scope.get("query_string", b"").decode().split("&")
                          if "=" in pair)
                hdrs = {k.decode().lower(): v.decode()
                        for k, v in scope.get("headers", [])}
                if not authed_from(qs.get("token"), hdrs.get("x-luffy-token")):
                    await JSONResponse({"error": "bad token"},
                                       status_code=401)(scope, receive, send)
                    return
            await self.app(scope, receive, send)

    app.add_middleware(TokenGuard)

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
