"""Dedicated read-only venue access for the protection snapshot.

The protection snapshot must not share the kernel's synchronous ccxt client
(the execution, Supervisor and recovery threads use it; ccxt's sync client is
not documented thread-safe) and must have no path to order authority. This
module provides both:

  * a **dedicated** ``binanceusdm`` instance, built once at kernel setup and
    used only by the snapshot worker thread. It gets a copy of the kernel's
    already-loaded markets (no extra exchangeInfo request) and must address
    the same venue (demo or not) as the kernel, or construction fails.
  * a **read-only API key** (``BINANCE_READ_API_KEY``/``BINANCE_READ_SECRET_KEY``,
    created with "Enable Reading" only) — required. This is the authority
    boundary, and the venue enforces it. The trading key is never loaded
    here; without a distinct read key the reader is not built and the
    snapshot publishes UNREADABLE ``venue_reader_unavailable:…``.
  * a **GET-only transport** as defence in depth only: ccxt's ordinary paths
    (``create_order``, ``cancel_order``, algo deletes …) are refused before
    sending. It is *not* a boundary: inherited ``requests``/``urllib3``
    methods sit beneath any Python-level guard.
  * **narrow read functions** (``VenueReads``): the snapshot sees only
    ``positions()``, ``algo_orders()``, ``open_orders(symbol)`` and a pure
    ``TickPrecision`` table, each returning plain data — no ccxt object, no
    bound ccxt method, no ``_ex`` attribute.
  * an **aggregate rate guard**. Binance reports the IP's used request weight
    for the current minute in ``X-MBX-USED-WEIGHT-1M`` — kernel, Supervisor,
    dashboard and snapshot traffic together. The guard refuses a snapshot
    request while the last observed aggregate weight is at or above
    ``max_used_weight`` (default 1200, half of the 2400/min USD-M limit), so
    the snapshot yields to trading traffic instead of competing with it.

Keys never leave this module and are never logged. Whether the configured key
really lacks trading permission cannot be proven offline; it is a pre-deploy
gate item.
"""
from __future__ import annotations

import copy
import threading
import time
from collections import deque
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from .protective import venue_key

# Every request the snapshot may make. Anything else is refused pre-send.
ALLOWED_PATHS = frozenset({
    "/fapi/v3/positionRisk",       # fetch_positions (ccxt default: positionRisk v3)
    "/fapi/v2/positionRisk",       # the same read if ccxt is told useV2
    "/fapi/v1/openAlgoOrders",     # global conditional (protective stop) listing
    "/fapi/v1/openOrders",         # ordinary orders, per symbol (cleanliness)
    "/fapi/v1/leverageBracket",    # ccxt loads this once before positionRisk
})
USED_WEIGHT_HEADER = "X-MBX-USED-WEIGHT-1M"
BINANCE_WEIGHT_LIMIT_1M = 2400


class ReadOnlyViolation(PermissionError):
    """A non-GET or non-allow-listed request was refused before sending."""


class VenueRateBudget(RuntimeError):
    """The IP's aggregate request weight is too high for an optional read."""


class RateGuard:
    """Aggregate request-weight guard fed by the venue's own weight header."""

    def __init__(self, max_used_weight: int = 1200, clock=time.monotonic):
        self.max_used_weight = int(max_used_weight)
        self.clock = clock
        self._lock = threading.Lock()
        self.last_used_weight: int | None = None
        self._observed_at: float | None = None
        self.sent = 0
        self.refused = 0

    def admit(self) -> None:
        with self._lock:
            fresh = (self._observed_at is not None
                     and self.clock() - self._observed_at < 60.0)
            if fresh and self.last_used_weight is not None \
                    and self.last_used_weight >= self.max_used_weight:
                self.refused += 1
                raise VenueRateBudget(
                    f"aggregate_used_weight_{self.last_used_weight}_of_{BINANCE_WEIGHT_LIMIT_1M}")
            self.sent += 1

    def observe(self, headers) -> None:
        try:
            value = int((headers or {}).get(USED_WEIGHT_HEADER))
        except (TypeError, ValueError):
            return
        with self._lock:
            self.last_used_weight = value
            self._observed_at = self.clock()


class _Guard:
    """The one decision: GET, allow-listed host and path, weight budget."""

    def __init__(self, host: str, rate: RateGuard):
        self.host = host
        self.rate = rate
        self.refusals: deque[tuple[str, str]] = deque(maxlen=64)

    def check(self, method: str, url: str) -> None:
        parts = urlsplit(url or "")
        if (str(method).upper() != "GET" or parts.hostname != self.host
                or parts.path not in ALLOWED_PATHS):
            self.refusals.append((str(method).upper(), parts.path))
            raise ReadOnlyViolation(f"read-only venue refuses {str(method).upper()} {parts.path}")
        self.rate.admit()


