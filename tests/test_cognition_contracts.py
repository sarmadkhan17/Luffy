"""Offline cognition contracts: input validation, closed-bar and as-of rules,
stable IDs, and the package's isolation from anything with runtime authority.

`make_fixture` builds the synthetic inputs every cognition test uses. Returns
alternate +-E per bar (phase set by symbol), so no symbol diverges or trends by
construction; each scenario then injects exactly the move it is about.
"""
import ast
import json
import math
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from trader.cognition.contracts import load_input, stable_id

TF = 3_600_000
T0 = (1_788_000_000_000 // TF) * TF
E = 0.004
SYMBOLS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "trader" / "cognition"


def at(bar: int) -> int:
    """ms at which bar index `bar - 1` has closed."""
    return T0 + bar * TF


def make_fixture(n_bars=60, symbols=SYMBOLS, event=None, decisions=(45,),
                 drop=(), participation=(), e=E):
    """`event(sym, i) -> (extra_return, volume_multiplier)`; `drop` is a set of
    (sym, bar) to omit; `e` is the alternating base return."""
    candles = []
    for s_i, sym in enumerate(symbols):
        close = 100.0 + s_i
        for i in range(n_bars):
            extra, vmult = event(sym, i) if event else (0.0, 1.0)
            r = (e if (i + s_i) % 2 == 0 else -e) + extra
            o, close = close, close * math.exp(r)
            if (sym, i) in drop:
                continue
            candles.append({"symbol": sym, "open_ms": T0 + i * TF, "open": o,
                            "high": max(o, close) * 1.001, "low": min(o, close) * 0.999,
                            "close": close, "source": "synthetic",
                            "volume": (1000.0 if i % 2 else 1100.0) * vmult})
    return {"schema": "cognition.input.v1", "timeframe": "1h",
            "decision_times": [at(b) for b in decisions],
            "membership": [{"symbol": s, "from_ms": T0, "to_ms": None,
                            "available_ms": T0, "source": "synthetic"} for s in symbols],
            "candles": candles, "participation": list(participation)}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def _bar(**kw):
    base = {"symbol": "AAA", "open_ms": T0, "open": 1.0, "high": 1.1, "low": 0.9,
            "close": 1.05, "volume": 5.0}
    base.update(kw)
    return base


def _raw(candles, **kw):
    raw = {"schema": "cognition.input.v1", "timeframe": "1h", "decision_times": [at(3)],
           "membership": [], "candles": candles}
    raw.update(kw)
    return raw


def test_invalid_records_are_rejected_with_reasons():
    ds = load_input(_raw([
        _bar(close=float("nan")),
        _bar(open_ms=T0 + 5),
        _bar(closed=False),
        _bar(available_ms=T0 + TF - 1),
        _bar(high=0.95),
        _bar(volume=-1),
        {"symbol": "AAA"},
        _bar(open_ms=True),
        _bar(),
    ]))
    reasons = [r["reason"] for r in ds.rejected]
    assert reasons == ["non_finite", "bad_open_ms", "incomplete_bar",
                       "available_before_close", "inconsistent_ohlcv",
                       "inconsistent_ohlcv", "missing_field", "bad_open_ms"]
    assert list(ds.candles["AAA"]) == [T0]


def test_structural_errors_raise():
    with pytest.raises(ValueError):
        load_input({"schema": "other"})
    with pytest.raises(ValueError):
        load_input(_raw([], timeframe="7m"))
    with pytest.raises(ValueError):
        load_input(_raw([], decision_times=[1.5]))


def test_exact_duplicates_collapse_and_conflicts_raise():
    ds = load_input(_raw([_bar(), _bar(), _bar(available_ms=T0 + 2 * TF, close=1.02)],
                         decision_times=[at(3), at(3)]))
    assert len(ds.candles["AAA"][T0]) == 2          # original + one later revision
    assert ds.decision_times == [at(3)]
    with pytest.raises(ValueError):
        load_input(_raw([_bar(), _bar(close=1.01)]))


def test_bar_usable_only_once_closed_and_available():
    ds = load_input(_raw([_bar(), _bar(available_ms=T0 + 3 * TF, close=1.02)]))
    assert ds.bar_asof("AAA", T0, T0 + TF - 1) is None           # still forming
    assert ds.bar_asof("AAA", T0, T0 + TF).close == 1.05         # closed, first print
    assert ds.bar_asof("AAA", T0, T0 + 3 * TF).close == 1.02     # revision once it landed


def test_membership_is_as_of_including_when_it_became_known():
    ds = load_input(_raw([], membership=[
        {"symbol": "AAA", "from_ms": T0, "to_ms": T0 + 5 * TF, "available_ms": T0},
        {"symbol": "BBB", "from_ms": T0, "available_ms": T0 + 3 * TF},
    ]))
    assert ds.members_asof(T0 + TF) == ["AAA"]
    assert ds.members_asof(T0 + 3 * TF) == ["AAA", "BBB"]
    assert ds.members_asof(T0 + 5 * TF) == ["BBB"]


def test_a_later_closing_revision_removes_the_member_only_once_known():
    """Regression: the open interval and its closing revision were unioned, so
    a removed asset stayed a member forever."""
    ds = load_input(_raw([], membership=[
        {"symbol": "AAA", "from_ms": T0, "to_ms": None, "available_ms": T0, "source": "idx"},
        {"symbol": "AAA", "from_ms": T0, "to_ms": T0 + 5 * TF, "available_ms": T0 + 7 * TF,
         "source": "idx"},
        {"symbol": "BBB", "from_ms": T0, "to_ms": None, "available_ms": T0, "source": "idx"},
        {"symbol": "BBB", "from_ms": T0, "to_ms": T0 + 5 * TF, "available_ms": T0 + 7 * TF,
         "source": "other"},                                   # different identity
    ]))
    assert ds.members_asof(T0 + 6 * TF) == ["AAA", "BBB"]      # closure not yet known
    assert ds.members_asof(T0 + 8 * TF) == ["BBB"]             # AAA closed; BBB idx still open


def test_conflicting_membership_revision_raises():
    with pytest.raises(ValueError, match="membership"):
        load_input(_raw([], membership=[
            {"symbol": "AAA", "from_ms": T0, "to_ms": None, "available_ms": T0},
            {"symbol": "AAA", "from_ms": T0, "to_ms": T0 + TF, "available_ms": T0},
        ]))


def test_conflicting_participation_revision_raises_and_series_stay_apart():
    p = {"symbol": "AAA", "kind": "open_interest", "source": "venA",
         "event_ms": T0, "available_ms": T0, "value": 10.0}
    with pytest.raises(ValueError, match="participation"):
        load_input(_raw([], participation=[p, dict(p, value=11.0)]))
    ds = load_input(_raw([], participation=[p, dict(p), dict(p, source="venB", value=11.0)]))
    assert [(x.source, x.value) for x in ds.participation["AAA"]] == [("venA", 10.0),
                                                                     ("venB", 11.0)]


def test_stable_id_is_deterministic_and_content_addressed():
    assert stable_id("x", 1, {"b": 2, "a": 1}) == stable_id("x", 1, {"a": 1, "b": 2})
    assert stable_id("x", 1) != stable_id("x", 2)


ALLOWED_IMPORTS = {"base64", "zlib", "datetime", "__future__", "argparse", "dataclasses", "hashlib", "json", "math",
                   "pathlib", "statistics", "sys", "trader.cognition",
                   "trader.cognition.attention", "trader.cognition.contracts",
                   "trader.cognition.hypotheses"}


def test_cognition_imports_only_stdlib_and_itself():
    for path in sorted(PKG.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module]
            else:
                continue
            for name in names:
                assert name in ALLOWED_IMPORTS, f"{path.name} imports {name}"


def test_importing_replay_loads_no_runtime_or_network_modules():
    code = ("import sys, json, trader.cognition.replay\n"
            "print(json.dumps(sorted(sys.modules)))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    mods = set(json.loads(out))
    runtime = [m for m in mods if m.startswith("trader.") and not m.startswith("trader.cognition")]
    assert runtime == []
    for banned in ("sqlite3", "ccxt", "requests", "httpx", "aiohttp", "urllib.request",
                   "http.client", "pandas", "openai"):
        assert banned not in mods
