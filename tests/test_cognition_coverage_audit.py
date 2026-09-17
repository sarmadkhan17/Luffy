"""Coverage audit: baseline pinning and drift refusals, destination safety,
deterministic network-free planning, and the fetch stage against a fake
transport — recovery, every failure class, 418/429 stop, overlap flags,
exact ledger accounting, research.db, repaired input and deterministic replay.

The baseline is a small real `cognition_history` export over a temporary
4h store. Sockets are blocked by `no_network`; `urllib_transport` is exercised
only through a mocked HTTPS handler, never the real network."""
import email
import http.client
import io
import json
import math
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.response
from collections import Counter

import pytest

from scripts import cognition_coverage_audit as ca
from scripts import cognition_history as ch
from tests.test_cognition_contracts import ROOT, no_network  # noqa: F401

TF = 14_400_000
START, STOP, RESOLVE = "2026-08-31T00:00:00Z", "2026-08-31T12:00:00Z", "2026-09-01T08:00:00Z"
START_MS = ch.parse_utc(START, "start")
RESOLVE_MS = ch.parse_utc(RESOLVE, "resolve")
WARM = 26
LAST = 8                                   # bar index of resolve_until (exclusive)
BARS = WARM + LAST                         # 34 per symbol
SYMS = ["AAA/USDT", "BBB/USDT", "CCC/USDT", "DDD/USDT", "EEE/USDT", "FFF/USDT"]
DROP = {("BBB/USDT", 5), ("BBB/USDT", 6), ("BBB/USDT", 7), ("CCC/USDT", 6), ("CCC/USDT", 7)}
# Independent of cognition_history: the local candle store shape it reads.
STORE_SCHEMA = ("CREATE TABLE candles (symbol TEXT, tf TEXT, ts INTEGER, open REAL, high REAL, "
                "low REAL, close REAL, volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))")
HUGE_DEC = "1" + "0" * 400                 # decimal string that overflows float to inf
HUGE_INT = 10 ** 400                       # JSON int too large for float


def series(s_i):
    """Full venue truth: bar index -> (open, high, low, close, volume, taker_buy)."""
    out, close = {}, 100.0 + s_i
    for i in range(-WARM, LAST):
        r = (0.004 if (i + s_i) % 2 == 0 else -0.004) + (0.05 if s_i == 0 and i == 1 else 0.0)
        o, close = close, close * math.exp(r)
        out[i] = (o, max(o, close) * 1.001, min(o, close) * 0.999, close,
                  1000.0 + 100 * (i % 2), 400.0 + i)
    return out


def make_baseline(root, symbols=SYMS, drop=DROP):
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "c.db")
    conn.execute(STORE_SCHEMA)
    for s_i, sym in enumerate(symbols):
        conn.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                         [(sym, "4h", START_MS + i * TF, *v) for i, v in series(s_i).items()
                          if (sym, i) not in drop])
    conn.commit()
    conn.close()
    out = root / "base"
    ch.execute(root / "c.db", "4h", START, STOP, RESOLVE, out, now_ms=RESOLVE_MS + 10 * TF)
    n = len(symbols)
    exp = ca.Experiment(
        timeframe="4h", warmup_start_ms=START_MS - WARM * TF, start_ms=START_MS,
        stop_ms=START_MS + 12 * 3_600_000, resolve_until_ms=RESOLVE_MS, n_decisions=3,
        n_symbols=n, bars_per_symbol=BARS, present_total=n * BARS - len(drop),
        missing_total=len(drop), affected_symbols=len({s for s, _ in drop}),
        files_sha256={f: ca.sha256_bytes((out / f).read_bytes()) for f in ca.PINNED_FILES},
        max_bars_per_request=4, max_requests=2)
    return out, exp


def hashes(d):
    return {p.name: ca.sha256_bytes(p.read_bytes()) for p in sorted(d.iterdir())}


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    return make_baseline(tmp_path_factory.mktemp("baseline"))


class Clock:
    def __init__(self, t=RESOLVE_MS / 1000 + 3600):
        self.t, self.sleeps = t, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def kline(i, v):
    o_ms = START_MS + i * TF
    return [o_ms, repr(v[0]), repr(v[1]), repr(v[2]), repr(v[3]), repr(v[4]), o_ms + TF - 1,
            "0", 10, repr(v[5]), "0", "0"]


