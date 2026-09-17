"""Retrospective historical replay: read-only store access, bounds and warmup
extraction, pre-start universe, gaps and malformed rows kept auditable,
deterministic export/replay/summary, and the explicit-path CLI.

Temporary SQLite stores only; sockets are blocked by `no_network`."""
import json
import math
import sqlite3
import subprocess
import sys

import pytest

from scripts import cognition_history as ch
from tests.test_cognition_contracts import ROOT, no_network  # noqa: F401

TF = 3_600_000
START = "2020-01-03T00:00:00Z"
START_MS = 1_578_009_600_000
STOP = "2020-01-03T06:00:00Z"            # 6 decisions
RESOLVE = "2020-01-03T12:00:00Z"         # last deadline 05:00 + 5h = 10:00
RESOLVE_MS = START_MS + 12 * TF
WARM = 26
SYMS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
SCHEMA = ("CREATE TABLE candles (symbol TEXT, tf TEXT, ts INTEGER, open REAL, high REAL, "
          "low REAL, close REAL, volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))")


def _rows(sym, s_i, first_bar, last_bar, spike=None):
    """Bars with open index in [first_bar, last_bar), index 0 = START."""
    out, close = [], 100.0 + s_i
    for i in range(-WARM - 10, last_bar):
        r = (0.004 if (i + s_i) % 2 == 0 else -0.004) + (0.05 if spike and i == spike else 0.0)
        o, close = close, close * math.exp(r)
        if i >= first_bar:
            out.append((sym, "1h", START_MS + i * TF, o, max(o, close) * 1.001,
                        min(o, close) * 0.999, close, 1000.0 + 100 * (i % 2), 500.0))
    return out


