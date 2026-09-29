"""LUFFY-PROTECTION-SNAPSHOT-R1 venue gate (evaluation only).

Uses the worktree's R4 candidate reader (make_venue_reads) with the production
.env read-only key, the production journal read-only (mode=ro), and never
builds ProtectionMonitor/SnapshotStore — nothing is persisted. Secrets are
never printed. Output: JSON at argv[1].
"""
from __future__ import annotations

import json
import re
import sys
import time
from urllib.parse import urlsplit

WT = "/home/sarmad/trader/.claude/worktrees/owner-frontend-live-binding"
PROD = "/home/sarmad/trader"
sys.path.insert(0, WT)

from dotenv import dotenv_values                                   # noqa: E402

import ccxt                                                        # noqa: E402
from trader.engine import protection_snapshot as ps                # noqa: E402
from trader.engine import venue_reads as vr                        # noqa: E402

OUT = sys.argv[1]
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
ROUND_GAP_S = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0

vals = dotenv_values(f"{PROD}/.env")


class GateEnv:
    @classmethod
    def get(cls, key, default=""):
        v = vals.get(key)
        return default if v in (None, "") else v

    @classmethod
    def binance_keys(cls):
        return (vals.get("BINANCE_API_KEY"), vals.get("BINANCE_SECRET_KEY"))


T0 = time.monotonic()
log: list[dict] = []


def recorder(tag):
    def hook(resp, *a, **k):
        req = resp.request
        h = resp.headers
        log.append({"t_s": round(time.monotonic() - T0, 3), "client": tag,
                    "method": req.method, "path": urlsplit(req.url).path,
                    "host": urlsplit(req.url).hostname, "status": resp.status_code,
                    "used_weight_1m": h.get("X-MBX-USED-WEIGHT-1M"),
                    "weight_headers": {k2: v for k2, v in h.items()
                                       if k2.upper().startswith("X-MBX-USED-WEIGHT")
                                       or k2.upper().startswith("X-MBX-ORDER-COUNT")},
                    "elapsed_ms": round(resp.elapsed.total_seconds() * 1000, 1)})
        return resp
    return hook


def clean(msg: str) -> str:
    msg = re.sub(r"signature=[0-9a-fA-F]+", "signature=<redacted>", str(msg))
    return re.sub(r"[A-Za-z0-9]{40,}", "<redacted>", msg)[:300]


result: dict = {"demo_flag_env": GateEnv.get("BINANCE_DEMO"), "ccxt": ccxt.__version__}

# ── kernel stand-in: keyless, same venue mode, markets only (the kernel's
#    already-loaded markets would be copied the same way)
demo = GateEnv.get("BINANCE_DEMO", "true").lower() in ("1", "true", "yes")
kx = ccxt.binanceusdm({"enableRateLimit": True, "options": {"defaultType": "future"}})
if demo:
    kx.enable_demo_trading(True) if hasattr(kx, "enable_demo_trading") else kx.set_sandbox_mode(True)
kx.session.hooks["response"].append(recorder("public_markets"))
kx.load_markets()
result["kernel_standin_fapiPrivate"] = kx.urls["api"]["fapiPrivate"]

# ── A. exactly the dedicated R4 reader
reads = vr.make_venue_reads(kx, env=GateEnv, timeout_ms=8000, max_used_weight=1200)
client = reads._client
client.session.hooks["response"].append(recorder("reader"))
result["reader"] = {
    "class": type(reads).__name__, "credential_scope": reads.credential_scope,
    "fapiPrivate": client.urls["api"]["fapiPrivate"],
    "session_class": type(client.session).__name__,
    "adapter_classes": sorted({type(a).__name__ for a in client.session.adapters.values()}),
    "uses_read_key": client.apiKey == vals.get("BINANCE_READ_API_KEY")
    and client.secret == vals.get("BINANCE_READ_SECRET_KEY"),
    "uses_trading_key": client.apiKey == vals.get("BINANCE_API_KEY")
    or client.secret == vals.get("BINANCE_SECRET_KEY"),
    "same_venue_as_kernel_standin": client.urls["api"]["fapiPrivate"] == kx.urls["api"]["fapiPrivate"],
    "allowlist": sorted(vr.ALLOWED_PATHS),
}
observer = ps.Observer(reads, ps.JournalReads(f"{PROD}/data/luffy.db"),
                       supervisor_busy=lambda: False)   # out-of-process: lock not observable


