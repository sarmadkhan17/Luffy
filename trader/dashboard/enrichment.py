"""Optional venue enrichment for the Overview: wallet and ticker prices.

Kept off the initial-data path. The same read-only capabilities the legacy
summary used (signed demo account read, public ticker via the existing
DataFeed/make_exchange) — no new credential handling, provider or service.

Concurrency contract:
- at most one job per key is ever in flight; a refresh request while one
  runs joins it rather than starting another;
- jobs run on a dedicated two-worker pool, one worker per key, so capacity
  is never released while an old job is still running and nothing queues;
- a caller that stops waiting (HTTP deadline, browser abort) does not stop
  the job and does not launch a replacement;
- the cache is written only when a job completes, with the retrieval time
  taken at completion and any source timestamp the venue returned;
- a failed job records its error but leaves the previous value and its
  timestamps untouched, so staleness stays visible.

Identity contract: one `Enrichment` per dashboard app (never process-global).
The account entry is bound to a fingerprint of the configured API key and
the ticker entry to the exchange routing (demo flag); a change of either
drops the cached value before any request, so one account's data is never
served as another's. The fingerprint is internal and never returned.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone

log = logging.getLogger("dashboard.enrichment")

UNIVERSE = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
            "ZEC/USDT", "AAVE/USDT", "SUI/USDT", "HYPE/USDT", "NEAR/USDT")
#: finite per-request timeouts (connect, read) and an overall job budget,
#: checked before each ticker call (a call in progress can overrun it by at
#: most TICKER_TIMEOUT_MS)
ACCOUNT_TIMEOUT = (3.05, 5.0)
TICKER_TIMEOUT_MS = 4000
TICKER_JOB_BUDGET_S = 12.0
MAX_AGE_S = {"account": 60.0, "tickers": 15.0}
RETRY_AFTER_FAILURE_S = 10.0
UNRESOLVED = "unresolved"


def _iso(ts: float | None) -> str | None:
    return (datetime.fromtimestamp(ts, timezone.utc).isoformat()
            if ts is not None else None)


def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


class SingleFlightCache:
    def __init__(self, executor: ThreadPoolExecutor, clock=time.time):
        self._executor = executor
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, dict] = {}
        self.submitted = 0          # observability for tests/profiles
        # version identity for clients: a new instance (restart) or a new
        # generation (identity invalidation) supersedes any retained copy;
        # within one generation a later retrieved_at supersedes an earlier one
        self.instance = secrets.token_hex(4)

    def _st(self, key: str) -> dict:
        return self._state.setdefault(key, {
            "value": None, "source_time": None, "retrieved_at": None,
            "last_attempt_at": None, "last_error": None, "future": None,
            "identity": None, "generation": 0})

    def _rebind(self, st: dict, identity) -> None:
        # caller holds the lock. A running job keeps its worker; its result
        # (success or failure) is discarded because its generation is old.
        st.update(value=None, source_time=None, retrieved_at=None,
                  last_error=None, last_attempt_at=None, identity=identity,
                  generation=st["generation"] + 1)

    def invalidate(self, key: str, identity) -> None:
        """Bind `key` to `identity`, dropping any value bound to another."""
        with self._lock:
            st = self._st(key)
            if st["identity"] != identity:
                self._rebind(st, identity)

    def identity(self, key: str):
        with self._lock:
            return self._st(key)["identity"]

    def ensure(self, key: str, loader, max_age: float, merge=None,
               identity=None) -> Future | None:
        """Start a refresh if due and none is running; return the running job.

        The identity is bound atomically with admission: a different identity
        first drops the old value and advances the generation, so a job still
        running for the previous identity can never commit (it keeps its
        worker until it really ends; no replacement is started meanwhile)."""
        with self._lock:
            st = self._st(key)
            if identity is not None and st["identity"] != identity:
                self._rebind(st, identity)
            fut = st["future"]
            if fut is not None and not fut.done():
                return fut
            now = self._clock()
            if st["retrieved_at"] is not None and now - st["retrieved_at"] < max_age:
                return None
            if (st["last_error"] is not None and st["last_attempt_at"] is not None
                    and now - st["last_attempt_at"] < RETRY_AFTER_FAILURE_S):
                return None
            st["last_attempt_at"] = now
            fut = self._executor.submit(self._run, key, loader, merge,
                                        st["generation"])
            st["future"] = fut
            self.submitted += 1
            return fut

    def _run(self, key: str, loader, merge, generation: int) -> None:
        try:
            value, source_time = loader()
            error = None
        except Exception as e:                       # never cached as data
            log.debug(f"enrichment {key} failed: {type(e).__name__}")
            error = type(e).__name__
        done = self._clock()
        with self._lock:
            st = self._st(key)
            st["future"] = None
            if st["generation"] != generation:
                return                     # admitted for a previous identity
            if error is not None:
                st["last_error"] = error
                return
            if merge is not None:
                value = merge(st["value"], value)
            st.update(value=value, source_time=source_time, retrieved_at=done,
                      last_error=None)

    def view(self, key: str, max_age: float) -> dict:
        with self._lock:
            st = dict(self._st(key))
        now = self._clock()
        age = (round(now - st["retrieved_at"], 1)
               if st["retrieved_at"] is not None else None)
        if st["value"] is None:
            freshness = "unavailable"
        else:
            freshness = "fresh" if age is not None and age <= max_age else "stale"
        failed_after = (st["last_error"] is not None and (
            st["retrieved_at"] is None
            or (st["last_attempt_at"] or 0) >= st["retrieved_at"]))
        return {"freshness": freshness, "value": st["value"],
                "source_time": st["source_time"],
                "retrieved_at": _iso(st["retrieved_at"]), "age_s": age,
                "max_age_s": max_age, "instance": self.instance,
                "generation": st["generation"],
                "refreshing": bool(st["future"] and not st["future"].done()),
                "last_attempt_at": _iso(st["last_attempt_at"]),
                "last_refresh_failed": failed_after,
                "last_error": st["last_error"] if failed_after else None}


# ── loaders (run only inside the enrichment pool) ─────────────────────────
def _account_identity():
    """(fingerprint, credentials) read once per refresh; the fingerprint is
    internal and never returned to clients."""
    from ..core.config import Env
    key, secret = Env.binance_keys()
    if not key or not secret:
        raise RuntimeError("account_credentials_missing")
    fp = hashlib.sha256(("luffy-dashboard:" + key).encode()).hexdigest()[:16]
    return fp, (key, secret)


def fetch_account(keys):
    """Cross-asset wallet view from the signed demo account endpoint.

    The response carries no read timestamp; per-asset updateTime is the time
    of the last balance change, returned as `last_balance_change_at`, not as
    a freshness time. Freshness is the retrieval time."""
    import requests
    key, secret = keys
    q = f"timestamp={int(time.time() * 1000)}&recvWindow=10000"
    sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    r = requests.get("https://demo-fapi.binance.com/fapi/v3/account",
                     params=q + f"&signature={sig}",
                     headers={"X-MBX-APIKEY": key},
                     timeout=ACCOUNT_TIMEOUT).json()
    if not isinstance(r, dict) or "assets" not in r:
        # an error body ({"code": ..., "msg": ...}) is a failure, not zeros
        raise RuntimeError("account_response_invalid")
    assets = {x["asset"]: float(x["walletBalance"]) for x in r["assets"]
              if abs(float(x.get("walletBalance") or 0)) > 1e-9}
    updated = [int(x["updateTime"]) for x in r["assets"]
               if str(x.get("updateTime") or "").isdigit()
               and int(x["updateTime"]) > 0]
    px_btc = None
    if "BTC" in assets:
        try:
            px_btc = float(requests.get(
                "https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT",
                timeout=ACCOUNT_TIMEOUT).json()["price"])
        except Exception:
            px_btc = None
    usd, unconverted = {}, {}
    for k, v in assets.items():
        if k in ("USDT", "USDC"):
            usd[k] = round(v, 2)
        elif k == "BTC" and px_btc:
            usd[k] = round(v * px_btc, 2)
        else:
            unconverted[k] = f"{v:.6f}"
    margin = r.get("totalMarginBalance")
    value = {"margin_equity": round(float(margin), 2) if margin is not None else None,
             "assets_usd": usd, "assets_unconverted": unconverted,
             # the total covers converted assets only; see assets_unconverted
             "assets_usd_total": round(sum(usd.values()), 2) if usd else None,
             "assets_usd_total_complete": not unconverted,
             "last_balance_change_at": _iso(max(updated) / 1000) if updated else None}
    return value, None


def fetch_tickers(ex, symbols):
    """Last price per symbol, each with the ticker's own timestamp and its
    retrieval time. Failed/skipped symbols are reported, not dropped."""
    start = time.monotonic()
    prices, errors, skipped = {}, {}, []
    for sym in symbols:
        if time.monotonic() - start > TICKER_JOB_BUDGET_S:
            skipped.append(sym)
            continue
        try:
            t = ex.fetch_ticker(sym)
        except Exception as e:
            errors[sym] = type(e).__name__
            continue
        px = t.get("last") or t.get("close")
        if not px:
            errors[sym] = "no_price"
            continue
        ts = t.get("timestamp")
        prices[sym] = {"price": float(px),
                       "source_time": _iso(ts / 1000) if ts else None,
                       "retrieved_at": _iso(time.time())}
    if not prices:
        raise RuntimeError("no_ticker_succeeded")
    return {"prices": prices, "errors": errors, "skipped": skipped}, None


def merge_tickers(old, new):
    """Keep a previous price for a symbol this job failed/skipped, with its
    own timestamps and the failure, instead of dropping it."""
    prices = dict(new["prices"])
    for sym, prev in ((old or {}).get("prices") or {}).items():
        if sym not in prices and (sym in new["errors"] or sym in new["skipped"]):
            prices[sym] = {**prev, "last_error": new["errors"].get(sym, "skipped")}
    return {**new, "prices": prices}


#: a ticker time this far ahead of our clock is treated as invalid
FUTURE_TOLERANCE_S = 5.0


def symbol_freshness(p: dict, now: float, max_age: float) -> dict:
    """Fresh requires BOTH a recent retrieval and a valid, recent exchange
    observation time. A missing, unparseable or future ticker time is not
    evidence of freshness (freshness "unknown")."""
    r_at, s_at = _epoch(p.get("retrieved_at")), _epoch(p.get("source_time"))
    r_age = None if r_at is None else round(now - r_at, 1)
    s_age = None if s_at is None else round(now - s_at, 1)
    if s_age is None or s_age < -FUTURE_TOLERANCE_S:
        state = "unknown"
    elif (r_age is not None and 0 <= r_age <= max_age
          and s_age <= max_age and not p.get("last_error")):
        state = "fresh"
    else:
        state = "stale"
    return {**p, "retrieved_age_s": r_age, "source_age_s": s_age, "freshness": state}


class Enrichment:
    """Per-app enrichment state: cache, pool, ticker exchange and identities."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self.cache = SingleFlightCache(ThreadPoolExecutor(
            max_workers=len(MAX_AGE_S), thread_name_prefix="dash-enrich"), clock)
        self._ex = None
        self._ex_route = None
        self._lock = threading.Lock()

    def _ticker_route(self) -> str:
        from ..core.config import Env
        return "demo" if Env.get("BINANCE_DEMO", "true").lower() in (
            "1", "true", "yes") else "production"

    def _exchange(self, route):
        with self._lock:
            if self._ex is None or self._ex_route != route:
                from ..data.feed import DataFeed, make_exchange
                # the route captured at admission decides the venue; never
                # re-read configuration here (it may have changed since)
                feed = DataFeed(make_exchange("futures", demo=(route == "demo")))
                feed.ex.timeout = TICKER_TIMEOUT_MS
                self._ex, self._ex_route = feed.ex, route
            return self._ex

    def refresh(self, open_symbols) -> list[Future]:
        """Called from a worker thread; never blocks on I/O beyond reading
        the configured identity. An unresolvable identity fails closed: the
        entry is rebound to "unresolved" (old value dropped), no job starts."""
        symbols = list(dict.fromkeys([*UNIVERSE, *open_symbols]))
        jobs = []
        try:
            fp, keys = _account_identity()
        except Exception:
            self.cache.invalidate("account", UNRESOLVED)
        else:
            jobs.append(self.cache.ensure(
                "account", lambda: fetch_account(keys), MAX_AGE_S["account"],
                identity=fp))
        try:
            route = self._ticker_route()
        except Exception:
            self.cache.invalidate("tickers", UNRESOLVED)
        else:
            jobs.append(self.cache.ensure(
                "tickers", lambda: fetch_tickers(self._exchange(route), symbols),
                MAX_AGE_S["tickers"], merge=merge_tickers, identity=route))
        return [j for j in jobs if j is not None]

    def view(self, positions: list[dict]) -> dict:
        now = self._clock()
        acct = self.cache.view("account", MAX_AGE_S["account"])
        tk = self.cache.view("tickers", MAX_AGE_S["tickers"])
        prices = {s: symbol_freshness(p, now, MAX_AGE_S["tickers"])
                  for s, p in ((tk["value"] or {}).get("prices") or {}).items()}
        if tk["value"] is not None:
            states = {p["freshness"] for p in prices.values()}
            tk["freshness"] = ("fresh" if states == {"fresh"} else
                               "partial" if "fresh" in states else
                               "unknown" if states == {"unknown"} else "stale")
            tk["value"] = {**tk["value"], "prices": prices}
        for key, sec in (("account", acct), ("tickers", tk)):
            if self.cache.identity(key) == UNRESOLVED:
                sec["reason"] = "identity_unresolved"
        return {"generated_at": _iso(now),
                "account": {"source": "venue account (signed read); "
                                      "freshness is retrieval time", **acct},
                "tickers": {"source": "venue ticker", **tk},
                "marks": {"basis": "estimate: journal entry and amount × ticker "
                                   "price; not venue-reported P&L",
                          "rows": marks(positions, prices)}}


def marks(positions: list[dict], prices: dict) -> dict:
    """Unrealized P&L ESTIMATES: journal entry/amount × ticker price."""
    out = {}
    for p in positions:
        tk = prices.get(p["symbol"])
        entry, amt = p.get("entry_price"), p.get("amount")
        if not tk or not entry or amt is None:
            continue
        px = tk["price"]
        direction = 1.0 if p["side"] == "long" else -1.0
        out[p["id"]] = {
            "mark": round(px, 6), "source_time": tk.get("source_time"),
            "retrieved_at": tk.get("retrieved_at"),
            "freshness": tk["freshness"],
            "upnl_estimate": round((px - entry) * direction * float(amt), 2),
            "upnl_pct": round((px - entry) / entry * 100 * direction, 2),
            "notional_marked": round(px * float(amt), 2)}
    return out
