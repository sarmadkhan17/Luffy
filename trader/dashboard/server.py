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

        # ── live position marks + unrealized P&L ──
        marks = _position_marks(journal)
        total_upnl = sum(m["upnl"] for m in marks.values()
                         if isinstance(m.get("upnl"), (int, float)))
        long_exp = sum(m["notional"] for m in marks.values() if m["side"] == "long")
        short_exp = sum(m["notional"] for m in marks.values() if m["side"] == "short")
        for p in opens:
            m = marks.get(p["symbol"])
            if m:
                p["mark"] = m["mark"]
                p["upnl"] = round(m["upnl"], 2)
                p["upnl_pct"] = m["upnl_pct"]
                p["sl_dist"] = m["sl_dist"]
                p["tp_dist"] = m["tp_dist"]

        # ── strategy P&L + winrate + agent activity ──
        strat_pnl = journal.query(
            "SELECT COALESCE(NULLIF(strategy_name,''),'orchestrator') sname,"
            " ROUND(SUM(realized_pnl),2) pnl, COUNT(*) n "
            "FROM trades WHERE status='closed' GROUP BY sname "
            "HAVING COUNT(*)>0 ORDER BY pnl")
        closed = journal.query(
            "SELECT COUNT(*) n, SUM(realized_pnl>0) wins FROM trades "
            "WHERE status='closed'")[0]
        agents_now = journal.query("""
            SELECT v.agent, v.side, v.conviction, v.confidence, v.ts,
                   c.regime FROM votes v JOIN cycles c ON c.id=v.cycle_id
            WHERE v.ts > datetime('now','-15 minutes')
              AND v.rowid IN (SELECT MAX(rowid) FROM votes
                              GROUP BY agent) ORDER BY v.ts DESC LIMIT 6""")
        prices = _universe_prices()
        return {
            "control_state": journal.kv_get("control_state", "ACTIVE"),
            "market_type": journal.kv_get("market_type", "futures"),
            "heartbeat_age_s": hb_age,
            "equity": eq[0]["equity"] if eq else 0,
            "equity_prev": eq[1]["equity"] if len(eq) > 1 else None,
            "open_positions": opens,
            "assets": assets, "assets_total": assets_total,
            "total_upnl": round(total_upnl, 2),
            "long_exposure": round(long_exp, 0),
            "short_exposure": round(short_exp, 0),
            "strategy_pnl": strat_pnl,
            "winrate": (round(closed["wins"] / closed["n"] * 100, 1)
                        if closed["n"] else None),
            "closed_trades": closed["n"],
            "agents_now": agents_now,
            "prices": prices,
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
            "ma_cross": "Behavioral Momentum",
            "vwap_fade": "Statistical Mean Reversion",
            "bb_fade": "Statistical Mean Reversion",
            "rsi_extreme": "Statistical Mean Reversion",
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

    @app.get("/api/pipeline")
    async def pipeline():
        """Discovery→validation pipeline truth: funnel stats, recent
        gauntlet verdicts, population snapshot, harness state."""
        from ..brain.tv_harness import TVHarness

        def _detail(rows):
            out = []
            for r in rows:
                try:
                    d = json.loads(r["detail"])
                except Exception:
                    d = {}
                out.append({"ts": r["ts"], "kind": r["kind"],
                            "subject": r["subject"],
                            "title": (d.get("title")
                                      or d.get("family") or "")[:90],
                            "stage": d.get("stage")
                            or (d.get("tv") or {}).get("stage"),
                            "checks": (d.get("tv") or d).get(
                                "checks", (d.get("validation") or {})
                                .get("checks")),
                            "pnl_pct": (d.get("tv") or {}).get(
                                "net_profit_pct",
                                (d.get("tv") or {}).get("oos_return_pct")),
                            "folds": f"{(d.get('tv') or {}).get('positive_folds')}"
                                     f"/{(d.get('tv') or {}).get('fold_count')}",
                            "source": d.get("source")})
            return out

        cycles = journal.query(
            "SELECT ts, detail FROM brain_events WHERE kind='harvest_cycle' "
            "ORDER BY id DESC LIMIT 4")
        verdicts = _detail(journal.query(
            "SELECT ts, kind, subject, detail FROM brain_events "
            "WHERE kind IN ('harvest_accepted','harvest_rejected',"
            "'proposal_accepted','proposal_rejected') "
            "ORDER BY id DESC LIMIT 14"))
        tv = journal.query(
            "SELECT COUNT(*) n FROM brain_events WHERE kind='tv_backtest' "
            "AND detail LIKE '%\"ok\": true%'")[0]["n"]
        pop = {r["state"]: r["n"] for r in journal.query(
            "SELECT state, COUNT(*) n FROM strategies GROUP BY state")}
        # Harvested/analyst additions — duplicates retired as re-harvest are
        # excluded so the panel never shows the same strategy twice.
        harvested = [{"id": r["id"], "name": r["name"], "kind": r["kind"],
                      "state": r["state"], "params": r["params"]}
                     for r in journal.query(
                         "SELECT id, name, kind, state, params FROM strategies "
                         "WHERE origin IN ('harvested','analyst','brain') "
                         "AND COALESCE(retire_reason,'') NOT LIKE 'duplicate%' "
                         "ORDER BY CASE state WHEN 'active' THEN 0 "
                         "WHEN 'paper' THEN 1 WHEN 'demoted' THEN 2 "
                         "ELSE 3 END, created_at DESC LIMIT 12")]
        # full population book for the discovery table (active on top)
        book = [{"id": r["id"], "name": r["name"], "kind": r["kind"],
                 "state": r["state"], "origin": r["origin"],
                 "retire_reason": (r.get("retire_reason") or "")[:110]}
                for r in journal.query(
                    "SELECT id, name, kind, state, origin, retire_reason "
                    "FROM strategies ORDER BY CASE state "
                    "WHEN 'active' THEN 0 WHEN 'paper' THEN 1 "
                    "WHEN 'demoted' THEN 2 ELSE 3 END, created_at DESC")]
        try:
            h = TVHarness(journal, {"tv_harness":
                                    cfg.get("tv_harness", {})})
            health = h.health()
        except Exception:
            health = {"state": "?", "runs_today": 0, "budget": 0}
        return {
            "funnel": {"cycles": len(cycles),
                       "last_cycle": json.loads(cycles[0]["detail"])
                       if cycles else {},
                       "tv_ok_all_time": tv},
            "verdicts": verdicts,
            "population": {"by_state": pop, "harvested": harvested,
                           "book": book},
            "tv_health": {**health,
                          "budget_enabled": bool(
                              cfg.get("tv_harness", {})
                              .get("budget_enabled", True))},
        }

    @app.get("/api/agents_stats")
    async def agents_stats():
        """Agents tab: live activity (votes) + learned accuracy + weights.

        Accuracy needs outcomes resolved with 4h-forward correctness; every
        directional decision schedules one, so the columns fill over time."""
        act = journal.query("""
            SELECT v.agent,
                   COUNT(*) AS votes,
                   SUM(v.conviction>0.02) buys,
                   SUM(v.conviction<-0.02) sells,
                   ROUND(AVG(ABS(v.conviction)),3) avg_conv,
                   MAX(v.ts) last_vote
            FROM votes v
            WHERE v.ts >= datetime('now','-24 hours')
              AND v.agent NOT LIKE '%strategy%'
            GROUP BY v.agent""")
        acc_rows = journal.agent_accuracy(since_hours=24 * 30)
        acc = {r["agent"]: r for r in acc_rows}
        weights = {}
        try:
            p = ROOT / "data" / "agent_weights.json"
            if p.exists():
                weights = json.loads(p.read_text())
        except Exception:
            weights = {}
        rows = []
        seen = []
        for a in act:
            seen.append(a["agent"])
            ac = acc.get(a["agent"], {})
            w = weights.get(a["agent"])
            if isinstance(w, dict):
                w = w.get("weight")
            rows.append({
                "agent": a["agent"], "votes24h": a["votes"],
                "buys": a["buys"] or 0, "sells": a["sells"] or 0,
                "avg_conv": a["avg_conv"] or 0,
                "last_vote": a["last_vote"] or "",
                "acc_n": int(ac.get("n") or 0),
                "accuracy": round(float(ac.get("accuracy") or 0), 2),
                "weight": w})
        # accuracy-only agents with no recent votes still deserve a row
        for agent_name, ac in acc.items():
            if agent_name not in seen:
                w = weights.get(agent_name)
                if isinstance(w, dict):
                    w = w.get("weight")
                rows.append({"agent": agent_name, "votes24h": 0, "buys": 0,
                             "sells": 0, "avg_conv": 0, "last_vote": "",
                             "acc_n": int(ac.get("n") or 0),
                             "accuracy": round(float(ac.get("accuracy") or 0), 2),
                             "weight": w})
        pending = journal.query(
            "SELECT COUNT(*) n FROM outcomes WHERE resolved_at IS NULL")[0]["n"]
        scored = journal.query(
            "SELECT COUNT(*) n FROM outcomes WHERE correct_4h IS NOT NULL")[0]["n"]
        return {"rows": sorted(rows, key=lambda x: -x["votes24h"]),
                "outcomes_pending": pending, "outcomes_scored": scored}

    @app.get("/api/org")
    async def org():
        """Company cockpit: roster + live per-employee status."""
        return build_company(journal, cfg)

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


_MARKS_CACHE = {"ts": 0.0, "data": {}}


def _position_marks(journal) -> dict:
    """symbol → {mark, upnl, upnl_pct, notional, side, sl_dist, tp_dist} (20s cache)."""
    import time as _t
    now = _t.time()
    if now - _MARKS_CACHE["ts"] < 20:
        return _MARKS_CACHE["data"]
    out = {}
    try:
        from ..data.feed import DataFeed, make_exchange
        global _marks_feed
        try:
            _marks_feed
        except NameError:
            _marks_feed = DataFeed(make_exchange("futures"))
        for t in journal.open_trades():
            sym = t["symbol"]
            px = _marks_feed.price(sym)
            if not px:
                continue
            direction = 1.0 if t["side"] == "long" else -1.0
            entry = float(t["entry_price"])
            amt = float(t["amount"])
            lev = int(t.get("leverage") or 1)
            upnl = (px - entry) * direction * amt
            notional = amt * entry
            sl, tp = float(t.get("stop_loss") or 0), float(t.get("take_profit") or 0)
            out[sym] = {
                "mark": round(px, 6), "upnl": upnl,
                "upnl_pct": round((px - entry) / entry * 100 * direction, 2),
                "notional": notional, "side": t["side"],
                "sl_dist": (abs(entry - sl) / entry * 100) if sl else None,
                "tp_dist": (abs(tp - entry) / entry * 100) if tp else None,
            }
    except Exception as e:
        log.debug(f"position marks failed: {e}")
    _MARKS_CACHE.update(ts=now, data=out)
    return out


_PRICE_CACHE = {"ts": 0.0, "data": {}}


def _universe_prices() -> dict:
    import time as _t
    now = _t.time()
    if now - _PRICE_CACHE["ts"] < 15:
        return _PRICE_CACHE["data"]
    out = {}
    try:
        from ..data.feed import DataFeed, make_exchange
        global _price_feed
        try:
            _price_feed
        except NameError:
            _price_feed = DataFeed(make_exchange("futures"))
        for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
                    "XRP/USDT", "ZEC/USDT", "AAVE/USDT", "SUI/USDT",
                    "HYPE/USDT", "NEAR/USDT"):
            px = _price_feed.price(sym)
            if px:
                out[sym] = px
    except Exception as e:
        log.debug(f"prices failed: {e}")
    _PRICE_CACHE.update(ts=now, data=out)
    return out


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
    holds = journal.query(
        "SELECT COUNT(*) n FROM decisions WHERE action='HOLD' AND ts LIKE ?",
        (f"{day}%",))[0]["n"]
    return {"taken": taken, "skipped": skipped, "holds": holds,
            "realized_pnl_today": round(pnl_rows[0]["s"], 2)}


