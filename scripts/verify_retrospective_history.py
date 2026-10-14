"""Read-only discovery verification; never spends a ledger or held-out look.

Run with an explicitly authorized frozen archive and an evidence output path.
Only preserved discovery symbols and bars closed by its authenticated cut are
loaded. Held-out information comes exclusively from timestamp/count metadata.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(archive):
    implementation_paths = [ROOT / "trader" / path for path in (
        "data/feed.py", "data/market_provenance.py", "data/derivatives.py", "data/references.py",
        "research/evaluate.py", "research/job.py", "strategy/compile.py", "strategy/dsl.py",
        "strategy/features.py", "strategy/features_deriv.py", "strategy/features_xs.py",
        "strategy/quant_contract.py", "strategy/spec_evidence.py", "strategy/rolling.py",
        "strategy/vector_backtest.py", "strategy/portfolio_evidence.py")]
    implementation_before = {str(path.relative_to(ROOT)): sha256(path) for path in implementation_paths}
    from trader.data.feed import DataFeed
    from trader.research import evaluate, job, slices
    from trader.research.combo import Combination
    from trader.research.vocab import Part
    from trader.research.universe import NoExchange
    from trader.strategy.compile import compile_spec
    from trader.strategy.spec import StrategySpec
    from trader.strategy.vector_backtest import simulate
    archive = Path(archive).resolve()
    paths = {key: str(archive / (name + ".db")) for key, name in
             (("candles", "candles"), ("derivs", "derivs"))}
    ledger_path = archive / "luffy.db"
    before = {str(p): sha256(p) for p in [ledger_path, *map(Path, paths.values())]}
    recorded = {}
    for line in (archive.parent / "SHA256SUMS").read_text().splitlines():
        digest, relative = line.split(maxsplit=1)
        recorded[str((archive.parent / relative).resolve())] = digest
    assert all(recorded.get(path) == digest for path, digest in before.items()), "archive certification hash mismatch"
    print("Frozen archive identities hashed", flush=True)
    con = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    row = con.execute("SELECT cut_ms,counts,measured_at FROM research_slices WHERE tf=?", ("4h",)).fetchone()
    con.close()
    assert row is not None, "preserved discovery cut missing"
    cut, counts, measured_at = row
    counts = json.loads(counts)
    symbols = list(counts["discovery"])
    heldout = list(counts["heldout_a"])
    # Enforce boundaries at SQL execution, including retained receipt JSON.
    # SQLite's column authorizer cannot distinguish rows, so the connection
    # checks the parameterized predicate before authorizing price columns.
    connect = sqlite3.connect
    guarded = {"price_queries": 0, "metadata_queries": 0}
    class DiscoveryConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            query = " ".join(sql.lower().split())
            if "from candles" in query and query.startswith("select"):
                projection = query.split("from candles", 1)[0]
                if any(key in projection for key in ("open", "high", "low", "close", "volume", "taker_buy")):
                    assert parameters[0] in symbols, "protected market price query"
                    assert "ts<=?" in query and len(parameters) >= 4
                    from trader.core.types import TF_MS
                    assert parameters[3] <= cut - TF_MS[parameters[1]], "post-cut candle price query"
                    guarded["price_queries"] += 1
                else:
                    guarded["metadata_queries"] += 1
            if "record_json" in query and "market_revisions" in query:
                assert "event_ms<=?" in query, "unbounded receipt price query"
                assert parameters[0].startswith("candle:"), "undeclared source query"
                _, symbol, tf = parameters[0].split(":")
                from trader.core.types import TF_MS
                assert symbol in symbols and parameters[2] <= cut - TF_MS[tf]
                guarded["price_queries"] += 1
            return super().execute(sql, parameters)
    def protected_connect(*args, **kwargs):
        kwargs["factory"] = DiscoveryConnection
        return connect(*args, **kwargs)
    sqlite3.connect = protected_connect
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg["risk"]["real_funding"] = False  # existing, explicitly modeled cost scenario
    contract = {"dataset_id": "historical-archive-20261009", "cut_ms": int(cut),
                "discovery_cut_source": str(ledger_path),
                "discovery_cut_source_sha256": before[str(ledger_path)]}
    cfg["research"].update(retrospective=contract, paths=paths, referee=False, handoff=False)
    bundle = evaluate.load_bundle("4h", symbols, cfg, ("ohlcv",), heldout, paths)
    print("Bounded discovery bundle loaded", flush=True)
    assert len(bundle.frames) >= 16, bundle.dropped
    assert all(int(slices._ms(df).max()) + 14_400_000 <= cut for df in bundle.frames.values())
    assert not set(bundle.frames) & set(heldout)
    combo = Combination((Part("ev:donch20", "event", "ev:donch20",
                              "close > donchian_hi(20)", "close < donchian_lo(20)"),), "4h", "trail")
    payload = {"tf": "4h", "symbols": symbols, "heldout_symbols": heldout,
               "requires": ["ohlcv"], "cfg": cfg, "paths": paths,
               "combos": [combo.as_dict()], "min_symbols": cfg["research"]["min_discovery_symbols"],
               "draws": cfg["research"]["discovery_null_draws"], "seed": cfg["research"]["seed"]}
    first = job.evaluate_job(payload)
    print("Discovery evaluator completed", flush=True)
    second = job.evaluate_job(payload)
    print("Discovery evaluator reproduced", flush=True)
    assert first["done"] == second["done"] == 1
    assert first["results"] == second["results"], "discovery result not reproducible"
    assert first["results"][0]["verdict"] != "error", first["results"]
    assert first["results"][0]["trades"] > 0, first["results"]
    spec = StrategySpec.from_dict(json.loads((ROOT / "data/authored_specs/donchian_breakout_trail.json").read_text()))
    # Keep the authored specification's declared timeframe and calendar cut.
    compiled = compile_spec(spec)
    feed = DataFeed(exchange=NoExchange(), db_path=paths["candles"], retrospective=contract)
    df = feed.cached_ohlcv("BTC/USDT", spec.timeframe, limit=200000)
    assert df is not None and len(df) > 420
    frames = {spec.timeframe: df}
    lo, sh = compiled.entries(frames, symbol="BTC/USDT")
    risk = evaluate.spec_evidence.risk_for(cfg["risk"], spec.timeframe)
    result = simulate(lo, sh, df, spec.exit, risk)
    repeat = simulate(*compiled.entries(frames, symbol="BTC/USDT"), df, spec.exit, risk)
    assert dataclasses.asdict(result) == dataclasses.asdict(repeat)
    assert result.trades > 0, "compiled historical seed produced no trades"
    from trader.strategy.rolling import rolling_windows
    rolling = rolling_windows(compiled, {"BTC/USDT": df}, risk, spec.timeframe)
    rolling_repeat = rolling_windows(compiled, {"BTC/USDT": df}, risk, spec.timeframe)
    assert rolling.n_windows > 0 and dataclasses.asdict(rolling) == dataclasses.asdict(rolling_repeat)
    after = {p: sha256(p) for p in before}
    assert before == after, "frozen archive changed"
    implementation_after = {str(path.relative_to(ROOT)): sha256(path) for path in implementation_paths}
    assert implementation_before == implementation_after, "implementation changed during verification"
    sqlite3.connect = connect
    return {"dataset": contract, "preserved_cut_measured_at": measured_at,
            "discovery_symbols": sorted(bundle.frames),
            "discovery_bar_counts": slices.bar_counts(bundle.frames),
            "heldout_coverage_metadata": bundle.heldout_bars,
            "discovery_admission": first.get("admission"),
            "research_result_sha256": hashlib.sha256(json.dumps(first["results"], sort_keys=True).encode()).hexdigest(),
            "research_done": first["done"], "research_verdict": first["results"][0]["verdict"],
            "research_evidence": bundle.evidence,
            "strategy": {"spec_id": spec.id, "timeframe": spec.timeframe,
                         "cost_assumptions": {"real_funding": False, "funding_rate_8h": cfg["risk"].get("funding_rate_8h"),
                                              "fills": "existing OHLCV backtest modeled fills", "liquidity_evidence": False},
                         "compiler_identity": compiled.identity, "input_identity": df.attrs["input_identity"],
                         "bars": len(df), "result": dataclasses.asdict(result)},
            "rolling_evaluation": rolling.as_dict(),
            "research_portfolio_accounting": first["results"][0].get("portfolio"),
            "identical_repeat": True, "archive_hashes_before": before,
            "archive_hashes_after": after, "archive_certification_verified": True, "ledger_alpha_spent": False,
            "implementation_hashes_before": implementation_before,
            "implementation_hashes_after": implementation_after,
            "factory_activated": False, "protected_prices_loaded": False,
            "sql_execution_guard": guarded}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="explicit frozen databases directory")
    parser.add_argument("--output", required=True, help="essential JSON evidence path")
    args = parser.parse_args()
    report = verify(args.archive)
    Path(args.output).write_text(json.dumps(report, sort_keys=True, indent=2, default=str) + "\n")
    print(json.dumps({"symbols": len(report["discovery_symbols"]), "research_done": report["research_done"],
                      "backtest_trades": report["strategy"]["result"]["trades"], "reproducible": True}))
