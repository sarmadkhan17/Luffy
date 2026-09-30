"""LUFFY-PROTECTION-SNAPSHOT-R2: the snapshot's dedicated, GET-only venue client.

A real ccxt ``binanceusdm`` instance is driven end to end through the guarded
session/adapter with a fake inner transport, so no request ever leaves the
process. Proves: the allow-list covers the reads ccxt actually issues; every
mutation path (instance methods, aliased reads, base-class fetch, the session,
the adapter) is refused before sending; the aggregate weight guard yields.
"""
import json

import ccxt
import pytest
import requests

from trader.engine import venue_reads as vr

MARKETS = {
    "BTC/USDT:USDT": {
        "id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT",
        "settle": "USDT", "baseId": "BTC", "quoteId": "USDT", "settleId": "USDT",
        "type": "swap", "spot": False, "margin": False, "swap": True, "future": False,
        "option": False, "contract": True, "linear": True, "inverse": False,
        "contractSize": 1.0, "active": True,
        "precision": {"amount": 0.001, "price": 0.1},
        "limits": {"amount": {"min": 0.001, "max": None}, "price": {"min": 0.1, "max": None},
                   "cost": {"min": 5, "max": None}, "leverage": {"min": 1, "max": 125}},
        "info": {"symbol": "BTCUSDT", "orderTypes": [
            "LIMIT", "MARKET", "STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET",
            "TRAILING_STOP_MARKET"], "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"}]},
    },
}
MUTATIONS = [
    ("create_order", ("BTC/USDT:USDT", "market", "sell", 1.0)),
    ("create_order", ("BTC/USDT:USDT", "market", "sell", 1.0, None,
                      {"stopLossPrice": 95.0, "reduceOnly": True})),
    ("cancel_order", ("1", "BTC/USDT:USDT")),
    ("cancel_all_orders", ("BTC/USDT:USDT",)),
    ("edit_order", ("1", "BTC/USDT:USDT", "limit", "sell", 1.0, 100.0)),
    ("set_leverage", (5, "BTC/USDT:USDT")),
    ("fapiPrivateDeleteAlgoOrder", ({"algoId": 1},)),
    ("fapiPrivatePostOrder", ({"symbol": "BTCUSDT", "side": "SELL", "type": "MARKET",
                               "quantity": "1"},)),
    ("fapiPrivateDeleteAllOpenOrders", ({"symbol": "BTCUSDT"},)),
]


class Transport:
    """Fake HTTP transport: canned Binance bodies; records every request sent."""

    def __init__(self, weight="12"):
        self.sent = []
        self.weight = weight
        self.positions = [{"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "1.000",
                           "entryPrice": "100.0", "markPrice": "100.0", "unRealizedProfit": "0",
                           "liquidationPrice": "0", "leverage": "5", "notional": "100",
                           "isolatedMargin": "0", "marginType": "cross", "updateTime": 1790000000000}]
        self.algo = [{"algoId": 7, "symbol": "BTCUSDT", "side": "SELL", "reduceOnly": True,
                      "orderType": "STOP_MARKET", "quantity": "1", "triggerPrice": "95.0"}]
        self.orders = [{"orderId": 3, "symbol": "BTCUSDT", "status": "NEW", "clientOrderId": "tp",
                        "price": "110", "avgPrice": "0", "origQty": "1", "executedQty": "0",
                        "cumQuote": "0", "timeInForce": "GTC", "type": "LIMIT",
                        "reduceOnly": True, "closePosition": False, "side": "SELL",
                        "positionSide": "BOTH", "stopPrice": "0", "workingType": "CONTRACT_PRICE",
                        "priceProtect": False, "origType": "LIMIT", "time": 1, "updateTime": 1}]

    def __call__(self, request):
        path = requests.utils.urlparse(request.url).path
        self.sent.append((request.method, path))
        body = {"/fapi/v3/positionRisk": self.positions,
                "/fapi/v2/positionRisk": self.positions,
                "/fapi/v1/openAlgoOrders": self.algo,
                "/fapi/v1/openOrders": self.orders,
                "/fapi/v1/leverageBracket": [{"symbol": "BTCUSDT", "brackets": [
                    {"bracket": 1, "initialLeverage": 125, "notionalCap": 50000,
                     "notionalFloor": 0, "maintMarginRatio": 0.004, "cum": 0}]}]}[path]
        r = requests.Response()
        r.status_code = 200
        r._content = json.dumps(body).encode()
        r.headers["Content-Type"] = "application/json"
        r.headers[vr.USED_WEIGHT_HEADER] = self.weight
        r.url = request.url
        return r