def ok(rows, url, body=None):
    return ca.Response(200, {"Content-Type": "application/json", "X-MBX-USED-WEIGHT-1M": "5",
                             "Server": "x"},
                       json.dumps(rows).encode() if body is None else body, url)


class Venue:
    def __init__(self, clock, hooks=None):
        self.clock, self.hooks, self.calls = clock, hooks or {}, []

    def __call__(self, url, timeout, max_bytes):
        self.calls.append((url, timeout, max_bytes, self.clock.t))
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        s_i = [s.replace("/", "") for s in SYMS].index(q["symbol"])
        rows = [kline(i, v) for i, v in series(s_i).items()
                if int(q["startTime"]) <= START_MS + i * TF <= int(q["endTime"])]
        hook = self.hooks.get(q["symbol"])
        return hook(rows, url) if hook else ok(rows, url)


def planned(tmp_path, baseline, name="audit"):
    base, exp = baseline
    ca.plan(base, tmp_path / name, experiment=exp)
    return tmp_path / name, exp


def do_fetch(audit, exp, hooks=None):
    clock = Clock()
    venue = Venue(clock, hooks)
    res = ca.fetch(audit, experiment=exp, transport=venue, clock=clock, sleep=clock.sleep)
    return res, venue, clock


# ---------------------------------------------------------------- plan

def test_plan_is_verified_deterministic_and_preserves_the_baseline(tmp_path, baseline):
    base, exp = baseline
    before = hashes(base)
    a = ca.plan(base, tmp_path / "a", experiment=exp)
    ca.plan(base, tmp_path / "b", experiment=exp)
    assert (tmp_path / "a" / "plan.json").read_bytes() == (tmp_path / "b" / "plan.json").read_bytes()
    assert hashes(base) == before
    assert hashes(tmp_path / "a" / "baseline") == {n: before[n] for n in ca.BASELINE_FILES}
    p = a["plan"]
    assert p["counts"] == {"symbols": 6, "bars_per_symbol": BARS, "expected_total": 6 * BARS,
                           "present_total": 6 * BARS - 5, "missing_total": 5,
                           "affected_symbols": 2}
    assert p["missing_keys"] == [[s, START_MS + i * TF] for s, i in sorted(DROP)]
    assert p["config"] == ca.asdict(ca.CognitionConfig())
    bbb, ccc = p["requests"]
    assert (bbb["symbol"], bbb["venue_symbol"], bbb["bars"]) == ("BBB/USDT", "BBBUSDT", 4)
    assert bbb["start_ms"] == START_MS + 4 * TF and bbb["overlap_opens"] == [START_MS + 4 * TF]
    assert bbb["end_ms"] == RESOLVE_MS - 1 and bbb["last_open_ms"] == RESOLVE_MS - TF
    assert bbb["params"] == {"symbol": "BBBUSDT", "interval": "4h", "startTime": START_MS + 4 * TF,
                             "endTime": RESOLVE_MS - 1, "limit": 100}
    assert bbb["url"].startswith("https://fapi.binance.com/fapi/v1/klines?symbol=BBBUSDT&")
    assert ccc["bars"] == 3 and ccc["missing_opens"] == [START_MS + 6 * TF, START_MS + 7 * TF]


def test_baseline_pinning_and_drift_refusals(tmp_path, baseline):
    base, exp = baseline
    bad_pin = ca.Experiment(**{**ca.asdict(exp), "files_sha256": {**exp.files_sha256,
                                                                   "trace.json": "0" * 64}})
    cases = [(bad_pin, base, "pinned"),
             (ca.Experiment(**{**ca.asdict(exp), "missing_total": 6}), base, "counts"),
             (ca.Experiment(**{**ca.asdict(exp), "n_decisions": 4}), base, "decision count"),
             (ca.Experiment(**{**ca.asdict(exp), "start_ms": START_MS + TF}), base, "window")]

    cfg_dir = tmp_path / "cfg"
    shutil.copytree(base, cfg_dir)
    m = json.loads((cfg_dir / "manifest.json").read_text())
    m["config"]["k"] = 4
    (cfg_dir / "manifest.json").write_text(json.dumps(m))
    cases.append((exp, cfg_dir, "config"))

    canon = tmp_path / "canon"
    shutil.copytree(base, canon)
    m = json.loads((canon / "manifest.json").read_text())
    m["hashes"]["input_sha256"] = "0" * 64
    (canon / "manifest.json").write_text(json.dumps(m))
    cases.append((exp, canon, "input canonical"))

    bytes_dir = tmp_path / "bytes"                   # same canonical input, other bytes
    shutil.copytree(base, bytes_dir)
    (bytes_dir / "input.json").write_text(json.dumps(json.loads((base / "input.json").read_text()),
                                                     indent=2, sort_keys=True))
    rebyte = ca.Experiment(**{**ca.asdict(exp), "files_sha256": {
        **exp.files_sha256, "input.json": ca.sha256_bytes((bytes_dir / "input.json").read_bytes())}})
    cases.append((rebyte, bytes_dir, "files_sha256"))

    for i, (e, d, msg) in enumerate(cases):
        with pytest.raises(ca.AuditError, match=msg):
            ca.plan(d, tmp_path / f"out{i}", experiment=e)
        assert not (tmp_path / f"out{i}").exists()