def snapshot(seq):
    i0 = len(log)
    t = time.monotonic()
    obs = ps.observe(observer, budget=ps.Budget(time.monotonic() + 15.0))
    snap = ps.evaluate(obs, reads.precision, boot=0, seq=seq)
    return {"snap": snap, "obs_requests": obs["requests"], "venue_error_type": obs["venue_error_type"],
            "ordinary_error": obs.get("ordinary_error"),
            "journal": obs["journal_before"], "algo_raw": obs["algo"],
            "positions_raw_plain": obs["positions_before"],
            "http": log[i0:], "duration_ms": round((time.monotonic() - t) * 1000, 1)}


# ── B. one evaluation-only snapshot (paths)
result["B"] = snapshot(1)

# ── D. repeated unchanged reads (no state forced)
rounds = []
for r in range(ROUNDS):
    if r:
        time.sleep(ROUND_GAP_S)
    row = {"t_s": round(time.monotonic() - T0, 3), "wall_ms": int(time.time() * 1000)}
    try:
        raw = client.fetch_positions()
        row["positions"] = [{
            "symbol": p.get("symbol"), "side": p.get("side"), "contracts": p.get("contracts"),
            "ccxt_timestamp": p.get("timestamp"),
            "info_time_fields": {k: v for k, v in (p.get("info") or {}).items() if "time" in k.lower()},
            "markPrice": (p.get("info") or {}).get("markPrice"),
            "unRealizedProfit": (p.get("info") or {}).get("unRealizedProfit"),
            "entryPrice": (p.get("info") or {}).get("entryPrice"),
            "plain_updateTime": vr._plain_position(p)["updateTime"],
            "info_keys": sorted((p.get("info") or {}).keys()) if r == 0 else None,
        } for p in raw if (p.get("contracts") or 0) > 0]
        algo = reads.algo_orders()
        rows = algo.get("orders", []) if isinstance(algo, dict) else (algo or [])
        row["algo"] = [{"algoId": a.get("algoId"), "symbol": a.get("symbol"),
                        "time_fields": {k: v for k, v in a.items() if "time" in k.lower()},
                        "triggerPrice": a.get("triggerPrice"), "quantity": a.get("quantity"),
                        "algoStatus": a.get("algoStatus"),
                        "keys": sorted(a.keys()) if r == 0 else None} for a in rows]
    except Exception as exc:
        row["error"] = type(exc).__name__ + ": " + clean(exc)
    rounds.append(row)
result["D"] = rounds

# ── E. first complete snapshot (not persisted)
result["E"] = snapshot(2)

# precision metadata for position symbols (venue exchangeInfo)
meta = {}
for rec in result["E"]["snap"]["symbols"]:
    m = kx.markets.get(rec["venue_symbol"]) or {}
    f = {x.get("filterType"): x for x in (m.get("info") or {}).get("filters") or []}
    meta[rec["symbol"]] = {"tickSize": (f.get("PRICE_FILTER") or {}).get("tickSize"),
                           "stepSize": (f.get("LOT_SIZE") or {}).get("stepSize"),
                           "minQty": (f.get("LOT_SIZE") or {}).get("minQty"),
                           "pricePrecision": (m.get("info") or {}).get("pricePrecision"),
                           "quantityPrecision": (m.get("info") or {}).get("quantityPrecision"),
                           "reader_tick": reads.precision.tick(rec["symbol"])}
result["precision_meta"] = meta

# ── G / B totals
result["http_all"] = log
result["refusals"] = list(reads._guard.refusals)
result["rate_state"] = reads.rate_state()
result["duration_s"] = round(time.monotonic() - T0, 2)
with open(OUT, "w") as fh:
    json.dump(result, fh, indent=1, default=str)
print("wrote", OUT, "requests", len(log), "refusals", len(result["refusals"]),
      "B", result["B"]["snap"]["status"], "E", result["E"]["snap"]["status"],
      "duration_s", result["duration_s"])