def _age_min(ts: str | None) -> float | None:
    """Minutes since an ISO-ish UTC timestamp, or None if unparseable."""
    if not ts:
        return None
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 60.0
    except Exception:
        return None


def _state_from_age(mins: float | None, active=30, idle=1440) -> str:
    if mins is None:
        return "stale"
    if mins <= active:
        return "active"
    if mins <= idle:
        return "idle"
    return "stale"


def _short_detail(detail: str) -> str:
    try:
        d = json.loads(detail)
    except Exception:
        return str(detail)[:80]
    for k in ("title", "verdict", "subject", "family", "name", "reason"):
        if d.get(k):
            return str(d[k])[:80]
    if "accepted" in d:
        return f"accepted {d['accepted']}"
    return ", ".join(f"{k}={v}" for k, v in list(d.items())[:2])[:80]


def build_company(journal, cfg: dict) -> dict:
    """Roster + live per-employee status for the Company cockpit.

    Read-only: sources everything from the journal (+ vault mtimes). Never makes
    network calls, so it stays fast and testable. Each employee's status is
    derived per its declarative `status_source` in org.yaml.
    """
    from .. import org as orgmod
    org = orgmod.Org.load()

    acc = {r["agent"]: r for r in journal.agent_accuracy(since_hours=24 * 30)}
    last_votes = {r["agent"]: r for r in journal.query(
        "SELECT agent, side, conviction, ts FROM votes "
        "WHERE rowid IN (SELECT MAX(rowid) FROM votes GROUP BY agent)")}
    opens = journal.open_trades()
    control = journal.kv_get("control_state", "ACTIVE")

    def analyst(e):
        a = acc.get(e.agent_key, {})
        lv = last_votes.get(e.agent_key, {})
        n = int(a.get("n") or 0)
        metric = (f"{float(a['accuracy'])*100:.0f}% acc ({n})"
                  if a.get("accuracy") is not None and n else "no data yet")
        out = (f"{lv.get('side','')} conv {float(lv.get('conviction',0)):+.2f}"
               if lv else "no recent vote")
        ts = lv.get("ts")
        return metric, out, ts, _state_from_age(_age_min(ts)), []

    def events(e):
        kinds = e.events or []
        ph = ",".join("?" * len(kinds)) or "''"
        rows = journal.query(
            f"SELECT ts, kind, subject, detail FROM brain_events "
            f"WHERE kind IN ({ph}) ORDER BY id DESC LIMIT 8", tuple(kinds))
        n24 = journal.query(
            f"SELECT COUNT(*) n FROM brain_events WHERE kind IN ({ph}) "
            f"AND ts >= datetime('now','-24 hours')", tuple(kinds))
        n = n24[0]["n"] if n24 else 0
        latest = rows[0] if rows else {}
        out = (f"{latest['kind']}: {_short_detail(latest.get('detail','{}'))}"
               if latest else "no activity")
        feed = [{"ts": r["ts"],
                 "text": f"{r['kind']} · {_short_detail(r.get('detail','{}'))}"}
                for r in rows]
        ts = latest.get("ts")
        return f"{n}/24h", out, ts, _state_from_age(_age_min(ts)), feed

    def trader(e):
        last = journal.query(
            "SELECT COALESCE(closed_at, opened_at) ts, symbol, realized_pnl "
            "FROM trades ORDER BY id DESC LIMIT 6")
        l0 = last[0] if last else {}
        out = (f"{l0.get('symbol','')} {float(l0.get('realized_pnl') or 0):+.2f}"
               if l0 else "no trades")
        feed = [{"ts": r["ts"],
                 "text": f"{r['symbol']} {float(r.get('realized_pnl') or 0):+.2f}"}
                for r in last]
        state = "active" if opens else _state_from_age(_age_min(l0.get("ts")))
        return f"{len(opens)} open", out, l0.get("ts"), state, feed

    def risk(e):
        ng = str(journal.kv_get("news_guard_state", "") or "")
        armed = control in ("HALTED", "FROZEN") or "arm" in ng.lower()
        exp = None
        try:
            eqr = journal.query("SELECT equity FROM equity ORDER BY ts DESC LIMIT 1")
            eq = float(eqr[0]["equity"]) if eqr else 0
            notional = sum(float(t.get("notional_usdt") or t.get("notional") or 0)
                           for t in opens)
            if eq:
                exp = notional / eq  # leveraged exposure as a multiple of equity
        except Exception:
            pass
        metric = f"{len(opens)} pos"
        out = ("guard armed / frozen" if armed else
               (f"exposure {exp:.1f}x equity" if exp is not None
                else f"{len(opens)} positions · limits nominal"))
        state = "alert" if armed else ("active" if opens else "idle")
        return metric, out, None, state, []

    def manager(e):
        cyc = journal.query("SELECT ts, id FROM cycles ORDER BY id DESC LIMIT 1")
        eqr = journal.query("SELECT equity FROM equity ORDER BY ts DESC LIMIT 1")
        eq = float(eqr[0]["equity"]) if eqr else 0
        ts = cyc[0]["ts"] if cyc else None
        metric = f"eq ${eq:,.0f}" if eq else control
        out = f"{control} · last cycle" + (f" {_age_min(ts):.0f}m ago"
                                           if _age_min(ts) is not None else "")
        state = ("alert" if control in ("HALTED", "FROZEN")
                 else _state_from_age(_age_min(ts)))
        return metric, out, ts, state, []

    def librarian(e):
        from ..knowledge.vault import VAULT
        from datetime import datetime, timezone
        md = list(VAULT.rglob("*.md"))
        newest = max(md, key=lambda p: p.stat().st_mtime, default=None)
        ts = (datetime.fromtimestamp(newest.stat().st_mtime, timezone.utc)
              .isoformat() if newest else None)
        out = f"latest: {newest.stem}" if newest else "empty vault"
        return f"{len(md)} notes", out, ts, _state_from_age(_age_min(ts)), []

    handlers = {"analyst": analyst, "events": events, "trader": trader,
                "risk": risk, "manager": manager, "librarian": librarian}

    entries = []
    for e in org.all():
        fn = handlers.get(e.status_source)
        if fn:
            metric, out, ts, state, feed = fn(e)
        else:
            metric, out, ts, state, feed = "—", "", None, "idle", []
        entries.append({
            "name": e.name, "title": e.title, "reports_to": e.reports_to,
            "desc": e.desc, "wraps": e.wraps,
            "node": f"00 Company/{e.name}.md",
            "status": state, "metric": metric, "last_output": out,
            "last_activity": ts, "feed": feed,
        })
    from datetime import datetime, timezone
    return {"employees": entries,
            "generated_at": datetime.now(timezone.utc).isoformat()}


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