def test_destination_refusals(tmp_path, baseline):
    base, exp = baseline
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "keep").write_text("mine")
    data = tmp_path / "repo" / "data"
    data.mkdir(parents=True)
    (tmp_path / "into_base").symlink_to(base)
    (tmp_path / "into_data").symlink_to(data)
    cases = [(busy, "not empty"), (base, "overlaps"), (base / "sub", "overlaps"),
             (base.parent, "overlaps"), (tmp_path / "into_base" / "x", "overlaps"),
             (data / "audit", "data/"), (tmp_path / "into_data" / "audit", "data/")]
    for dest, msg in cases:
        with pytest.raises(ca.AuditError, match=msg):
            ca.plan(base, dest, experiment=exp, repo_data=data)
    with pytest.raises(ca.AuditError, match="data/"):
        ca.plan(base, ca.REPO_DATA / "coverage-audit-never-created", experiment=exp)
    assert not (ca.REPO_DATA / "coverage-audit-never-created").exists()
    assert sorted(p.name for p in busy.iterdir()) == ["keep"]
    assert list(data.iterdir()) == [] and not (base / "sub").exists()
    with pytest.raises(ca.AuditError, match="overlaps"):
        ca.plan(tmp_path / "into_base", base / "x", experiment=exp)


def test_symbol_mapping_and_overlap_refusals(tmp_path):
    assert ca.venue_symbol("1000PEPE/USDT") == "1000PEPEUSDT"
    for bad in ("BTC/USDT:USDT", "BTCUSDT", "btc/usdt", "BTC/USDC", "BTC-USDT", "/USDT", None):
        with pytest.raises(ca.AuditError, match="mapping"):
            ca.venue_symbol(bad)
    syms = ["AAA/USDT", "BBB/USDT:USDT", "CCC/USDT", "DDD/USDT", "EEE/USDT"]
    base, exp = make_baseline(tmp_path / "m", symbols=syms, drop={("BBB/USDT:USDT", 7)})
    with pytest.raises(ca.AuditError, match="mapping"):
        ca.plan(base, tmp_path / "out", experiment=exp)
    base, exp = make_baseline(tmp_path / "o", drop={("DDD/USDT", -WARM)})
    with pytest.raises(ca.AuditError, match="overlap"):
        ca.plan(base, tmp_path / "out", experiment=exp)
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------- fetch

