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


def verify_dependency_features(report_path):
    """Check actual source-backed features; preserve the original discovery window."""
    import numpy as np
    from trader.data.feed import DataFeed
    from trader.data.derivatives import DerivFeed
    from trader.data.references import RefStore
    from trader.research.universe import NoExchange
    from trader.strategy.features import FeatureCtx
    from trader.strategy import dsl
    from trader.strategy.compile import compile_spec
    from trader.strategy.spec import StrategySpec, ExitSpec
    from trader.strategy.vector_backtest import funding_for
    from trader.strategy.spec_evidence import MIN_COVERAGE
    original = json.loads(Path(report_path).read_text())
    contract = dict(original["dataset"])
    archive = Path(contract["discovery_cut_source"]).parent
    paths = {"candles": str(archive / "candles.db"), "derivs": str(archive / "derivs.db")}
    contract["paths"] = paths
    before = {path: sha256(path) for path in paths.values()}
    assert all(original["archive_hashes_after"][path] == digest for path, digest in before.items())
    source_paths = [ROOT / path for path in original["implementation_hashes_after"]]
    source_before = {str(path.relative_to(ROOT)): sha256(path) for path in source_paths}
    assert source_before == original["implementation_hashes_after"]
    connect = sqlite3.connect
    counts = {"bounded_candle_queries": 0, "bounded_dependency_queries": 0}
    cut = contract["cut_ms"]
    allowed = {"derivative:BTC/USDT:funding": cut, "derivative:BTC/USDT:basis": cut,
               "reference:stables": cut - 86_400_000}
    class FeatureConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            query = " ".join(sql.lower().split())
            if "from candles" in query and query.startswith("select"):
                projection = query.split("from candles", 1)[0]
                if any(column in projection for column in ("open", "high", "low", "close", "volume")):
                    assert parameters[0] == "BTC/USDT" and parameters[1] == "4h"
                    assert "ts<=?" in query and parameters[3] <= cut - 14_400_000
                    counts["bounded_candle_queries"] += 1
            if "record_json" in query and "market_revisions" in query:
                key = parameters[0]
                bound = cut - 14_400_000 if key == "candle:BTC/USDT:4h" else allowed.get(key)
                assert bound is not None and "event_ms<=?" in query and parameters[2] <= bound
                counts["bounded_dependency_queries"] += 1
            return super().execute(sql, parameters)
    def protected_connect(*args, **kwargs):
        kwargs["factory"] = FeatureConnection
        return connect(*args, **kwargs)
    sqlite3.connect = protected_connect
    try:
        feed = DataFeed(exchange=NoExchange(), db_path=paths["candles"], retrospective=contract)
        full = feed.cached_ohlcv("BTC/USDT", "4h", limit=200000)
        assert full is not None
        full_refusal = None
        try:
            funding_for("BTC/USDT", full, {"real_funding": True})
        except ValueError as exc:
            full_refusal = str(exc)
        assert full_refusal is not None, "full-span measured funding unexpectedly met the required coverage"
        available = original["dependency_data_results"]
        first = max(available[key]["event_first_ms"] for key in
                    ("derivative:funding", "derivative:basis", "reference:stables"))
        step = 14_400_000
        start = ((first + step - 1) // step) * step
        narrowed = dict(contract, start_ms=start)
        narrow_feed = DataFeed(exchange=NoExchange(), db_path=paths["candles"], retrospective=narrowed)
        df = narrow_feed.cached_ohlcv("BTC/USDT", "4h", limit=200000)
        derivatives = DerivFeed(db_path=paths["derivs"], retrospective=narrowed)
        references = RefStore(paths["candles"], retrospective=narrowed)
        derivs = {series: derivatives.load("BTC/USDT", series) for series in ("funding", "basis")}
        market = {"stables": references.load("stables")}
        assert all((frame.observed_at_ms > cut).all() for frame in [*derivs.values(), *market.values()])
        ctx = FeatureCtx(frames={"4h": df}, tf="4h", derivs=derivs, market=market, symbol="BTC/USDT")
        features = {}
        for name, expression in (("funding", "funding_z(360)"), ("basis", "basis_z(90)"),
                                 ("stables", 'ref("stables", close)')):
            values = dsl.evaluate(dsl.parse(expression), ctx).to_numpy(float)
            repeat = dsl.evaluate(dsl.parse(expression), ctx).to_numpy(float)
            assert np.isfinite(values).sum() > 0 and np.array_equal(values, repeat, equal_nan=True)
            spec = StrategySpec(id="dependency_probe_" + name, name="Historical Dependency Probe",
                thesis="Verification-only expression proving the selected historical source can supply a feature; no predictive edge or strategy admission is claimed.",
                invalidation="This verification confers no strategy or trading authority.",
                provenance={"source_kind": "verification"}, universe={"include": ["BTC/USDT"], "exclude": []},
                timeframe="4h", direction="both", entry_long=expression + " > 0",
                entry_short=expression + " < 0", filters=[], exit=ExitSpec(), regime_filter=[], markets=["futures"])
            compiled = compile_spec(spec)
            lo, sh = compiled.entries({"4h": df}, derivs=derivs, market=market, symbol="BTC/USDT")
            features[name] = {"expression": expression, "finite_values": int(np.isfinite(values).sum()),
                              "bars": len(df), "compiler_identity": compiled.identity,
                              "compiled_entry_masks": int((lo | sh).sum()),
                              "value_sha256": hashlib.sha256(values.tobytes()).hexdigest()}
        funding = funding_for("BTC/USDT", df, {"real_funding": True})
        assert np.isfinite(funding).mean() >= MIN_COVERAGE
        assert np.array_equal(funding, funding_for("BTC/USDT", df, {"real_funding": True}), equal_nan=True)
        after = {path: sha256(path) for path in before}
        source_after = {str(path.relative_to(ROOT)): sha256(path) for path in source_paths}
        assert before == after and source_before == source_after
        return {"dataset_id": contract["dataset_id"], "cut_ms": cut, "original_discovery_window_unchanged": True,
                "full_span": {"bars": len(full), "input_identity": full.attrs["input_identity"],
                              "real_funding_status": "REFUSED", "reason": full_refusal},
                "feature_window": {"start_ms": start, "bars": len(df), "input_identity": df.attrs["input_identity"],
                                   "selection": "First 4h boundary at or after all three declared sources' first retained observations"},
                "features": features, "measured_funding_finite_fraction": float(np.isfinite(funding).mean()),
                "source_inputs": {**{key: value.attrs["input_identity"] for key, value in derivs.items()},
                                  "stables": market["stables"].attrs["input_identity"]},
                "late_acquisition_remains_late": True, "evidence_scope": "retrospective_observation",
                "factory_validated": False, "archive_hashes_before": before, "archive_hashes_after": after,
                "implementation_hashes_before": source_before, "implementation_hashes_after": source_after,
                "sql_guard": counts}
    finally:
        sqlite3.connect = connect


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
    from trader.data.derivatives import DerivFeed
    from trader.data.references import RefStore, REFS
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
    guarded = {"price_queries": 0, "metadata_queries": 0, "dependency_queries": 0}
    derivative_series = ("funding", "oi", "basis", "ls_ratio", "ls_account_ratio")
    allowed_dependencies = {"derivative:BTC/USDT:" + series: cut for series in derivative_series}
    allowed_dependencies.update({"reference:" + key: cut - ref.close_after_ms for key, ref in REFS.items()})
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
                if parameters[0].startswith("candle:"):
                    _, symbol, tf = parameters[0].split(":")
                    from trader.core.types import TF_MS
                    assert symbol in symbols and parameters[2] <= cut - TF_MS[tf]
                    guarded["price_queries"] += 1
                else:
                    assert parameters[0] in allowed_dependencies, "undeclared dependency source query"
                    assert parameters[2] <= allowed_dependencies[parameters[0]], "post-cut dependency query"
                    guarded["dependency_queries"] += 1
            return super().execute(sql, parameters)
    def protected_connect(*args, **kwargs):
        kwargs["factory"] = DiscoveryConnection
        return connect(*args, **kwargs)
    sqlite3.connect = protected_connect
    try:
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
        dependency_results = {}
        dependency_contract = dict(contract, paths=paths)
        derivatives = DerivFeed(db_path=paths["derivs"], retrospective=dependency_contract)
        references = RefStore(paths["candles"], retrospective=dependency_contract)
        def describe_dependency(name, source_path, loader):
            try:
                frame = loader()
            except ValueError as exc:
                return {"status": "REFUSED", "requirement": name,
                        "source_path": source_path, "dataset_id": contract["dataset_id"],
                        "reason": str(exc)}
            assert frame is not None and len(frame)
            assert int(frame.event_time_ms.max()) <= cut
            return {"status": "AVAILABLE", "requirement": name, "source_path": source_path,
                    "dataset_id": frame.attrs["dataset_id"], "input_identity": frame.attrs["input_identity"],
                    "rows": len(frame), "event_first_ms": int(frame.event_time_ms.min()),
                    "event_last_ms": int(frame.event_time_ms.max()),
                    "observed_first_ms": int(frame.observed_at_ms.min()),
                    "observed_last_ms": int(frame.observed_at_ms.max()),
                    "late_acquired_rows": int((frame.observed_at_ms > cut).sum()),
                    "sources": sorted(frame.source.unique()),
                    "instrument_ids": sorted(frame.instrument_id.unique()),
                    "evidence_scope": "retrospective_observation"}
        for series in derivative_series:
            dependency_results["derivative:" + series] = describe_dependency(
                "BTC/USDT:" + series, paths["derivs"], lambda series=series: derivatives.load("BTC/USDT", series))
        for key in sorted(REFS):
            dependency_results["reference:" + key] = describe_dependency(
                "ref:" + key, paths["candles"], lambda key=key: references.load(key))
        print("Declared frozen dependencies probed", flush=True)
        after = {p: sha256(p) for p in before}
        assert before == after, "frozen archive changed"
        implementation_after = {str(path.relative_to(ROOT)): sha256(path) for path in implementation_paths}
        assert implementation_before == implementation_after, "implementation changed during verification"
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
                "dependency_data_results": dependency_results,
                "identical_repeat": True, "archive_hashes_before": before,
                "archive_hashes_after": after, "archive_certification_verified": True, "ledger_alpha_spent": False,
                "implementation_hashes_before": implementation_before,
                "implementation_hashes_after": implementation_after,
                "factory_activated": False, "protected_prices_loaded": False,
                "sql_execution_guard": guarded}
    finally:
        sqlite3.connect = connect


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="explicit frozen databases directory")
    parser.add_argument("--output", required=True, help="essential JSON evidence path")
    parser.add_argument("--dependency-features-from", help="Existing successful main report; verify actual source-backed features only")
    args = parser.parse_args()
    report = verify_dependency_features(args.dependency_features_from) if args.dependency_features_from else verify(args.archive)
    Path(args.output).write_text(json.dumps(report, sort_keys=True, indent=2, default=str) + "\n")
    if args.dependency_features_from:
        print(json.dumps({"actual_features": sorted(report["features"]), "full_span_real_funding": "REFUSED",
                          "narrow_window_bars": report["feature_window"]["bars"]}))
    else:
        print(json.dumps({"symbols": len(report["discovery_symbols"]), "research_done": report["research_done"],
                          "backtest_trades": report["strategy"]["result"]["trades"], "reproducible": True}))