class Kernel:
    """The kernel's exchange as make_venue_reads sees it (markets + venue URL)."""

    def __init__(self, demo=True):
        ex = ccxt.binanceusdm()
        if demo:
            ex.enable_demo_trading(True)
        self.urls = ex.urls
        self.markets = MARKETS
        self.mutations = []

    def create_order(self, *a, **k):          # pragma: no cover - must never be reached
        self.mutations.append(a)


class Env:
    def __init__(self, read_key=False, demo="true"):
        self.values = {"BINANCE_DEMO": demo}
        if read_key:
            self.values.update(BINANCE_READ_API_KEY="rk", BINANCE_READ_SECRET_KEY="rs")

    def get(self, key, default=""):
        return self.values.get(key, default)

    def binance_keys(self):
        return "tk", "ts"


def _reads(transport=None, max_used_weight=1200, read_key=False):
    transport = transport or Transport()
    client, guard = vr.guarded_client(api_key="k", secret="s", demo=True, markets=MARKETS,
                                      max_used_weight=max_used_weight, inner=transport)
    return vr.VenueReads(client, guard, vr.TickPrecision.from_markets(MARKETS),
                         "test"), transport, client, guard


def test_reads_go_through_the_allow_list_and_return_plain_data():
    reads, t, _, _ = _reads()
    pos = reads.positions()
    assert pos == [{"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 1.0,
                    "entryPrice": 100.0, "updateTime": 1790000000000}]
    assert reads.algo_orders()[0]["orderType"] == "STOP_MARKET"
    oo = reads.open_orders("BTC/USDT:USDT")
    assert oo[0]["reduceOnly"] is True and oo[0]["info"]["origType"] == "LIMIT"
    assert all(m == "GET" and p in vr.ALLOWED_PATHS for m, p in t.sent)
    assert {p for _, p in t.sent} >= {"/fapi/v3/positionRisk", "/fapi/v1/openAlgoOrders",
                                      "/fapi/v1/openOrders"}
    assert reads.rate_state()["used_weight_1m"] == 12
    # plain data: nothing returned holds a ccxt object
    assert all(type(v) in (str, int, float, bool, type(None)) for v in pos[0].values())


@pytest.mark.parametrize("name,args", MUTATIONS)
def test_every_mutation_is_refused_before_sending(name, args):
    _, t, client, guard = _reads()
    before = len(t.sent)
    with pytest.raises(Exception) as err:
        getattr(client, name)(*args)
    assert isinstance(err.value, (vr.ReadOnlyViolation, ccxt.BaseError))
    assert [s for s in t.sent[before:] if s[0] != "GET"] == []
    assert all(p in vr.ALLOWED_PATHS for _, p in t.sent)
    assert guard.refusals, "the transport guard, not something else, stopped it"


def test_escape_paths_are_refused_too():
    """Base-class fetch, the session and the adapter directly — all refused."""
    _, t, client, guard = _reads()
    url = client.urls["api"]["fapiPrivate"] + "/order"
    with pytest.raises(vr.ReadOnlyViolation):
        ccxt.Exchange.fetch(client, url, "POST", {}, "symbol=BTCUSDT")
    with pytest.raises(vr.ReadOnlyViolation):
        client.session.request("DELETE", url)
    prepared = requests.Request("POST", url, data="x").prepare()
    with pytest.raises(vr.ReadOnlyViolation):
        client.session.get_adapter(url).send(prepared)
    with pytest.raises(vr.ReadOnlyViolation):          # a GET outside the allow-list
        client.session.request("GET", url)
    with pytest.raises(vr.ReadOnlyViolation):          # another host
        client.session.request("GET", "https://fapi.binance.com/fapi/v3/positionRisk")
    assert t.sent == [] and len(guard.refusals) == 5


