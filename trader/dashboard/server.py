"""Luffy dashboard — FastAPI + GraphQL + WS push. Run: python -m trader.dashboard.server

Read-only against the journal except control mutations, which only write
*intent* (kv flags) — the kernel remains the single writer of truth.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ..core.config import ROOT, load_config
from ..core.journal import Journal
from ..api.graphql_schema import make_graphql_router
from .enrichment import Enrichment
from .local_view import (Busy, ReadOnlyStore, SharedTTL, StoreUnavailable,
                         live_payload, overview as local_overview, tail_lines)

log = logging.getLogger("dashboard")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
WEB = Path(__file__).parent / "web"
#: how long /api/enrichment waits for a running venue refresh before
#: answering with the cached (possibly stale/unavailable) view
ENRICH_WAIT_S = 1.5
PIPELINE_BOOK_ROWS = 500


def create_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or load_config()
    journal = Journal(str(ROOT / "data" / "luffy.db"))
    app = FastAPI(title="Luffy")
    from .auth import DashboardAuth
    DashboardAuth(os.environ.get("DASH_TOKEN")).install(app)

    def _nocache(response):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    app.include_router(make_graphql_router(journal))
    # Owner read paths use a read-only connection: no schema/migration work
    # and no write capability on a request, unlike Journal.__init__.
    store = ReadOnlyStore(ROOT / "data" / "luffy.db")
    shared = SharedTTL()
    # per app, never process-global: cached venue values stay bound to this
    # app's configured account/routing (see enrichment.Enrichment)
    enrichment = Enrichment()
    app.state.enrichment = enrichment

    async def _shared(key, ttl, fn):
        """Shared value plus cache metadata; waiting holds no worker token."""
        try:
            value, meta = await shared.get(key, ttl, fn)
        except Busy as e:
            return None, {"stale": True, "reason": str(e)}
        return value, meta

    def _json(value, meta):
        # served_at is this response's server time; a cached snapshot keeps
        # its own generated_at, so clients can age a stale fallback correctly
        meta = {**meta, "served_at": _now_iso()}
        if value is None:            # saturated, nothing computed yet
            r = JSONResponse({"status": "busy", "cache": meta}, status_code=503,
                             headers={"Retry-After": "2"})
        else:
            if isinstance(value, dict):
                value = {**value, "cache": meta}
            r = JSONResponse(value)
        return _nocache(r)

    async def _overview():
        return await _shared("overview", 2.0,
                             lambda: local_overview(store, ROOT))

    @app.get("/", response_class=HTMLResponse)
    def index():
        from fastapi import Response
        html = (WEB / "index.html").read_text()
        return _nocache(Response(html, media_type="text/html"))

    @app.get("/attention.js")
    async def attention_script():
        from fastapi.responses import FileResponse
        return _nocache(FileResponse(WEB / "attention.js", media_type="application/javascript"))

    @app.get("/investigation.js")
    async def investigation_script():
        from fastapi.responses import FileResponse
        return _nocache(FileResponse(WEB / "investigation.js", media_type="application/javascript"))

    @app.get("/api/investigations/latest")
    def investigations_latest():
        from ..observability.investigation import owner_view
        return _nocache(JSONResponse(owner_view(ROOT / "data" / "investigation.db")))

    @app.get("/api/attention/latest")
    def attention_latest():
        from ..observability.attention import settings
        from ..observability.store import read_latest
        raw = cfg.get("attention") or {}
        if raw.get("enabled") is not True:
            return _nocache(JSONResponse({"status": "disabled", "scan": None, "causes": []}))
        try:
            acfg = settings(raw)
        except ValueError:
            return _nocache(JSONResponse({"status": "configuration_error", "scan": None, "causes": []}))
        healths = []
        for filename, field in (("attention_health.json", None), ("heartbeat_luffy.json", "attention")):
            try:
                data = json.loads((ROOT / "data" / filename).read_text())
                health = data.get(field) if field else data
                if isinstance(health, dict):
                    healths.append(health)
            except (OSError, ValueError):
                pass
        health = max(healths, key=lambda h: h.get("updated_ms", 0), default={})
        result = read_latest(ROOT / "data" / "attention.db", health=health,
                             stale_seconds=acfg["stale_seconds"])
        if (cfg.get("attention_learning") or {}).get("enabled"):
            from ..observability.learning import read_health
            result["learning"] = read_health(ROOT / "data" / "attention_learning_health.json")
        from ..engine.recovery import read as read_recovery
        try:
            recovery = read_recovery(journal)
            result["execution_recovery"] = ({k: recovery.get(k) for k in
                ("id", "symbol", "phase", "reason", "attempts", "updated_ms", "error_type")}
                if recovery else None)
        except (ValueError, TypeError):
            result["execution_recovery"] = {"reason": "recovery_ledger_unreadable"}
        return _nocache(JSONResponse(result))

    @app.get("/api/overview")
    async def overview():
        """First useful Overview data: local journal/files only, no venue."""
        return _json(*await _overview())

    @app.get("/api/enrichment")
    async def enrichment_view():
        """Optional wallet/ticker enrichment, separate from local data.

        Joins (never duplicates) a running refresh for up to ENRICH_WAIT_S;
        a caller that stops waiting leaves the job running to completion."""
        ov, _ = await _overview()
        positions = ((ov or {}).get("positions") or {}).get("rows") or []
        jobs = await run_in_threadpool(
            enrichment.refresh, [p["symbol"] for p in positions])
        if jobs:
            await asyncio.wait([asyncio.wrap_future(j) for j in jobs],
                               timeout=ENRICH_WAIT_S)
        return _nocache(JSONResponse(enrichment.view(positions)))

    def _summary_trades():
        # the full open book with every journal column, as the legacy
        # summary returned it (not the Overview's capped display rows)
        try:
            return store.query("SELECT * FROM trades WHERE status='open'")
        except StoreUnavailable:
            return None

    @app.get("/api/summary", dependencies=[])
    def summary_v1_retired():
        """v1 is retired rather than silently changed: its fabricated zeros /
        ACTIVE defaults cannot be served honestly (see /api/v2/summary)."""
        return _nocache(JSONResponse({
            "error": "gone", "retired": "/api/summary (v1)",
            "use": "/api/v2/summary",
            "migration": ("v2 keeps the v1 keys, but a missing or incomplete "
                          "value is null instead of 0/'ACTIVE'/'futures'; "
                          "exposure is entry notional; assets_total is null "
                          "unless every asset converted (see "
                          "assets_known_usd_subtotal); per-source ages are in "
                          "'freshness'.")}, status_code=410))

    @app.get("/api/v2/summary")
    async def summary():
        """summary v2 — the v1 keys composed from local data plus already-
        cached enrichment. Never waits on the venue. A value that is missing
        or incomplete is null (equity, control_state, market_type, exposure,
        total_upnl, assets_total); exposure is entry notional. See
        `freshness` for the age and completeness of each source."""
        ov, meta = await _overview()
        if ov is None:
            return _json(None, meta)
        rows = await run_in_threadpool(_summary_trades)
        positions = [dict(p) for p in rows or []]
        await run_in_threadpool(enrichment.refresh,
                                [p["symbol"] for p in positions])
        ev = enrichment.view(positions)
        marks = ev["marks"]["rows"]
        for p in positions:
            entry, sl, tp = p.get("entry_price"), p.get("stop_loss"), p.get("take_profit")
            p["sl_dist"] = abs(entry - sl) / entry * 100 if entry and sl else None
            p["tp_dist"] = abs(tp - entry) / entry * 100 if entry and tp else None
            m = marks.get(p["id"])
            if m:
                p.update(mark=m["mark"], upnl=m["upnl_estimate"],
                         upnl_pct=m["upnl_pct"], mark_freshness=m["freshness"])
        eq, hb = ov.get("equity") or {}, ov.get("heartbeat") or {}
        acct = ev["account"].get("value") or {}
        perf, ctl = ov.get("performance") or {}, ov.get("control") or {}
        prices = ((ev["tickers"].get("value") or {}).get("prices") or {})
        exposure = (ov.get("positions") or {}).get("entry_notional") or {}
        all_marked = rows is not None and all(p["id"] in marks for p in positions)
        return _nocache(JSONResponse({
            "schema": "luffy.summary.v2",
            "control_state": ctl.get("control_state"),
            "market_type": ctl.get("market_type"),
            "heartbeat_age_s": hb.get("age_s"),
            "equity": eq.get("equity"), "equity_prev": eq.get("equity_prev"),
            # None, not [], when the journal could not be read
            "open_positions": positions if rows is not None else None,
            # converted USD values plus unconverted asset strings, as in v1
            "assets": ({**acct.get("assets_usd", {}), **acct.get("assets_unconverted", {})}
                       if acct else {}),
            # null unless every asset was converted; the known part is labelled
            "assets_total": (acct.get("assets_usd_total")
                             if acct.get("assets_usd_total_complete") else None),
            "assets_known_usd_subtotal": acct.get("assets_usd_total"),
            "assets_total_complete": (bool(acct.get("assets_usd_total_complete"))
                                      if acct else None),
            "total_upnl": (round(sum(m["upnl_estimate"] for m in marks.values()), 2)
                           if all_marked else None),
            "long_exposure": exposure.get("long"),
            "short_exposure": exposure.get("short"),
            "strategy_pnl": perf.get("strategy_pnl"),
            "winrate": perf.get("winrate"),
            "closed_trades": perf.get("closed_trades"),
            "agents_now": (ov.get("agents") or {}).get("rows"),
            "prices": {k: v["price"] for k, v in prices.items()},
            "today": ov.get("today"),
            "freshness": {
                "local_cache": meta,
                "heartbeat": {k: hb.get(k) for k in ("freshness", "age_s")},
                "equity": {k: eq.get(k) for k in ("freshness", "age_s", "source")},
                "account": {k: ev["account"].get(k) for k in (
                    "freshness", "age_s", "retrieved_at", "refreshing",
                    "last_refresh_failed")},
                "tickers": {k: ev["tickers"].get(k) for k in (
                    "freshness", "age_s", "retrieved_at", "refreshing",
                    "last_refresh_failed")},
                "exposure_complete": exposure.get("complete"),
                "assets_total_complete": (bool(acct.get("assets_usd_total_complete"))
                                          if acct else None),
                "total_upnl_complete": all_marked,
            },
        }))

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await ws.accept()

        def _frame():
            try:
                return json.dumps(live_payload(store), default=str)
            except StoreUnavailable:
                return json.dumps({"error": "journal_unavailable"})
        try:
            while True:
                # one query set + serialization per interval, shared by every
                # connection, computed off the event loop
                payload, _ = await _shared("live", 2.5, _frame)
                if payload is not None:
                    await ws.send_text(payload)
                await asyncio.sleep(3)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug(f"ws closed: {e}")

    _feed_cache: list = []

    @app.get("/api/klines")
    def klines(symbol: str = "BTC/USDT", tf: str = "15m", limit: int = 300):
        from ..data.feed import DataFeed
        try:
            if not _feed_cache:
                # DataFeed() reads PRODUCTION public data. An injected
                # make_exchange("futures") follows BINANCE_DEMO, and every
                # chart view merged simulated candles into the shared store.
                _feed_cache.append(DataFeed())
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
    def vault_tree():
        from ..knowledge.vault import VAULT
        items = []
        for p in sorted(VAULT.rglob("*.md")):
            rel = str(p.relative_to(VAULT))
            items.append({"folder": str(p.parent.relative_to(VAULT)),
                          "name": p.stem, "path": rel,
                          "size": p.stat().st_size})
        return {"root": "knowledge", "files": items}

    @app.get("/api/vault/file")
    def vault_file(path: str):
        from ..knowledge.vault import VAULT
        target = (VAULT / path).resolve()
        if not str(target).startswith(str(VAULT.resolve())) or                 target.suffix != ".md" or not target.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"path": path, "content": target.read_text(errors="replace")}

    @app.get("/api/vault/graph")
    async def vault_graph():
        """Nodes = notes, edges = wikilinks (+ family→theory synapses).
        This is the shape of Luffy's memory. Shared across clients for 30 s:
        it reads every note."""
        return _json(*await _shared("vault_graph", 30.0, _vault_graph))

    def _vault_graph():
        import re
        from ..knowledge.vault import (VAULT, THEORY_NOTES,
                                       FAMILY_THEORY)
        FOLDER_COLOR = {
            "00 Company": "#e08cf0", "10 Theories": "#3498db",
            "20 Strategies": "#2ecc71", "30 Postmortems": "#e74c3c",
            "40 Regimes": "#f1c40f", "50 Daily": "#7a8593", "": "#9b59b6",
        }
        files = {}
        for p in sorted(VAULT.rglob("*.md")):
            rel = str(p.relative_to(VAULT))
            folder = str(p.parent.relative_to(VAULT)) \
                if p.parent != VAULT else ""
            files[rel] = {"stem": p.stem, "folder": folder,
                          "text": p.read_text(errors="replace")}

        stems = {v["stem"]: rel for rel, v in files.items()}

        def _norm(s: str) -> str:
            return re.sub(r"[\s_-]+", " ", s).strip().casefold()

        loose = {_norm(s): rel for s, rel in stems.items()}

        def resolve(link: str) -> str | None:
            link = link.split("|")[0].split("#")[0].strip()
            if link in stems:
                return stems[link]
            # Strategy notes are filed slugged ("VWAP_Extreme_Fade") but the
            # theory notes and LLM-written autopsies link them by prose name
            # ("VWAP Extreme Fade"). Match on a normalised stem so those
            # theory→strategy edges don't silently vanish.
            return loose.get(_norm(link))

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
        gauntlet verdicts, population snapshot, harness state. The book is
        the first PIPELINE_BOOK_ROWS; /api/pipeline/book pages the rest."""
        return _json(*await _shared("pipeline", 5.0, _pipeline))

    @app.get("/api/pipeline/book")
    def pipeline_book(offset: int = 0, limit: int = PIPELINE_BOOK_ROWS,
                      version: str = ""):
        """The population book past the pipeline snapshot's cap, paged.

        Pages are bound to a book version (hash of the ordered book, read in
        the same statement as the page). If the book changed since `version`
        the request is refused with 409, so concatenated pages can never be
        presented as one complete population."""
        offset, limit = max(0, int(offset)), max(1, min(int(limit), PIPELINE_BOOK_ROWS))
        book, current = _book()
        if version and version != current:
            return _nocache(JSONResponse({"status": "changed", "version": current,
                                          "total": len(book)}, status_code=409))
        return _nocache(JSONResponse({
            "offset": offset, "total": len(book), "version": current,
            "more": offset + limit < len(book), "rows": book[offset:offset + limit]}))

    def _book():
        """(ordered book, version) from ONE statement: a consistent snapshot."""
        rows = [{"id": r["id"], "name": r["name"], "kind": r["kind"],
                 "state": r["state"], "origin": r["origin"],
                 "retire_reason": (r.get("retire_reason") or "")[:110]}
                for r in journal.query(
                    "SELECT id, name, kind, state, origin, retire_reason "
                    "FROM strategies ORDER BY CASE state "
                    "WHEN 'active' THEN 0 WHEN 'paper' THEN 1 "
                    "WHEN 'demoted' THEN 2 ELSE 3 END, created_at DESC, id")]
        version = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]
        return rows, version

    def _pipeline():
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
            "WHERE kind IN ('proposal_accepted','proposal_rejected') "
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
        # population book for the discovery table (active on top): the first
        # PIPELINE_BOOK_ROWS of one consistent read, with its version; the
        # rest is paged from /api/pipeline/book bound to that version
        full_book, book_version = _book()
        book = full_book
        book_truncated = len(book) > PIPELINE_BOOK_ROWS
        book = book[:PIPELINE_BOOK_ROWS]
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
                           "book": book, "book_truncated": book_truncated,
                           "book_total": len(full_book),
                           "book_version": book_version,
                           "book_rest": ("/api/pipeline/book?offset=%d&version=%s"
                                         % (len(book), book_version))
                           if book_truncated else None},
            "tv_health": {**health,
                          "budget_enabled": bool(
                              cfg.get("tv_harness", {})
                              .get("budget_enabled", True))},
        }

    @app.get("/api/org")
    async def org():
        """Company cockpit + Agents command deck: roster + live status.

        build_company runs a ~30-day accuracy scan over the votes table, so it
        is briefly cached: both the Company tab and the Agents deck poll this
        every 8s, and a shared TTL keeps those polls — including concurrent
        ones from several clients — from stacking the query."""
        return _json(*await _shared("org", 12.0,
                                    lambda: build_company(journal, cfg)))

    @app.get("/api/doctrine")
    def doctrine():
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
            return ChatEngine(journal, cfg).handle(msg, history, do_ops=False)
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
            from ..brain.postmortem import run_postmortem
            from ..knowledge.vault import Vault
            loop = asyncio.get_event_loop()
            rep = await loop.run_in_executor(
                None, lambda: run_postmortem(journal, Vault(journal)))
            return rep
        except Exception as e:
            return JSONResponse({"ran": False, "reason": str(e)},
                                status_code=500)
        finally:
            _autopsy_lock["busy"] = False

    @app.get("/api/brain/last_autopsy")
    def last_autopsy():
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
    def review_status():
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
    def logs(lines: int = 60):
        # bounded tail read: never loads the whole log file
        return {"tail": tail_lines(ROOT / "logs" / "luffy.log", lines)}

    return app


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

    # ── shared portfolio math (equity / exposure / heat), computed once ──
    eqr = journal.query("SELECT equity FROM equity ORDER BY ts DESC LIMIT 1")
    equity_now = float(eqr[0]["equity"]) if eqr else 0.0
    heat_cap_pct = float(cfg.get("risk", {}).get("portfolio_heat_cap_pct", 15.0))

    def _pos_risk(t):
        # mirrors RiskManager._position_risk: distance-to-stop × notional
        notional = float(t.get("notional_usdt") or t.get("notional") or 0)
        entry = float(t.get("entry_price") or 0)
        stop = float(t.get("stop_loss") or 0)
        if entry > 0 and stop > 0:
            return notional * abs(entry - stop) / entry
        return notional * 0.05  # unprotected fallback, same as risk.py

    open_risk = sum(_pos_risk(t) for t in opens)
    notional_sum = sum(float(t.get("notional_usdt") or t.get("notional") or 0)
                       for t in opens)
    exposure = (notional_sum / equity_now) if equity_now else None
    heat_pct = (open_risk / equity_now * 100) if equity_now else None

    vote24 = {r.get("agent"): int(r.get("n") or 0) for r in journal.query(
        "SELECT agent, COUNT(*) n FROM votes "
        "WHERE ts >= datetime('now','-24 hours') GROUP BY agent")}
    _as = journal.query(
        "SELECT COUNT(*) n FROM strategies WHERE state IN ('ACTIVE','active')")
    active_strats = int(_as[0]["n"]) if _as else 0

    def _belief_count():
        try:
            from ..core.config import ROOT
            p = ROOT / "data" / "doctrine.json"
            if p.exists():
                return len(json.loads(p.read_text()).get("beliefs", []))
        except Exception:
            pass
        return 0
    belief_count = _belief_count()

    def _age_str(ts):
        m = _age_min(ts)
        if m is None:
            return "—"
        if m < 60:
            return f"{m:.0f}m"
        if m < 1440:
            return f"{m/60:.0f}h"
        return f"{m/1440:.0f}d"

    def _spark(table, tscol="ts", extra="", params=()):
        """24-bucket hourly event histogram (oldest→newest) for a sparkline."""
        rows = journal.query(
            f"SELECT CAST((julianday('now')-julianday({tscol}))*24 AS INT) h, "
            f"COUNT(*) n FROM {table} "
            f"WHERE {tscol} >= datetime('now','-24 hours') {extra} GROUP BY h",
            params)
        buckets = [0] * 24
        for r in rows:
            h = r.get("h")
            if h is not None and 0 <= int(h) < 24:
                buckets[23 - int(h)] = int(r.get("n") or 0)
        return buckets

    def _ev_count(kinds):
        ph = ",".join("?" * len(kinds)) or "''"
        r = journal.query(
            f"SELECT COUNT(*) n FROM brain_events WHERE kind IN ({ph}) "
            f"AND ts >= datetime('now','-24 hours')", tuple(kinds))
        return int(r[0]["n"]) if r else 0

    def _queued_total():
        """Sum queued_strategy + queued_research from harvest_cycle events 24h."""
        total = 0
        rows = journal.query(
            "SELECT detail FROM brain_events WHERE kind='harvest_cycle' "
            "AND ts >= datetime('now','-24 hours')")
        for r in rows:
            try:
                d = json.loads(r["detail"])
                total += d.get("queued_strategy", 0)
                total += d.get("queued_research", 0)
            except Exception:
                pass
        return total

    def analyst(e):
        a = acc.get(e.agent_key, {})
        lv = last_votes.get(e.agent_key, {})
        n = int(a.get("n") or 0)
        av = a.get("accuracy")
        has = av is not None and n
        acc_pct = f"{float(av)*100:.0f}%" if has else "—"
        metric = f"{acc_pct} acc ({n})" if has else "no data yet"
        out = (f"{lv.get('side','')} conv {float(lv.get('conviction',0)):+.2f}"
               if lv else "no recent vote")
        ts = lv.get("ts")
        return {"metric": metric, "out": out, "ts": ts,
                "state": _state_from_age(_age_min(ts)), "feed": [],
                "stats": [["Accuracy", acc_pct], ["Samples", str(n)],
                          ["Votes 24h", f"{vote24.get(e.agent_key, 0):,}"]],
                "bar": int(round(float(av) * 100)) if has else 0,  # exact: accuracy%
                "signal": _spark("votes", "ts", "AND agent=?", (e.agent_key,))}

    # per-role stat triples for the event-driven brain staff
    _EVENT_STATS = {
        "Researcher": lambda: [["Ideas 24h", str(_ev_count(["harvest_cycle"]))],
                               ["Queued", str(_queued_total())],
                               ["Sources", str(_ev_count(["crawl_doc"]))]],
        "Strategist": lambda: [["Reviews", str(_ev_count(["review_complete"]))],
                               ["Active", str(active_strats)],
                               ["Promoted", str(_ev_count(["proposal_accepted"]))]],
        "Theorist": lambda: [["Post-mortems", str(_ev_count(["postmortem"]))],
                             ["Beliefs", str(belief_count)],
                             ["Last run", "—"]],  # filled with real ts below
    }

    def events(e):
        kinds = e.events or []
        ph = ",".join("?" * len(kinds)) or "''"
        rows = journal.query(
            f"SELECT ts, kind, subject, detail FROM brain_events "
            f"WHERE kind IN ({ph}) ORDER BY id DESC LIMIT 8", tuple(kinds))
        n = _ev_count(kinds)
        latest = rows[0] if rows else {}
        out = (f"{latest['kind']}: {_short_detail(latest.get('detail','{}'))}"
               if latest else "no activity")
        feed = [{"ts": r["ts"],
                 "text": f"{r['kind']} · {_short_detail(r.get('detail','{}'))}"}
                for r in rows]
        ts = latest.get("ts")
        stats = _EVENT_STATS.get(e.name, lambda: [["Events 24h", str(n)]])()
        if e.name == "Theorist":  # complete the "Last run" cell with the real age
            stats = [stats[0], stats[1], ["Last run", _age_str(ts)]]
        return {"metric": f"{n}/24h", "out": out, "ts": ts,
                "state": _state_from_age(_age_min(ts)), "feed": feed,
                "stats": stats,
                "bar": min(100, n * 6),  # rough 24h-activity gauge, not a KPI
                "signal": _spark("brain_events", "ts",
                                 f"AND kind IN ({ph})", tuple(kinds))}

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
        pnl = journal.query("SELECT COALESCE(SUM(realized_pnl),0) p FROM trades "
                            "WHERE closed_at >= date('now')")
        day = float(pnl[0].get("p") or 0) if pnl else 0.0
        return {"metric": f"{len(opens)} open", "out": out,
                "ts": l0.get("ts"), "state": state, "feed": feed,
                "stats": [["Open", str(len(opens))],
                          ["Exposure",
                           f"{exposure:.1f}x" if exposure is not None else "—"],
                          ["Day P&L", f"{day:+.2f}"]],
                "bar": min(100, int((exposure or 0) / 2.5 * 100)),  # exposure gauge
                "signal": _spark("trades", "opened_at")}

    def risk(e):
        ng_raw = str(journal.kv_get("news_guard_state", "") or "")
        guard, ng_active = "clear", False
        if ng_raw:
            try:  # news_guard_state is a JSON blob {active, why, ts}
                g = json.loads(ng_raw)
                ng_active = bool(g.get("active"))
                guard = (g.get("why") or "armed") if ng_active else "clear"
            except Exception:
                guard, ng_active = ng_raw, "arm" in ng_raw.lower()
        armed = control in ("HALTED", "FROZEN", "RECOVERY") or ng_active
        metric = f"{len(opens)} pos"
        out = ("guard armed / frozen" if armed else
               (f"exposure {exposure:.1f}x equity" if exposure is not None
                else f"{len(opens)} positions · limits nominal"))
        state = "alert" if armed else ("active" if opens else "idle")
        heat_str = f"{heat_pct:.1f}%" if heat_pct is not None else "—"
        bar = (min(100, int(heat_pct / heat_cap_pct * 100))
               if heat_pct is not None and heat_cap_pct else 0)  # exact: heat vs cap
        return {"metric": metric, "out": out, "ts": None, "state": state,
                "feed": [],
                "stats": [["Heat", heat_str],
                          ["Breaker", "armed" if armed else "nominal"],
                          ["Guard", guard]],
                "bar": bar,
                "signal": _spark("control_events", "ts")}

    def manager(e):
        cyc = journal.query("SELECT ts FROM cycles ORDER BY id DESC LIMIT 1")
        ts = cyc[0]["ts"] if cyc else None
        metric = f"eq ${equity_now:,.0f}" if equity_now else control
        out = f"{control} · last cycle" + (f" {_age_min(ts):.0f}m ago"
                                           if _age_min(ts) is not None else "")
        state = ("alert" if control in ("HALTED", "FROZEN", "RECOVERY")
                 else _state_from_age(_age_min(ts)))
        heat_str = f"{heat_pct:.1f}%" if heat_pct is not None else "—"
        from ..engine.rent_keeper import snapshot as _rent_snapshot
        _rent = _rent_snapshot(journal)
        rs = _rent["state"]
        rent_str = ("—" if not rs else
                    "unreadable" if rs.get("net") is None else
                    f"{rs['net']:+.0f} / {float(rs.get('bar', 50)):.0f}, "
                    f"{float(rs.get('days_left') or 0):.1f}d left")
        # last verdicts, oldest first: P pass, F fail, ? unknown
        weeks_str = "".join({"PASS": "P", "FAIL": "F"}.get(h["verdict"], "?")
                            for h in reversed(_rent["history"])) or "—"
        return {"metric": metric, "out": out, "ts": ts, "state": state,
                "feed": [], "stats": [], "bar": 0,
                "signal": _spark("cycles", "ts"),
                "core": [["Equity", f"${equity_now:,.0f}" if equity_now else "—"],
                         ["Control", control],
                         ["Cycle", _age_str(ts)],
                         ["Heat", heat_str],
                         ["Rent", rent_str],
                         ["Weeks", weeks_str]]}

    def librarian(e):
        import re
        from ..knowledge.vault import VAULT
        from datetime import datetime, timezone
        md = list(VAULT.rglob("*.md"))
        newest = max(md, key=lambda p: p.stat().st_mtime, default=None)
        ts = (datetime.fromtimestamp(newest.stat().st_mtime, timezone.utc)
              .isoformat() if newest else None)
        out = f"latest: {newest.stem}" if newest else "empty vault"
        links = 0
        for p in md:  # small vault; a full scan per poll is cheap enough
            try:
                links += len(re.findall(r"\[\[", p.read_text(errors="replace")))
            except Exception:
                pass
        return {"metric": f"{len(md)} notes", "out": out, "ts": ts,
                "state": _state_from_age(_age_min(ts)), "feed": [],
                "stats": [["Notes", str(len(md))], ["Links", str(links)],
                          ["Updated", _age_str(ts)]],
                "bar": min(100, len(md)),  # rough vault-size gauge
                "signal": [0] * 24}  # no per-hour vault event stream

    handlers = {"analyst": analyst, "events": events, "trader": trader,
                "risk": risk, "manager": manager, "librarian": librarian}
    _blank = {"metric": "—", "out": "", "ts": None, "state": "idle",
              "feed": [], "stats": [], "bar": 0, "signal": [0] * 24}

    entries = []
    for e in org.all():
        fn = handlers.get(e.status_source)
        r = fn(e) if fn else dict(_blank)
        entry = {
            "name": e.name, "title": e.title, "reports_to": e.reports_to,
            "desc": e.desc, "wraps": e.wraps,
            "node": f"00 Company/{e.name}.md",
            "category": e.category
            or ("ANALYST" if e.status_source == "analyst" else ""),
            "status": r["state"], "metric": r["metric"],
            "last_output": r["out"], "last_activity": r["ts"],
            "feed": r["feed"], "stats": r.get("stats", []),
            "bar": r.get("bar", 0), "signal": r.get("signal", [0] * 24),
        }
        if r.get("core"):
            entry["core"] = r["core"]
        entries.append(entry)
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
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