def make_db(path, extra=(), symbols=SYMS, drop=(), schema=SCHEMA):
    conn = sqlite3.connect(path)
    conn.execute(schema)
    rows = []
    for s_i, sym in enumerate(symbols):
        rows += [r for r in _rows(sym, s_i, -WARM - 10, 20, spike=2 if sym == "CCC" else None)
                 if (sym, (r[2] - START_MS) // TF) not in drop]
    conn.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)", list(rows) + list(extra))
    conn.execute("INSERT INTO candles VALUES ('AAA','4h',?,1,1,1,1,1,1)", (START_MS,))
    conn.commit()
    conn.close()
    return path


def execute(db, out, **kw):
    args = dict(allow_incomplete=False, now_ms=START_MS + 1000 * TF)
    args.update(kw)
    bounds = {k: args.pop(k) for k in ("start", "stop", "resolve") if k in args}
    return ch.execute(db, "1h", bounds.get("start", START), bounds.get("stop", STOP),
                      bounds.get("resolve", RESOLVE), out, **args)


def test_connection_is_read_only_and_never_creates_a_db(tmp_path):
    db = make_db(tmp_path / "c.db")
    before = db.read_bytes()
    conn = ch.connect_readonly(db)
    for sql in ("INSERT INTO candles VALUES ('X','1h',0,1,1,1,1,1,1)", "DELETE FROM candles",
                "CREATE TABLE t (x)"):
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(sql)
    conn.close()
    assert db.read_bytes() == before
    missing = tmp_path / "nope.db"
    with pytest.raises(ch.HistoryError, match="not found"):
        ch.connect_readonly(missing)
    assert not missing.exists()
    with pytest.raises(ch.HistoryError, match="not found"):
        execute(missing, tmp_path / "out")
    assert not missing.exists() and not (tmp_path / "out").exists()


def test_timestamps_must_be_exact_utc():
    assert ch.parse_utc(START, "start") == START_MS
    for bad in ("2020-01-03T00:00:00", "2020-01-03", "2020-01-03T00:00:00+00:00",
                "2020-01-03 00:00:00Z", "2020-02-30T00:00:00Z", 5):
        with pytest.raises(ch.HistoryError):
            ch.parse_utc(bad, "start")


def test_bounds_are_validated():
    cfg = ch.CognitionConfig()
    now = START_MS + 1000 * TF
    ok = ch.plan_window("1h", START_MS, START_MS + 6 * TF, RESOLVE_MS, cfg, now, False, 500)
    assert ok["decision_times"] == [START_MS + i * TF for i in range(6)]
    assert ok["warmup_start_ms"] == START_MS - WARM * TF
    assert ok["last_decision_deadline_ms"] == START_MS + 10 * TF and ok["complete_horizon"]
    cases = [("1h", START_MS + 1, START_MS + 6 * TF, RESOLVE_MS, now, False, 500, "aligned"),
             ("1h", START_MS, START_MS, RESOLVE_MS, now, False, 500, "after start"),
             ("1h", START_MS, START_MS + 6 * TF, START_MS + 5 * TF, now, False, 500, ">= stop"),
             ("1h", START_MS, START_MS + 6 * TF, RESOLVE_MS, RESOLVE_MS - 1, False, 500, "future"),
             ("1h", START_MS, START_MS + 6 * TF, START_MS + 8 * TF, now, False, 500, "deadline"),
             ("1h", START_MS, START_MS + 6 * TF, RESOLVE_MS, now, False, 5, "max_decisions"),
             ("7m", START_MS, START_MS + 6 * TF, RESOLVE_MS, now, False, 500, "timeframe")]
    for tf, s, e, r, n, inc, mx, msg in cases:
        with pytest.raises(ch.HistoryError, match=msg):
            ch.plan_window(tf, s, e, r, cfg, n, inc, mx)
    part = ch.plan_window("1h", START_MS, START_MS + 6 * TF, START_MS + 8 * TF, cfg, now,
                          True, 500)
    assert part["complete_horizon"] is False


def test_window_universe_and_gaps(tmp_path):
    future_only = [r for r in _rows("NEW", 7, 1, 20)]
    late_warm = [r for r in _rows("LATE", 8, -1, 20)]           # one warmup bar only
    ancient = [r for r in _rows("OLD", 9, -WARM - 10, -WARM)]    # entirely before warmup
    db = make_db(tmp_path / "c.db", extra=future_only + late_warm + ancient,
                 drop={("BBB", -3)})
    res = execute(db, tmp_path / "out")
    m, raw = res["manifest"], json.loads((tmp_path / "out" / "input.json").read_text())

    assert m["universe"] == sorted(SYMS + ["LATE"])
    assert m["excluded_post_start_symbols"] == 1
    opens = {c["open_ms"] for c in raw["candles"]}
    assert min(opens) == START_MS - WARM * TF and max(opens) == RESOLVE_MS - TF
    assert {c["symbol"] for c in raw["candles"]} == set(m["universe"])
    assert all(c["available_ms"] == c["open_ms"] + TF for c in raw["candles"])
    assert raw["participation"] == [] and raw["metadata"]["kind"] == "retrospective_reconstruction"
    assert all(x["from_ms"] == START_MS and x["to_ms"] is None for x in raw["membership"])

    cov = m["coverage"]["per_symbol"]
    assert cov["AAA"] == {"expected_bars": WARM + 12, "valid_bars": WARM + 12, "missing_bars": 0,
                          "rejected_rows": 0, "first_open_ms": START_MS - WARM * TF,
                          "last_open_ms": RESOLVE_MS - TF}
    assert cov["BBB"]["missing_bars"] == 1
    assert cov["LATE"]["valid_bars"] == 13 and cov["LATE"]["missing_bars"] == WARM - 1

    trace = json.loads((tmp_path / "out" / "trace.json").read_text())
    first = {r["symbol"]: r for r in trace["decisions"][0]["universe"]}
    assert first["BBB"]["status"] == "gap" and first["LATE"]["status"] == "warmup"
    assert first["CCC"]["status"] == "ok"
    assert res["report"]["omission_reason"]["gap"] >= 1


def test_rows_after_the_window_cannot_change_the_export(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    base = execute(make_db(tmp_path / "x" / "c.db"), tmp_path / "a")
    later = [r for r in _rows("NEW", 7, 13, 30)] + [
        ("AAA", "1h", RESOLVE_MS + i * TF, 1, 1, 1, 1, 1, 1) for i in range(20, 25)]
    grown = execute(make_db(tmp_path / "y" / "c.db", extra=later), tmp_path / "b")
    for k in ("input_sha256", "trace_sha256", "config_sha256"):
        assert base["manifest"]["hashes"][k] == grown["manifest"]["hashes"][k]
    assert grown["manifest"]["excluded_post_start_symbols"] == 0       # NEW starts after resolve


def test_export_replay_and_summary_are_deterministic(tmp_path):
    db = make_db(tmp_path / "c.db")
    a, b = execute(db, tmp_path / "a"), execute(db, tmp_path / "b")
    for name in ("input.json", "trace.json", "report.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    assert a["manifest"]["hashes"] == b["manifest"]["hashes"]
    assert a["manifest"]["rerun_identical"] is True
    assert a["manifest"]["config"] == ch.asdict(ch.CognitionConfig())

    out = subprocess.run(
        [sys.executable, "-c",
         "import socket,sys\n"
         "def boom(*a,**k): raise AssertionError('network')\n"
         "socket.socket.connect=boom; socket.create_connection=boom\n"
         "from trader.cognition.replay import main\n"
         "sys.exit(main(sys.argv[1:]))",
         "--input", str(tmp_path / "a" / "input.json"), "--output", str(tmp_path / "t.json"),
         "--end-ms", str(RESOLVE_MS)], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "t.json").read_bytes() == (tmp_path / "a" / "trace.json").read_bytes()
    trace = json.loads((tmp_path / "t.json").read_text())
    assert ch.sha256_json(trace) == a["manifest"]["hashes"]["trace_sha256"]


def test_summary_denominators_and_unresolved(tmp_path):
    db = make_db(tmp_path / "c.db")
    r = execute(db, tmp_path / "full")["report"]
    trace = json.loads((tmp_path / "full" / "trace.json").read_text())
    assert r["decisions"] == 6 and r["universe_rows"] == 6 * len(SYMS)
    assert r["eligible"] + r["ineligible"] == r["universe_rows"]
    assert r["selected"] + r["eligible_unselected"] == r["eligible"]
    assert r["selected"] >= 1 and r["episodes"] == r["selected"]
    assert sum(r["omission_reason"].values()) == r["universe_rows"] - r["selected"]
    sel, uns = r["baselines"]["raw:selected"], r["baselines"]["raw:unselected"]
    assert sel["samples"] == r["selected"] and uns["samples"] == r["eligible_unselected"]
    assert r["baselines"]["relative:selected"]["samples"] == r["selected"]
    assert sel["unresolved_fraction"] == 0 and sel["abs_forward_z"]["n"] == r["selected"]
    assert sum(sum(c.values()) for c in r["hypothesis_status"].values()) == 3 * r["episodes"]
    assert r["outcomes"] == len(trace["outcomes"]) == 3 * r["episodes"] + 2 * r["eligible"]

    part = execute(db, tmp_path / "part", resolve="2020-01-03T08:00:00Z",
                   allow_incomplete=True)
    p = part["report"]
    assert part["manifest"]["window"]["complete_horizon"] is False
    b = p["baselines"]["raw:unselected"]
    assert 0 < b["unresolved_fraction"] < 1
    assert set(b["unresolved_reason"]) == {"deadline_not_reached"}
    assert b["abs_forward_z"]["n"] == b["status"]["measured"]
    assert "Complete horizon `False`".lower() in (tmp_path / "part" / "report.md").read_text().lower()


def test_malformed_rows_are_rejected_auditably(tmp_path):
    bad = [("AAA", "1h", START_MS - 20 * TF + 5, 1, 1, 1, 1, 1, 1),      # misaligned, in window
           ("BBB", "1h", START_MS + 8 * TF, 1.0, 0.5, 2.0, 1.0, 1.0, 1),  # high < low
           ("CCC", "1h", START_MS + 9 * TF, 1.0, 1.0, 1.0, None, 1.0, 1),  # NULL close
           ("DDD", "1h", START_MS + 9 * TF, 1.0, 1.0, 1.0, 1.0, float("inf"), 1)]
    db = make_db(tmp_path / "c.db", extra=bad,
                 drop={("BBB", 8), ("CCC", 9), ("DDD", 9)})
    m = execute(db, tmp_path / "out")["manifest"]
    reasons = sorted(r["reason"] for r in m["load_rejected"])
    assert reasons == ["bad_open_ms", "inconsistent_ohlcv", "non_finite"]
    assert m["export_rejected"] == [{"symbol": "'DDD'", "ts": repr(START_MS + 9 * TF),
                                     "reason": "not_json_representable"}]
    cov = m["coverage"]["per_symbol"]
    assert cov["BBB"]["rejected_rows"] == 1 and cov["BBB"]["missing_bars"] == 1
    assert cov["DDD"]["missing_bars"] == 1 and cov["AAA"]["rejected_rows"] == 1
    trace = json.loads((tmp_path / "out" / "trace.json").read_text())
    assert len(trace["rejected_inputs"]) == 3
    # CCC's deadline bar for the 05:00 decision (open 9, close 10) was rejected: unresolved.
    unresolved = [o for o in trace["outcomes"] if o["status"] == "unresolved"]
    assert any(o["measurement"]["reason"] == "deadline_bar_unavailable" for o in unresolved)


def test_store_refusals(tmp_path):
    empty = tmp_path / "e.db"
    sqlite3.connect(empty).close()
    with pytest.raises(ch.HistoryError, match="not found"):
        execute(empty, tmp_path / "o1")
    narrow = make_db(tmp_path / "n.db", schema="CREATE TABLE candles (symbol TEXT, tf TEXT, "
                     "ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, "
                     "taker_buy REAL)")
    conn = sqlite3.connect(tmp_path / "s.db")
    conn.execute("CREATE TABLE candles (symbol TEXT, tf TEXT, ts INTEGER)")
    conn.close()
    with pytest.raises(ch.HistoryError, match="lacks columns"):
        execute(tmp_path / "s.db", tmp_path / "o2")
    with pytest.raises(ch.HistoryError, match="no stored 1h history"):
        execute(narrow, tmp_path / "o3", start="2019-01-03T00:00:00Z",
                stop="2019-01-03T06:00:00Z", resolve="2019-01-03T12:00:00Z")
    with pytest.raises(ch.HistoryError, match="max_symbols"):
        execute(narrow, tmp_path / "o4", max_symbols=5)
    with pytest.raises(ch.HistoryError, match="max_rows"):
        execute(narrow, tmp_path / "o5", max_rows=100)
    with pytest.raises(ch.HistoryError, match="max_rows"):
        execute(narrow, tmp_path / "o6", max_rows=ch.MAX_ROWS + 1)
    assert not any((tmp_path / f"o{i}").exists() for i in range(1, 7))


def test_cli_requires_explicit_paths_and_an_empty_output_dir(tmp_path, capsys):
    db = make_db(tmp_path / "c.db")
    with pytest.raises(SystemExit) as e:
        ch.main(["--timeframe", "1h", "--start", START, "--stop", STOP,
                 "--resolve-until", RESOLVE, "--output-dir", str(tmp_path / "o")])
    assert e.value.code == 2
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "keep.txt").write_text("mine")
    argv = ["--db", str(db), "--timeframe", "1h", "--start", START, "--stop", STOP,
            "--resolve-until", RESOLVE]
    assert ch.main(argv + ["--output-dir", str(busy)]) == 2
    assert "not empty" in capsys.readouterr().err
    assert sorted(p.name for p in busy.iterdir()) == ["keep.txt"]
    assert ch.main(argv + ["--output-dir", str(tmp_path), "--start", "2020-01-03"]) == 2
    out = tmp_path / "fresh"
    assert ch.main(argv + ["--output-dir", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(ch.OUTPUT_FILES)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["command"].startswith("./venv/bin/python -m scripts.cognition_history --db")
    assert manifest["files_sha256"]["trace.json"] == ch.hashlib.sha256(
        (out / "trace.json").read_bytes()).hexdigest()
    assert ch.main(argv + ["--output-dir", str(out)]) == 2          # now nonempty


def test_possible_aliases_are_disclosed_not_merged():
    assert ch.possible_aliases(["BTC/USDT:USDT", "BTCUSDT", "ETHUSDT"]) == [
        ["BTC/USDT:USDT", "BTCUSDT"]]