class GetOnlyAdapter(HTTPAdapter):
    """Transport adapter that refuses before sending. ``inner`` is for tests."""

    def __init__(self, guard: _Guard, inner=None):
        super().__init__()
        self._guard = guard
        self._inner = inner

    def send(self, request, *args, **kwargs):
        self._guard.check(request.method, request.url)
        response = (self._inner(request) if self._inner is not None
                    else super().send(request, *args, **kwargs))
        self._guard.rate.observe(getattr(response, "headers", None))
        return response


class GetOnlySession(requests.Session):
    """Session whose every send path is guarded (session and adapters)."""

    def __init__(self, guard: _Guard, inner=None):
        super().__init__()
        self._guard = guard
        adapter = GetOnlyAdapter(guard, inner)
        self.adapters.clear()
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def send(self, request, **kwargs):
        self._guard.check(request.method, request.url)
        return super().send(request, **kwargs)


class TickPrecision:
    """Authoritative price tick per market, from exchangeInfo PRICE_FILTER.

    Pure data. ``tick()`` is None when the venue's metadata is unavailable —
    precision is then UNKNOWN, never inferred from a fixed decimal count.
    ``price_to_precision`` mirrors ccxt's (ROUND to tick) and raises when the
    tick is unknown, so ``protective.protection_match`` cannot fall back to
    six decimals.
    """

    __slots__ = ("_ticks",)

    def __init__(self, ticks: dict[str, str]):
        object.__setattr__(self, "_ticks", dict(ticks))

    def __setattr__(self, name, value):
        raise AttributeError("TickPrecision is immutable")

    def tick(self, symbol: str) -> str | None:
        return self._ticks.get(venue_key(str(symbol or "")))

    def price_to_precision(self, symbol: str, price) -> str:
        from ccxt.base.decimal_to_precision import (NO_PADDING, ROUND, TICK_SIZE,
                                                    decimal_to_precision)
        tick = self.tick(symbol)
        if tick is None:
            raise LookupError("price_precision_unknown")
        return decimal_to_precision(price, ROUND, tick, TICK_SIZE, NO_PADDING)

    def on_tick(self, symbol: str, price) -> bool | None:
        """True/False when the tick is known; None when it is not."""
        tick = self.tick(symbol)
        if tick is None or price is None:
            return None
        try:
            t, p = Decimal(tick), Decimal(str(price))
            return t > 0 and p > 0 and p % t == 0
        except (InvalidOperation, ValueError):
            return False

    @classmethod
    def from_markets(cls, markets: dict | None) -> "TickPrecision":
        ticks: dict[str, str] = {}
        for m in (markets or {}).values():
            try:
                filters = (m.get("info") or {}).get("filters") or []
                tick = next((f.get("tickSize") for f in filters
                             if f.get("filterType") == "PRICE_FILTER"), None)
                if tick is not None and Decimal(str(tick)) > 0:
                    ticks[venue_key(m.get("id") or m.get("symbol") or "")] = \
                        format(Decimal(str(tick)).normalize(), "f")
            except (AttributeError, InvalidOperation, TypeError, ValueError):
                continue
        return cls(ticks)


def _plain_position(p: dict) -> dict:
    info = p.get("info") or {}
    update = info.get("updateTime", p.get("timestamp"))
    # passed through as the venue sent it (str/int only); the evaluator decides
    # validity — nothing here turns a malformed value into evidence
    return {"symbol": p.get("symbol"), "side": p.get("side"),
            "contracts": p.get("contracts"), "entryPrice": p.get("entryPrice"),
            "updateTime": update if isinstance(update, (str, int))
            and not isinstance(update, bool) else None}


def _plain_order(o: dict) -> dict:
    info = o.get("info") or {}
    return {"id": o.get("id"), "symbol": o.get("symbol"), "side": o.get("side"),
            "type": o.get("type"), "amount": o.get("amount"), "price": o.get("price"),
            "stopPrice": o.get("stopPrice"), "reduceOnly": o.get("reduceOnly"),
            "info": {k: info.get(k) for k in ("symbol", "origType", "type", "stopPrice",
                                                "reduceOnly", "closePosition", "positionSide")
                     if k in info}}