def test_read_alias_to_order_creation_is_refused():
    """Even a read slot rebound to create_order cannot create an order."""
    reads, t, client, _ = _reads()
    client.fetch_positions = lambda *a, **k: client.create_order(
        "BTC/USDT:USDT", "market", "sell", 1.0)
    with pytest.raises(Exception):
        reads.positions()
    assert all(m == "GET" for m, _ in t.sent)


def test_aggregate_weight_guard_yields_to_other_traffic():
    """X-MBX-USED-WEIGHT-1M is the IP's total (kernel + Supervisor + snapshot)."""
    reads, t, _, guard = _reads(Transport(weight="40"), max_used_weight=1200)
    reads.positions()
    t.weight = "1300"                                  # other traffic spikes
    reads.algo_orders()                                # this response reports it
    n = len(t.sent)
    with pytest.raises(vr.VenueRateBudget, match="aggregate_used_weight_1300"):
        reads.open_orders("BTC/USDT:USDT")
    assert len(t.sent) == n and guard.rate.refused == 1


def test_tick_precision_is_authoritative_or_unknown():
    p = vr.TickPrecision.from_markets(MARKETS)
    assert p.tick("BTC/USDT") == "0.1" and p.tick("ETH/USDT") is None
    assert p.on_tick("BTC/USDT", 95.0) is True and p.on_tick("BTC/USDT", 95.05) is False
    assert p.on_tick("ETH/USDT", 95.0) is None             # unknown, never inferred
    assert p.price_to_precision("BTC/USDT", 95.04) == "95"
    with pytest.raises(LookupError):
        p.price_to_precision("ETH/USDT", 1.0)
    with pytest.raises(AttributeError):
        p._ticks = {}


def test_make_venue_reads_is_dedicated_and_same_venue():
    kernel = Kernel(demo=True)
    reads = vr.make_venue_reads(kernel, env=Env(read_key=True))
    assert reads.credential_scope == "read_only_key"
    assert reads._client is not kernel and isinstance(reads._client.session, vr.GetOnlySession)
    assert reads._client.markets is not kernel.markets          # copied, not shared
    with pytest.raises(RuntimeError, match="read_only_api_key_not_configured"):
        vr.make_venue_reads(kernel, env=Env())            # never falls back to trading key
    same = Env(read_key=True)
    same.values.update(BINANCE_READ_API_KEY="tk", BINANCE_READ_SECRET_KEY="ts")
    with pytest.raises(RuntimeError, match="read_only_key_equals_trading_key"):
        vr.make_venue_reads(kernel, env=same)
    with pytest.raises(RuntimeError, match="snapshot_venue_differs_from_kernel_venue"):
        vr.make_venue_reads(Kernel(demo=False), env=Env(read_key=True, demo="true"))
    assert kernel.mutations == []


def test_inherited_transport_bypass_carries_only_the_read_only_key():
    """Astra R2 repro, restated honestly: the GET-only guard is defence in
    depth and CAN be bypassed through inherited requests methods. What the
    bypass can sign with is the read-only key — never the trading key, which
    this client never receives. The venue refuses a mutation for that key
    (pre-deploy gate: confirm the key's permissions)."""
    from requests.adapters import HTTPAdapter
    t = Transport()
    kernel = Kernel()
    reads = vr.make_venue_reads(kernel, env=Env(read_key=True))
    client = reads._client
    adapter = client.session.get_adapter("https://demo-fapi.binance.com")
    adapter._inner = t
    signed = client.sign("order", "fapiPrivate", "POST",
                         {"symbol": "BTCUSDT", "side": "SELL", "type": "MARKET", "quantity": "1"})
    prepared = requests.Request("POST", signed["url"], headers=signed["headers"],
                                data=signed["body"]).prepare()
    seen = {}

    def record(self, request, *a, **k):
        seen.update(method=request.method, key=request.headers.get("X-MBX-APIKEY"))
        raise ConnectionError("offline")
    orig = HTTPAdapter.send
    HTTPAdapter.send = record
    try:
        with pytest.raises(ConnectionError):
            super(vr.GetOnlyAdapter, adapter).send(prepared)
    finally:
        HTTPAdapter.send = orig
    assert seen == {"method": "POST", "key": "rk"}        # the bypass exists …
    assert client.apiKey == "rk" and client.secret == "rs"  # … but only with the read key
    assert "tk" not in (client.apiKey, client.secret) and "ts" not in (client.apiKey, client.secret)
