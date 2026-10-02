"""Synthetic exact-grid 4h frames for the correlation-change tests (no network)."""
import random

import pandas as pd

TF = 14_400_000
NOW = 1_790_000_000_000 + 60_000          # one minute after a 4h close
ANCHOR = NOW // TF * TF                    # close of the newest closed bar
SYM = "S1/USDT"


def steps(seed, n=150, choices=(-1, 0, 1)):
    rng = random.Random(seed)
    return [rng.choice(choices) for _ in range(n)]


MARKET = steps(11)


def closes(k):
    """Closes whose successive ratios are exact powers of two, so negated
    steps give exactly negated log returns."""
    out = [1024.0]
    for s in k:
        out.append(out[-1] * 2.0 ** s)
    return out


def frame(c, end_open=None, extra=0):
    end_open = ANCHOR - TF if end_open is None else end_open
    n = len(c)
    ts = [end_open - (n - 1 - i) * TF for i in range(n)]
    return {"4h": pd.DataFrame({
        "ts": pd.to_datetime(ts, unit="ms", utc=True),
        "open": c, "high": [x * 2 for x in c], "low": [x / 2 for x in c], "close": c,
        "volume": [100.0 + (i + extra) % 7 for i in range(n)]})}


def peer_steps(j):
    noise = steps(100 + j, choices=(-1, 0, 0, 1))
    return [m + e for m, e in zip(MARKET, noise)]


def candidate_steps(sign=1):
    """Tracks the market over the 120-return baseline, independent over the
    most recent 30: a sharp decoupling. sign=-1 mirrors it exactly."""
    k = MARKET[:120] + steps(999, n=30)
    return [sign * s for s in k]


def universe(n_peers=5, candidate=None, sign=1):
    data = {f"S{j}/USDT": frame(closes(peer_steps(j)), extra=j) for j in (0, 2, 3, 4, 5, 6, 7)[:n_peers]}
    data[SYM] = frame(closes(candidate if candidate is not None else candidate_steps(sign)), extra=1)
    return data


HEALTH = {"health_schema": "attention-collector-health.v2", "instance_id": "1" * 32,
          "updated_ms": NOW, "status": "ok", "worker_alive": True, "errors": 0,
          "capture_errors": 0, "worker_errors": 0, "dropped": 0, "details_lost": 0,
          "process_started_ms": 0, "failure_generation": 0, "fence_seq": 0, "certificate": None,
          "last_error": None, "first_error_ms": None, "last_error_ms": None,
          "last_complete": {"scan_id": "r1", "seq": 1}}


def publish(tmp_path, data=None, name="attention.db", mutate=None):
    """A persisted scan (with causes and collector health) whose close history
    is the captured input only."""
    import copy
    import json
    from types import SimpleNamespace
    from unittest.mock import patch

    from trader.observability import attention as A
    from trader.observability.store import Store

    path = tmp_path / name
    data = universe() if data is None else data
    ev = A.capture(data, list(data), "r1", A.settings(), NOW)
    if mutate:
        mutate(ev)
    ident = dict(schema="attention-scan-identity.v1", instance_id="1" * 32, seq=1)
    store = Store(path, A.settings())
    with patch("trader.observability.store.time", SimpleNamespace(time=lambda: NOW / 1000)):
        store.write(dict(copy.deepcopy(ev), identity=ident))
        store.write({"kind": "causes", "scan_id": "r1", "as_of_ms": NOW, "identity": ident,
                     "items": [{"symbol": s, "decision_id": "d_" + s} for s in data]})
    store.close()
    path.with_name("attention_health.json").write_text(json.dumps(HEALTH))
    return path