class VenueReads:
    """The snapshot's only venue capability: three reads and a tick table.

    Results are plain dicts/lists copied out of ccxt's structures. There is no
    exchange attribute to reach; the dedicated client is held privately and
    its transport refuses everything but the allow-listed GETs.
    """

    __slots__ = ("_client", "_guard", "precision", "credential_scope")

    def __init__(self, client, guard: _Guard, precision: TickPrecision,
                 credential_scope: str):
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_guard", guard)
        object.__setattr__(self, "precision", precision)
        object.__setattr__(self, "credential_scope", credential_scope)

    def __setattr__(self, name, value):
        raise AttributeError("VenueReads is immutable")

    def positions(self) -> list:
        rows = self._client.fetch_positions()
        if not isinstance(rows, list):
            raise ValueError("invalid position snapshot")
        return [_plain_position(p) for p in rows]

    def algo_orders(self):
        rows = self._client.fapiPrivateGetOpenAlgoOrders()
        return copy.deepcopy(rows)

    def open_orders(self, symbol: str) -> list:
        rows = self._client.fetch_open_orders(symbol)
        if not isinstance(rows, list):
            raise ValueError("invalid ordinary order snapshot")
        return [_plain_order(o) for o in rows]

    def rate_state(self) -> dict:
        rate = self._guard.rate
        return {"used_weight_1m": rate.last_used_weight,
                "max_used_weight": rate.max_used_weight,
                "refused": rate.refused, "refusals": len(self._guard.refusals)}


class ReadCredentialsMissing(RuntimeError):
    """No dedicated read credentials configured."""


class ReadCredentialsSeparation(RuntimeError):
    """Dedicated read credentials overlap trading credentials."""


def _read_credentials(env) -> tuple[str, str, str]:
    """A read-only key, or nothing. The trading key is never used here.

    No in-process guard can make a trading credential read-only: Python code
    that can reach a signing client can always reach a transport beneath any
    guard (``super(GetOnlyAdapter, a).send`` did). The boundary is therefore
    the venue's own key permission ("Enable Reading" only), not this module.
    """
    key, secret = env.get("BINANCE_READ_API_KEY"), env.get("BINANCE_READ_SECRET_KEY")
    if not (key and secret):
        raise ReadCredentialsMissing("read_only_api_key_not_configured")
    trading_key, trading_secret = env.binance_keys()
    if key == trading_key or secret == trading_secret:
        raise ReadCredentialsSeparation("read_only_key_equals_trading_key")
    return key, secret, "read_only_key"


def guarded_client(*, api_key: str, secret: str, demo: bool, markets: dict,
                   timeout_ms: int = 8000, max_used_weight: int = 1200, inner=None,
                   client_class=None):
    """A dedicated binanceusdm client behind the GET-only transport."""
    import ccxt
    klass = client_class or ccxt.binanceusdm
    client = klass({"apiKey": api_key, "secret": secret, "enableRateLimit": True,
                    "timeout": int(timeout_ms),
                    "options": {"defaultType": "future",
                                "fetchPositions": {"method": "positionRisk"},
                                "warnOnFetchOpenOrdersWithoutSymbol": True}})
    if demo:
        if hasattr(client, "enable_demo_trading"):
            client.enable_demo_trading(True)
        else:
            client.set_sandbox_mode(True)
    host = urlsplit(client.urls["api"]["fapiPrivate"]).hostname
    guard = _Guard(host, RateGuard(max_used_weight))
    client.session = GetOnlySession(guard, inner)
    client.set_markets(copy.deepcopy(markets))
    return client, guard


def make_venue_reads(kernel_exchange, *, timeout_ms: int = 8000,
                     max_used_weight: int = 1200, env=None) -> VenueReads:
    """Build the snapshot's reads at kernel setup (main thread, once).

    Raises if the kernel's markets cannot be established or the dedicated
    client would address a different venue than the kernel trades on.
    """
    from ..core.config import Env
    env = env or Env
    if not getattr(kernel_exchange, "markets", None):
        kernel_exchange.load_markets()          # boot thread; cached thereafter
    markets = kernel_exchange.markets
    if not markets:
        raise RuntimeError("venue_markets_unavailable")
    key, secret, scope = _read_credentials(env)
    demo = env.get("BINANCE_DEMO", "true").lower() in ("1", "true", "yes")
    client, guard = guarded_client(api_key=key, secret=secret, demo=demo, markets=markets,
                                   timeout_ms=timeout_ms, max_used_weight=max_used_weight)
    kernel_url = ((getattr(kernel_exchange, "urls", None) or {}).get("api") or {}).get("fapiPrivate")
    if kernel_url != client.urls["api"]["fapiPrivate"]:
        raise RuntimeError("snapshot_venue_differs_from_kernel_venue")
    return VenueReads(client, guard, TickPrecision.from_markets(markets), scope)