def test_full_recovery_evidence_db_repaired_input_and_replay(tmp_path, baseline):
    base, exp = baseline
    before = hashes(base)
    audit, exp = planned(tmp_path, baseline)
    res, venue, clock = do_fetch(audit, exp)
    fdir, m = audit / "fetch", res["manifest"]

    plan = json.loads((audit / "plan.json").read_text())
    assert [c[0] for c in venue.calls] == [r["url"] for r in plan["requests"]]
    assert all(c[1] == 20.0 and c[2] == 1_000_000 for c in venue.calls)
    assert clock.sleeps == [pytest.approx(0.3)]
    assert venue.calls[1][3] - venue.calls[0][3] >= 0.3 - 1e-6

    assert len(res["ledger"]) == 5 and m["ledger_counts"]["recovered"] == 5
    assert m["recovered_plus_remaining"] == 5 and m["review_flags"] == []
    reqs = json.loads((fdir / "requests.json").read_text())
    for r in reqs:
        body = (fdir / r["body_file"]).read_bytes()
        assert r["body_sha256"] == ca.sha256_bytes(body) and r["http_status"] == 200
        assert r["headers"] == {"content-type": "application/json", "x-mbx-used-weight-1m": "5"}
        assert r["overlap"][0]["status"] == "match" and r["request_utc"] and r["retrieval_utc"]

    raw = json.loads((base / "input.json").read_text())
    conn = sqlite3.connect(fdir / "research.db")
    assert conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 6 * BARS
    assert conn.execute("SELECT COUNT(*) FROM candles WHERE taker_buy IS NULL").fetchone()[0] \
        == len(raw["candles"])
    got = conn.execute("SELECT open, volume, taker_buy FROM candles WHERE symbol='BBB/USDT' "
                       "AND ts=?", (START_MS + 7 * TF,)).fetchone()
    assert got == (series(1)[7][0], series(1)[7][4], series(1)[7][5])
    conn.close()

    rep = json.loads((fdir / "repaired-input.json").read_text())
    assert rep["candles"][:len(raw["candles"])] == raw["candles"]
    added = rep["candles"][len(raw["candles"]):]
    assert sorted((c["symbol"], (c["open_ms"] - START_MS) // TF) for c in added) == sorted(DROP)
    assert all(c["source"] == ca.SOURCE_TAG and c["available_ms"] == c["open_ms"] + TF
               for c in added)
    for k in ("membership", "decision_times", "participation", "timeframe"):
        assert rep[k] == raw[k]
    assert rep["metadata"]["coverage_audit"]["complete_recovery"] is True

    assert m["replay"]["performed"] and m["replay"]["rerun_identical"]
    assert m["replay"]["baseline_rerun_identical"] and m["replay"]["label"] == "complete"
    trace_text = (fdir / "repaired-trace.json").read_text()
    assert ca.dump(ca.run(json.loads((fdir / "repaired-input.json").read_text()),
                          ca.CognitionConfig(), RESOLVE_MS)) == trace_text
    cmp_ = json.loads((fdir / "comparison.json").read_text())
    assert cmp_["coverage"]["original"]["missing_total"] == 5
    assert cmp_["coverage"]["repaired"]["missing_total"] == 0
    assert set(cmp_["selected"]) >= {"original", "repaired", "only_original", "only_repaired"}

    assert m["files_sha256"] == {str(p.relative_to(fdir)): ca.sha256_bytes(p.read_bytes())
                                 for p in fdir.rglob("*") if p.is_file()
                                 and p.name != "manifest.json"}
    assert "report.md" in m["files_sha256"] and "raw/000-BBBUSDT.body" in m["files_sha256"]
    assert "manifest.json" not in m["files_sha256"] and m["baseline"]["preserved"]
    assert m["baseline"]["original_after"]["status"] == "verified"
    assert hashes(base) == before

    snapshot = {p: p.read_bytes() for p in fdir.rglob("*") if p.is_file()}
    with pytest.raises(ca.AuditError, match="never overwrite"):
        do_fetch(audit, exp)
    assert {p: p.read_bytes() for p in fdir.rglob("*") if p.is_file()} == snapshot


def _set(rows, r, c, v):
    rows[r][c] = v
    return rows


def _err(status, headers=None, body=b'{"code":-1}'):
    return lambda rows, url: ca.Response(status, headers or {}, body, url)


def _raise(rows, url):
    raise OSError("connection reset")


SCENARIOS = {
    "empty": (lambda rows, url: ok([], url), {"absent_in_response": 3}, "recovered", 2,
              ["overlap_missing"]),
    "partial": (lambda rows, url: ok(rows[:-1], url), {"recovered": 2, "absent_in_response": 1},
                "recovered", 2, []),
    "http_500": (_err(500), {"request_failed": 3}, "recovered", 2, []),
    "network": (_raise, {"request_failed": 3}, "recovered", 2, []),
    "http_429": (_err(429, {"Retry-After": "60"}), {"request_failed": 3}, "not_attempted", 1, []),
    "http_418": (_err(418), {"request_failed": 3}, "not_attempted", 1, []),
    "oversized": (lambda rows, url: ok(rows, url, b"[" + b" " * 1_000_000 + b"]"),
                  {"request_failed": 3}, "recovered", 2, []),
    "redirect_host": (lambda rows, url: ok(rows, url.replace("fapi.binance.com", "evil.test")),
                      {"request_failed": 3}, "recovered", 2, []),
    "redirect_302": (_err(302, {"Location": "https://evil.test/"}, b""),
                     {"request_failed": 3}, "recovered", 2, []),
    "redirect_same_host_endpoint": (
        lambda rows, url: ok(rows, url.replace("/fapi/v1/klines", "/fapi/v1/continuousKlines")),
        {"request_failed": 3}, "recovered", 2, []),
    "redirect_302_same_host": (_err(302, {"Location": "https://fapi.binance.com/fapi/v1/"
                                                      "continuousKlines"}, b""),
                               {"request_failed": 3}, "recovered", 2, []),
    "huge_decimal_string": (lambda rows, url: ok(_set(rows, 1, 2, HUGE_DEC), url),
                            {"invalid_response": 3}, "recovered", 2, []),
    "huge_int": (lambda rows, url: ok(_set(rows, 1, 2, HUGE_INT), url),
                 {"invalid_response": 3}, "recovered", 2, []),
    "huge_taker_buy": (lambda rows, url: ok(_set(_set(rows, 1, 9, HUGE_DEC), 2, 9, HUGE_INT), url),
                       {"recovered": 3}, "recovered", 2, []),
    "nan_string": (lambda rows, url: ok(_set(rows, 1, 1, "NaN"), url),
                   {"invalid_response": 3}, "recovered", 2, []),
    "nan_constant": (lambda rows, url: ok(rows, url, json.dumps(_set(rows, 1, 4, "X")).replace(
        '"X"', "NaN").encode()), {"invalid_response": 3}, "recovered", 2, []),
    "negative_price": (lambda rows, url: ok(_set(rows, 1, 3, "-1.0"), url),
                       {"invalid_response": 3}, "recovered", 2, []),
    "low_above_high": (lambda rows, url: ok(_set(rows, 1, 3, "1000000.0"), url),
                       {"invalid_response": 3}, "recovered", 2, []),
    "misaligned": (lambda rows, url: ok(_set(_set(rows, 1, 0, rows[1][0] + 1), 1, 6,
                                             rows[1][6] + 1), url),
                   {"invalid_response": 3}, "recovered", 2, []),
    "bad_close_time": (lambda rows, url: ok(_set(rows, 1, 6, rows[1][6] + 1), url),
                       {"invalid_response": 3}, "recovered", 2, []),
    "string_timestamp": (lambda rows, url: ok(_set(rows, 1, 0, str(rows[1][0])), url),
                         {"invalid_response": 3}, "recovered", 2, []),
    "duplicate_identical": (lambda rows, url: ok(rows + [list(rows[1])], url),
                            {"invalid_response": 3}, "recovered", 2, []),
    "out_of_range": (lambda rows, url: ok([[rows[0][0] - TF, *rows[0][1:6], rows[0][6] - TF,
                                            *rows[0][7:]]] + rows, url),
                     {"invalid_response": 3}, "recovered", 2, []),
    "short_row": (lambda rows, url: ok(_set(rows, 1, slice(11, 12), []), url),
                  {"invalid_response": 3}, "recovered", 2, []),
    "wrong_shape": (lambda rows, url: ok({"code": 0}, url), {"invalid_response": 3},
                    "recovered", 2, []),
    "overlap_mismatch": (lambda rows, url: ok(_set(rows, 0, 5, repr(float(rows[0][5]) + 1)), url),
                         {"recovered": 3}, "recovered", 2, ["overlap_mismatch"]),
    "overlap_missing": (lambda rows, url: ok(rows[1:], url), {"recovered": 3}, "recovered", 2,
                        ["overlap_missing"]),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_fetch_failure_classes_are_accounted_exactly(tmp_path, baseline, name):
    hook, bbb_want, ccc_want, n_calls, flag_kinds = SCENARIOS[name]
    audit, exp = planned(tmp_path, baseline)
    base = baseline[0]
    before = hashes(base)
    res, venue, _ = do_fetch(audit, exp, {"BBBUSDT": hook})
    m, ledger, fdir = res["manifest"], res["ledger"], audit / "fetch"

    assert len(venue.calls) == n_calls
    assert len(ledger) == 5 and sum(m["ledger_counts"].values()) == 5
    assert len({(x["symbol"], x["open_ms"]) for x in ledger}) == 5
    assert dict(Counter(x["status"] for x in ledger if x["symbol"] == "BBB/USDT")) == bbb_want
    assert {x["status"] for x in ledger if x["symbol"] == "CCC/USDT"} == {ccc_want}
    assert [f["kind"] for f in m["review_flags"]] == flag_kinds

    reqs = json.loads((fdir / "requests.json").read_text())
    bbb = reqs[0]
    if name == "network":
        assert bbb["body_file"] is None and bbb["error"]["kind"] == "network_error"
    else:
        assert (fdir / bbb["body_file"]).is_file() and bbb["http_status"] is not None
        assert bbb["body_sha256"] == ca.sha256_bytes((fdir / bbb["body_file"]).read_bytes())
    if next(iter(bbb_want)) == "invalid_response":
        assert bbb["outcome"] == "invalid_response" and bbb["validation_errors"]
    if ccc_want == "not_attempted":
        assert reqs[1]["outcome"] == "not_attempted" and "stopped_after_http" in reqs[1]["reason"]
    if name == "overlap_mismatch":
        assert m["review_flags"][0]["differences"][0]["field"] == "volume"
    if name.startswith("redirect"):
        assert bbb["outcome"] == "request_failed" and bbb["error"]["kind"] == "redirect_refused"
    if name in ("huge_decimal_string", "huge_int"):
        assert [e["reason"] for e in bbb["validation_errors"]] == ["bad_number"]
    if name == "huge_taker_buy":
        conn = sqlite3.connect(fdir / "research.db")
        tb = dict(conn.execute("SELECT ts, taker_buy FROM candles WHERE symbol='BBB/USDT' "
                               "AND ts >= ?", (START_MS + 5 * TF,)).fetchall())
        conn.close()
        assert tb == {START_MS + 5 * TF: None, START_MS + 6 * TF: None,
                      START_MS + 7 * TF: series(1)[7][5]}

    recovered = m["ledger_counts"]["recovered"]
    raw = json.loads((base / "input.json").read_text())
    rep = json.loads((fdir / "repaired-input.json").read_text())
    assert len(rep["candles"]) == len(raw["candles"]) + recovered
    assert rep["candles"][:len(raw["candles"])] == raw["candles"]
    conn = sqlite3.connect(fdir / "research.db")
    assert conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == len(raw["candles"]) + recovered
    conn.close()
    if flag_kinds:
        assert m["replay"]["performed"] is False and m["replay"]["withheld_reasons"]
        assert not (fdir / "repaired-trace.json").exists()
        assert "WITHHELD" in (fdir / "report.md").read_text()
    else:
        assert m["replay"]["performed"] and m["replay"]["rerun_identical"]
        assert m["replay"]["complete_recovery"] is (recovered == 5)
        assert m["replay"]["label"].startswith("complete" if recovered == 5 else "incomplete")
        cmp_ = json.loads((fdir / "comparison.json").read_text())
        assert cmp_["coverage"]["repaired"]["missing_total"] == 5 - recovered
    assert hashes(base) == before


def test_fetch_refuses_tampered_plan_baseline_and_early_clock(tmp_path, baseline):
    audit, exp = planned(tmp_path, baseline, "a")
    text = (audit / "plan.json").read_text()
    (audit / "plan.json").write_text(text.replace("fapi.binance.com", "testnet.binancefuture.com"))
    clock = Clock()
    venue = Venue(clock)
    with pytest.raises(ca.AuditError, match="plan"):
        ca.fetch(audit, experiment=exp, transport=venue, clock=clock, sleep=clock.sleep)

    audit_b, _ = planned(tmp_path, baseline, "b")
    with open(audit_b / "baseline" / "report.md", "a") as f:
        f.write("\nedited")
    with pytest.raises(ca.AuditError, match="plan"):
        ca.fetch(audit_b, experiment=exp, transport=venue, clock=clock, sleep=clock.sleep)

    audit_c, _ = planned(tmp_path, baseline, "c")
    early = Clock(RESOLVE_MS / 1000 - 1)
    with pytest.raises(ca.AuditError, match="closed"):
        ca.fetch(audit_c, experiment=exp, transport=venue, clock=early, sleep=early.sleep)
    assert venue.calls == []
    assert not any((d / "fetch").exists() for d in (audit, audit_b, audit_c))


def _own_baseline(tmp_path, baseline):
    """A private copy of the module baseline, so tests may remove or edit it."""
    base, exp = baseline
    shutil.copytree(base, tmp_path / "orig")
    return planned(tmp_path, (tmp_path / "orig", exp))


def test_fetch_refuses_absent_or_changed_original_before_any_request(tmp_path, baseline):
    audit, exp = _own_baseline(tmp_path, baseline)
    shutil.rmtree(tmp_path / "orig")
    with pytest.raises(ca.AuditError, match="absent_not_checked"):
        do_fetch(audit, exp)
    assert not (audit / "fetch").exists()

    sub = tmp_path / "changed"
    sub.mkdir()
    audit, exp = _own_baseline(sub, baseline)
    with open(sub / "orig" / "report.md", "a") as f:
        f.write("\nedited")
    clock = Clock()
    venue = Venue(clock)
    with pytest.raises(ca.AuditError, match="drifted"):
        ca.fetch(audit, experiment=exp, transport=venue, clock=clock, sleep=clock.sleep)
    assert venue.calls == [] and not (audit / "fetch").exists()


@pytest.mark.parametrize("change", ["absent", "report_md"])
def test_fetch_records_and_refuses_original_changed_during_work(tmp_path, baseline, change):
    audit, exp = _own_baseline(tmp_path, baseline)
    orig = tmp_path / "orig"

    def tamper(rows, url):
        if change == "absent":
            orig.rename(tmp_path / "gone")
        else:
            with open(orig / "report.md", "a") as f:
                f.write("\nedited")
        return ok(rows, url)
    with pytest.raises(ca.AuditError, match="not preserved"):
        do_fetch(audit, exp, {"CCCUSDT": tamper})
    fdir = audit / "fetch"
    m = json.loads((fdir / "manifest.json").read_text())
    b = m["baseline"]
    assert b["preserved"] is False and b["copy_files_sha256_after"] == b["copy_files_sha256_before"]
    if change == "absent":
        assert b["original_after"]["status"] == "absent_not_checked"
    else:
        assert b["original_after"]["status"] == "verified"
        assert b["original_after"]["files_sha256"] != b["copy_files_sha256_before"]
    assert "baseline preserved `False`" in (fdir / "report.md").read_text()
    assert (fdir / "requests.json").is_file() and (fdir / "ledger.json").is_file()


def test_validate_klines_units():
    rq = {"start_ms": START_MS, "last_open_ms": START_MS + 2 * TF}
    rows = [kline(i, series(0)[i]) for i in range(3)]
    got, errors = ca.validate_klines(json.dumps(rows).encode(), rq, TF, RESOLVE_MS)
    assert errors == [] and sorted(got) == [START_MS + i * TF for i in range(3)]
    _, errors = ca.validate_klines(json.dumps(rows).encode(), rq, TF, START_MS + 2 * TF - 1)
    assert [e["reason"] for e in errors] == ["not_closed", "not_closed"]
    for body in (b"[[1, Infinity]]", b"not json", b"\xff"):
        _, errors = ca.validate_klines(body, rq, TF, RESOLVE_MS)
        assert errors[0]["reason"] == "unparseable_json"
    bad = [list(r) for r in rows]
    bad[0][5] = "1e3"
    _, errors = ca.validate_klines(json.dumps(bad).encode(), rq, TF, RESOLVE_MS)
    assert errors[0]["reason"] == "bad_number"
    for col in range(1, 6):
        for huge in (HUGE_DEC, HUGE_INT):
            bad = [list(r) for r in rows]
            bad[0][col] = huge
            _, errors = ca.validate_klines(json.dumps(bad).encode(), rq, TF, RESOLVE_MS)
            assert [e["reason"] for e in errors] == ["bad_number"], (col, type(huge))
    for huge in (HUGE_DEC, HUGE_INT, "-1", None):
        bad = [list(r) for r in rows]
        bad[0][9] = huge
        got, errors = ca.validate_klines(json.dumps(bad).encode(), rq, TF, RESOLVE_MS)
        assert errors == [] and got[START_MS]["taker_buy"] is None
        assert json.dumps(got[START_MS], allow_nan=False)


def test_requests_and_ledger_persist_before_db_and_replay_failures(tmp_path, baseline, monkeypatch):
    for i, target in enumerate(("write_research_db", "run")):
        audit, exp = planned(tmp_path, baseline, f"a{i}")

        def boom(*a, **k):
            raise RuntimeError("late failure")
        monkeypatch.setattr(ca, target, boom)
        with pytest.raises(RuntimeError, match="late failure"):
            do_fetch(audit, exp)
        monkeypatch.undo()
        fdir = audit / "fetch"
        reqs = json.loads((fdir / "requests.json").read_text())
        ledger = json.loads((fdir / "ledger.json").read_text())
        assert [r["outcome"] for r in reqs] == ["validated", "validated"]
        assert all((fdir / r["body_file"]).is_file() for r in reqs)
        assert len(ledger) == 5 and {x["status"] for x in ledger} == {"recovered"}
        assert not (fdir / "manifest.json").exists()
        assert (fdir / "research.db").exists() is (target == "run")


# ---------------------------------------------------------------- real transport, mocked opener

class _FakeHTTPS(urllib.request.HTTPSHandler):
    """Replaces the socket-level HTTPS handler; everything above it is real urllib."""

    def __init__(self, replies):
        super().__init__()
        self.replies, self.seen = list(replies), []

    def https_open(self, req):
        self.seen.append({"url": req.full_url, "timeout": req.timeout,
                          "method": req.get_method()})
        status, headers, body = self.replies.pop(0)
        msg = email.message_from_string("".join(f"{k}: {v}\n" for k, v in headers.items()) + "\n",
                                        _class=http.client.HTTPMessage)
        resp = urllib.response.addinfourl(io.BytesIO(body), msg, req.full_url, status)
        resp.msg = "fake"
        return resp


@pytest.fixture
def fake_opener(monkeypatch):
    state = {}
    real = urllib.request.build_opener

    def build(*handlers):
        state["handlers"] = handlers
        return real(*handlers, state["fake"])
    monkeypatch.setattr(ca.urllib.request, "build_opener", build)
    return state


def test_urllib_transport_refuses_redirects(fake_opener):
    url = ca.KLINES_URL + "?symbol=BBBUSDT"
    for location in ("https://evil.test/x", ca.KLINES_URL + "?symbol=OTHERUSDT",
                     "https://fapi.binance.com/fapi/v1/continuousKlines"):
        fake_opener["fake"] = fake = _FakeHTTPS([(302, {"Location": location}, b"moved"),
                                                 (200, {}, b"[]")])
        r = ca.urllib_transport(url, 7.5, 1000)
        assert (r.status, r.url, r.body, r.headers["Location"]) == (302, url, b"moved", location)
        assert len(fake.seen) == 1 and len(fake.replies) == 1          # never followed
    handlers = fake_opener["handlers"]
    assert ca._NoRedirect in handlers
    assert [h.proxies for h in handlers if isinstance(h, urllib.request.ProxyHandler)] == [{}]


def test_urllib_transport_keeps_error_bodies_passes_timeout_and_bounds_reads(fake_opener):
    url = ca.KLINES_URL + "?symbol=BBBUSDT"
    fake_opener["fake"] = fake = _FakeHTTPS([
        (500, {"Content-Type": "application/json"}, b'{"code":-1,"msg":"boom"}'),
        (429, {"Retry-After": "60"}, b"e" * 100),
        (200, {"Content-Type": "application/json"}, b"[" + b" " * 100 + b"]"),
        (200, {}, b"[1]")])
    r = ca.urllib_transport(url, 7.5, 1000)
    assert (r.status, r.body, r.url) == (500, b'{"code":-1,"msg":"boom"}', url)
    assert r.headers["Content-Type"] == "application/json"
    r = ca.urllib_transport(url, 3.0, 10)
    assert (r.status, r.body, r.headers["Retry-After"]) == (429, b"e" * 11, "60")
    r = ca.urllib_transport(url, 4.0, 10)
    assert r.status == 200 and r.body == (b"[" + b" " * 100)[:11] and r.url == url
    r = ca.urllib_transport(url, 5.0, 10)
    assert (r.status, r.body) == (200, b"[1]")
    assert [s["timeout"] for s in fake.seen] == [7.5, 3.0, 4.0, 5.0]
    assert {s["method"] for s in fake.seen} == {"GET"} and {s["url"] for s in fake.seen} == {url}


def test_cli_plan_and_import_isolation(tmp_path, baseline, capsys):
    with pytest.raises(SystemExit):
        ca.main(["fetch"])
    assert ca.main(["plan", "--original-dir", str(baseline[0]),
                    "--output-dir", str(tmp_path / "x")]) == 2     # pinned hashes differ
    assert "pinned" in capsys.readouterr().err and not (tmp_path / "x").exists()
    code = ("import sys, json, scripts.cognition_coverage_audit\n"
            "print(json.dumps(sorted(sys.modules)))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    mods = set(json.loads(out))
    assert [x for x in mods if x.startswith("trader.") and not x.startswith("trader.cognition")] == []
    for banned in ("ccxt", "requests", "httpx", "aiohttp", "dotenv", "websockets"):
        assert banned not in mods
