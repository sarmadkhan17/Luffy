"""STR-02: the compiler/evaluator consume the EXACT STR-01 StrategyVersion.

Compiling grants nothing (no install, approval, capital or order); a missing or
unsupported input is surfaced, never zero-filled; identity changes with every
material dependency; current and replay evaluation share one implementation.
"""
import copy

import numpy as np
import pandas as pd
import pytest

from tests.market_receipt_fixtures import snapshot as Snapshot
from tests.test_compile import _funding_spec, _spec
from tests.test_strategy_factory_handoff import (  # noqa: F401
    T0, _install, _journal, _kernel, cfg)
from trader.engine.trade_provenance import spec_version
from trader.strategy import compile as C
from trader.strategy import dsl
from trader.strategy import factory_handoff as F
from trader.strategy.compile import compile_spec, compile_version
from tests.test_wrld05_normal_context import retained  # noqa: F401  (fixture)
from trader.strategy.spec import ExitSpec, StrategySpec


# ── fixtures ─────────────────────────────────────────────────────────────
def _src(h):
    return {"kind": "research_candidate", "hash": h}


@pytest.fixture
def installed(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = F.create_version(j, cfg, _src(h), at_ms=T0)
    _install(j, v["version_id"])
    return j, v["version_id"]


def _frame(tf="4h", n=600, seed=5):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n)))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq={"15m": "15min", "1h": "1h", "4h": "4h"}[tf], tz="UTC"),
        "open": close, "high": close * 1.004, "low": close * 0.996,
        "close": close, "volume": rng.uniform(50, 500, n)})


def _snap(frame, tf, **kw):
    return Snapshot(symbol="BTC/USDT", ts="", price=float(frame["close"].iloc[-1]),
                    dfs={tf: frame}, market_type="futures", **kw)


def _diag():
    seen = []
    return seen, lambda code, *a: seen.append(code)


# ── 1. exact version binding ─────────────────────────────────────────────
def test_compile_version_is_bound_to_the_exact_authenticated_version(installed):
    j, vid = installed
    c = compile_version(j, vid)
    rec = F.load_version(j, vid)
    assert c.version_id == vid
    assert c.spec_sha256 == spec_version(StrategySpec.from_dict(rec["spec"]))["spec_sha256"]
    assert c.spec.to_dict() == rec["spec"]
    assert (c.compiler_version, c.feature_version) == (C.COMPILER_VERSION, C.FEATURE_VERSION)
    assert c.identity == C.compile_identity(c.spec_sha256, C.COMPILER_VERSION,
                                            C.FEATURE_VERSION, C.feature_contract_sha256())


def test_compile_version_writes_nothing_and_grants_no_authority(installed):
    j, vid = installed
    tables = ("strategy_versions", "strategy_version_events", "strategy_version_installs",
              "strategy_validation_receipts", "strategies", "trades", "brain_events")
    names = {r["name"] for r in j.query("SELECT name FROM sqlite_master WHERE type='table'")}
    before = {t: j.query(f"SELECT * FROM {t}") for t in tables if t in names}
    state = F.state_of(j, vid)
    compile_version(j, vid)
    assert {t: j.query(f"SELECT * FROM {t}") for t in before} == before
    assert F.state_of(j, vid) == state
    assert F.live_entry_block(j, F.load_version(j, vid)["strategy_id"])  # still blocked


def test_wrong_or_missing_version_refused(installed):
    j, vid = installed
    with pytest.raises(F.HandoffRefused) as e:
        compile_version(j, "0" * 64)
    assert e.value.code == "version_missing"


@pytest.mark.parametrize("kw,code", [
    ({"spec_hash": "f" * 64}, "spec_hash_mismatch"),
    ({"compiler_version": "strategy-compiler.v0"}, "compiler_version_mismatch"),
    ({"feature_version": "strategy-features.v0"}, "feature_version_mismatch"),
])
def test_wrong_hash_or_compiler_or_feature_version_refused(installed, kw, code):
    j, vid = installed
    with pytest.raises(F.HandoffRefused) as e:
        compile_version(j, vid, **kw)
    assert e.value.code == code


def test_wrong_schema_and_legacy_version_unsupported(installed, monkeypatch):
    j, vid = installed
    real = F.load_version
    monkeypatch.setattr(F, "load_version", lambda *a: {**real(*a), "schema": "strategy-version.v0"})
    with pytest.raises(F.HandoffRefused) as e:
        compile_version(j, vid)
    assert e.value.code == "version_schema_unsupported"
    def legacy(*a):
        r = dict(real(*a))
        r.pop("lineage")
        return r
    monkeypatch.setattr(F, "load_version", legacy)
    with pytest.raises(F.HandoffRefused) as e:
        compile_version(j, vid)
    assert e.value.code == "legacy_version_unsupported"


def test_stale_lineage_retired_or_uninstalled_or_drifted_refused(installed, tmp_path, cfg):
    j, vid = installed
    spec_d = F.load_version(j, vid)["spec"]
    # installed row drifts from the frozen version
    drift = copy.deepcopy(spec_d)
    drift["name"] = spec_d["name"] + " x"
    j.upsert_spec(StrategySpec.from_dict(drift), state="paper", origin="research")
    with pytest.raises(F.HandoffRefused) as e:
        compile_version(j, vid)
    assert e.value.code == "installed_version_differs"
    j.upsert_spec(StrategySpec.from_dict(spec_d), state="paper", origin="research")
    compile_version(j, vid)
    F.retire_version(j, vid, F.RETIRED, reason_code="test", actor="strategy_governor", at_ms=T0 + 1)
    with pytest.raises(F.HandoffRefused) as e:
        compile_version(j, vid)
    assert e.value.code == "version_not_live"
    # a validated-but-never-installed version is not compilable on the normal path
    (tmp_path / "b").mkdir()
    j2, h2 = _journal(tmp_path / "b")
    v2 = F.create_version(j2, cfg, _src(h2), at_ms=T0)
    with pytest.raises(F.HandoffRefused) as e:
        compile_version(j2, v2["version_id"])
    assert e.value.code in ("install_missing", "installed_version_missing")


def test_kernel_population_compiles_only_the_exact_installed_version(installed):
    j, vid = installed
    k = _kernel(j)
    row = j.query("SELECT spec_json FROM strategies")[0]
    spec = StrategySpec.from_json(row["spec_json"])
    c = k._compile_population_spec(spec)
    assert c.version_id == vid
    # a row that differs from the version never compiles through the kernel
    spec.name += " drift"
    with pytest.raises(Exception):
        k._compile_population_spec(spec)
    # a versioned strategy without an install row fails closed
    with j._tx() as conn:
        conn.execute("DROP TRIGGER IF EXISTS strategy_version_installs_no_delete")
        conn.execute("DELETE FROM strategy_version_installs")
    with pytest.raises(Exception):
        k._compile_population_spec(StrategySpec.from_json(row["spec_json"]))


def test_unversioned_legacy_spec_keeps_the_plain_compile(tmp_path):
    from trader.core.journal import Journal
    c = _kernel(Journal(tmp_path / "l.db"))._compile_population_spec(_spec())
    assert c.version_id is None


# ── 2. identity changes with every material dependency ───────────────────
@pytest.mark.parametrize("change", [
    {"universe": {"include": ["BTC/USDT"]}},
    {"timeframe": "1h"},
    {"entry_long": "close > ema(50)"},
    {"direction": "both", "entry_short": "close < ema(20)"},
    {"filters": []},
    {"exit": ExitSpec(stop={"kind": "atr", "mult": 3.0})},
    {"entry_long": "funding > 0.005", "filters": []},                      # data requirement
    {"entry_long": 'world_observation("volume_anomaly", "intraday") > 1',  # WorldModel requirement
     "filters": []},
])
def test_material_spec_change_changes_compiled_identity(change):
    base = compile_spec(_spec())
    other = compile_spec(_spec(**change))
    assert other.identity != base.identity
    assert other.spec_sha256 != base.spec_sha256


def test_compiler_and_feature_versions_and_contract_change_identity(monkeypatch):
    base = compile_spec(_spec())
    monkeypatch.setattr(C, "COMPILER_VERSION", "strategy-compiler.v2")
    assert compile_spec(_spec()).identity != base.identity
    monkeypatch.setattr(C, "COMPILER_VERSION", base.compiler_version)
    monkeypatch.setattr(C, "FEATURE_VERSION", "strategy-features.v2")
    assert compile_spec(_spec()).identity != base.identity


def test_stale_compiled_object_refuses_after_feature_or_compiler_change(monkeypatch):
    c = compile_spec(_spec(entry_long="close > ema(2)", filters=[]))
    f = _frame("15m")
    snap = _snap(f, "15m")
    seen, diag = _diag()
    c.to_evaluator()(None, snap, diagnostic=diag)
    assert "compiled_feature_version_mismatch" not in seen
    for attr, val in (("COMPILER_VERSION", "strategy-compiler.v9"),
                      ("FEATURE_VERSION", "strategy-features.v9")):
        with monkeypatch.context() as m:
            m.setattr(C, attr, val)
            seen, diag = _diag()
            assert c.to_evaluator()(None, snap, diagnostic=diag) is None
            assert seen == ["compiled_feature_version_mismatch"]
            with pytest.raises(dsl.SpecError):
                c.entries({"15m": f})
    # feature contract (registry) change
    from trader.strategy import features
    with monkeypatch.context() as m:
        m.setitem(features.FEATURES, "str02_probe",
                  features.Feature("str02_probe", lambda ctx: None, (), None, ("ohlcv",)))
        with pytest.raises(dsl.SpecError, match="compiled_feature_version_mismatch"):
            c.entries({"15m": f})


# ── 3. missing / unsupported inputs surface; nothing defaults ────────────
def test_declared_dependencies_missing_are_surfaced_not_silent():
    f = _frame("15m")
    c = compile_spec(_funding_spec())
    seen, diag = _diag()
    assert c.to_evaluator()(None, _snap(f, "15m"), diagnostic=diag) is None
    assert seen == ["required_input_missing:funding"]
    r = compile_spec(_spec(entry_long='ref("spx", close) > 0', filters=[]))
    assert "ref:spx" in r.data_requires
    seen, diag = _diag()
    assert r.to_evaluator()(None, _snap(f, "15m"), diagnostic=diag) is None
    assert seen == ["required_input_missing:ref:spx"]


def test_required_frame_absent_is_surfaced():
    c = compile_spec(_spec())
    seen, diag = _diag()
    assert c.to_evaluator()(None, _snap(_frame("1h"), "1h"), diagnostic=diag) is None
    assert seen == ["missing_closed_timeframe"]
    with pytest.raises(dsl.SpecError, match="not in frames"):
        c.entries({"1h": _frame("1h")})


def test_unsupported_timeframe_and_dependency_refused_at_compile():
    with pytest.raises(dsl.SpecError):
        compile_spec(_spec(timeframe="7m"))
    with pytest.raises(dsl.SpecError):
        compile_spec(_spec(entry_long='world_observation("volume_anomaly", "fortnight") > 0', filters=[]))
    with pytest.raises(dsl.SpecError):
        compile_spec(_spec(entry_long="no_such_feature(3) > 0", filters=[]))


WORLD = 'world_observation("volume_anomaly", "intraday") > 0'


def test_world_dependency_unavailable_is_surfaced_and_never_loads_latest():
    from trader.world import WorldHistory
    from trader.world.context import WorldContext
    c = compile_spec(_spec(entry_long=WORLD, filters=[]))
    assert "world" in c.data_requires
    f = _frame("15m")
    # entries() without a cut or world has no unbounded/latest fallback
    with pytest.raises(dsl.SpecError, match="world_context_cut_required"):
        c.entries({"15m": f})
    snap = _snap(f, "15m")
    snap.world = WorldContext(WorldHistory(()), "SOURCE_UNAVAILABLE", 2_000_000_000_000)
    seen, diag = _diag()
    assert c.to_evaluator()(None, snap, diagnostic=diag) is None
    assert seen == ["required_input_missing:world:volume_anomaly:intraday"]


def test_failed_quality_condition_is_surfaced_not_a_silent_disappearance():
    f = _frame("15m")
    f["quality"] = "VALID"
    f.loc[f.index[-1], "quality"] = "INVALID"
    c = compile_spec(_spec(entry_long="close > 0", filters=[]))
    lo, _ = c.entries({"15m": f})
    assert lo[:-1].all() and not lo[-1]
    seen, diag = _diag()
    snap = _snap(f, "15m")        # provenance annotation re-derives quality
    snap.dfs["15m"].loc[snap.dfs["15m"].index[-1], "quality"] = "INVALID"
    assert c.to_evaluator()(None, snap, diagnostic=diag) is None
    assert seen == ["quality_condition_failed"]


# ── 4. read-only arrays / immutable inputs ───────────────────────────────
def test_readonly_arrays_are_evaluated_and_inputs_unmodified():
    f = _frame("15m")
    f["quality"] = "VALID"
    c = compile_spec(_spec(entry_long="close > ema(2)", filters=["adx(14) > 5"]))
    want = c.entries({"15m": f.copy(deep=True)})
    ro = f.copy(deep=True)
    for col in ("open", "high", "low", "close", "volume"):
        arr = ro[col].to_numpy()
        arr.flags.writeable = False
    before = ro.copy(deep=True)
    lo, sh = c.entries({"15m": ro})
    assert (lo == want[0]).all() and (sh == want[1]).all()
    pd.testing.assert_frame_equal(ro, before)
    # the returned arrays are the caller's to own, never views of the inputs
    assert lo.flags.writeable and sh.flags.writeable


def test_evaluation_does_not_mutate_snapshot_inputs():
    f = _frame("15m")
    snap = _snap(f, "15m")
    before = {k: v.copy(deep=True) for k, v in snap.dfs.items()}
    spec_before = copy.deepcopy(compile_spec(_spec()).spec.to_dict())
    c = compile_spec(_spec(entry_long="close > ema(2)", filters=[]))
    c.to_evaluator()(None, snap)
    for k, v in snap.dfs.items():
        pd.testing.assert_frame_equal(v, before[k])
    assert compile_spec(_spec()).spec.to_dict() == spec_before


# ── 5. current / historical parity, no leakage, replay determinism ───────
CUTS = (260, 300, 377, 450, 599)


def _versioned(installed):
    j, vid = installed
    return j, vid, compile_version(j, vid)


def test_no_future_leakage_entries_at_a_cut_equal_full_series(installed):
    _, _, c = _versioned(installed)
    f = _frame("4h")
    tf = c.spec.timeframe
    full_lo, full_sh = c.entries({tf: f})
    for i in CUTS:
        lo, sh = c.entries({tf: f.iloc[: i + 1].reset_index(drop=True)})
        assert lo[i] == full_lo[i] and sh[i] == full_sh[i], i


def test_current_evaluator_equals_historical_entries_at_every_cut(installed):
    _, _, c = _versioned(installed)
    f = _frame("4h")
    tf = c.spec.timeframe
    ev = c.to_evaluator()
    lo_full, sh_full = c.entries({tf: f})
    for i in CUTS:
        sub = f.iloc[: i + 1].reset_index(drop=True)
        sig = ev(None, _snap(sub, tf))
        expect = "BUY" if lo_full[i] else "SELL" if sh_full[i] else None
        assert (sig.action.value.upper() if sig else None) == expect, i


def test_parity_on_signalling_bars_and_cuts_with_version_stamp():
    c = compile_spec(_spec(entry_long="close > ema(20)", direction="both",
                           entry_short="close < ema(20)", filters=["adx(14) > 15"]))
    c.version_id = "v-test"
    f = _frame("15m")
    ev = c.to_evaluator()
    lo, sh = c.entries({"15m": f})
    bars = [i for i in range(260, 599) if lo[i] or sh[i]]
    assert len(bars) > 20 and any(lo[i] for i in bars) and any(sh[i] for i in bars)
    for i in bars[::7]:
        sub = f.iloc[: i + 1].reset_index(drop=True)
        sig = ev(None, _snap(sub, "15m"))
        assert sig is not None and sig.action.value == ("BUY" if lo[i] else "SELL"), i
        assert sig.params["version_id"] == "v-test"
        assert sig.params["compile_identity"] == c.identity
        # the same bar evaluated twice (restart/replay) is bit-identical
        again = ev(None, _snap(sub.copy(deep=True), "15m"))
        strip = lambda p: {k: v for k, v in p.items() if k != "market_provenance"}
        assert strip(again.params) == strip(sig.params)   # receipt ids are per-ingest
    for i in range(260, 599, 31):
        assert (c.entries({"15m": f.iloc[: i + 1].reset_index(drop=True)})[0][i], ) == (lo[i],)


def test_restart_replay_is_deterministic_and_identical(installed):
    j, vid, c1 = _versioned(installed)
    c2 = compile_version(j, vid)                 # a "restarted" process recompiles
    f = _frame("4h")
    tf = c1.spec.timeframe
    assert (c1.identity, c1.spec_sha256) == (c2.identity, c2.spec_sha256)
    a = c1.entries({tf: f})
    b = c2.entries({tf: f.copy(deep=True)})
    assert (a[0] == b[0]).all() and (a[1] == b[1]).all()
    snap = _snap(f.iloc[:450].reset_index(drop=True), tf)
    s1, s2 = c1.to_evaluator()(None, snap), c2.to_evaluator()(None, snap)
    assert (s1 is None) == (s2 is None)
    if s1:
        assert {k: v for k, v in s1.params.items() if k != 'market_provenance'} == \
            {k: v for k, v in s2.params.items() if k != 'market_provenance'}


def test_exit_logic_executes_with_the_same_context():
    f = _frame("15m")
    c = compile_spec(_spec(entry_long="close > ema(2)", filters=[],
                           exit=ExitSpec(signal_exit="close < ema(2)")))
    x = c.exit_signal({"15m": f})
    assert x is not None and x.dtype == bool and len(x) == len(f) and x.any()
    assert compile_spec(_spec()).exit_signal({"15m": f}) is None   # none declared -> None, not zeros


def test_filters_and_universe_are_part_of_what_runs():
    f = _frame("15m")
    on = compile_spec(_spec(entry_long="close > ema(2)", filters=[])).entries({"15m": f})[0]
    off = compile_spec(_spec(entry_long="close > ema(2)", filters=["close < 0"])).entries({"15m": f})[0]
    assert on.any() and not off.any()


# ── 6. exact dependency validation (no "derivs is non-empty" proxy) ──────
from tests.test_compile import _funding_obs  # noqa: E402


def _obs(frame, value):
    return _funding_obs(frame, value)


def _oi_spec(**kw):
    return _spec(id="oz", direction="long", filters=[], entry_long="oi > 0",
                 entry_short="", **kw)


def _run(c, f, derivs=None, market=None, world=None):
    seen, diag = _diag()
    snap = _snap(f, "15m", **({"derivs": derivs} if derivs is not None else {}),
                 **({"market": market} if market is not None else {}))
    if world is not None:
        snap.world = world
    return c.to_evaluator()(None, snap, diagnostic=diag), seen


def test_concrete_dependencies_are_derived_from_the_expressions():
    c = compile_spec(_spec(entry_long='funding > 0 and oi > 0 and ref("spx", close) > 0 '
                                      'and ' + WORLD, filters=[]))
    assert c.deps == {"derivs": ("funding", "oi"), "refs": ("spx",),
                      "world": (("volume_anomaly", "intraday"),)}


def test_funding_required_oi_only_is_refused_explicitly():
    f = _frame("15m")
    sig, seen = _run(compile_spec(_funding_spec()), f, derivs={"oi": _obs(f, 1.0)})
    assert sig is None and seen == ["required_input_missing:funding"]


def test_oi_required_funding_only_is_refused_explicitly():
    f = _frame("15m")
    sig, seen = _run(compile_spec(_oi_spec()), f, derivs={"funding": _obs(f, 0.01)})
    assert sig is None and seen == ["required_input_missing:oi"]


def test_partial_multi_dependency_is_refused_and_names_the_absent_one():
    f = _frame("15m")
    c = compile_spec(_spec(direction="long", filters=[], entry_short="",
                           entry_long="funding > 0.005 and oi > 0"))
    assert c.deps["derivs"] == ("funding", "oi")
    sig, seen = _run(c, f, derivs={"funding": _obs(f, 0.01)})
    assert sig is None and seen == ["required_input_missing:oi"]
    sig, seen = _run(c, f, derivs={"oi": _obs(f, 5.0)})
    assert sig is None and seen == ["required_input_missing:funding"]


def test_exact_dependencies_present_evaluate_normally():
    f = _frame("15m")
    c = compile_spec(_spec(direction="long", filters=[], entry_short="",
                           entry_long="funding > 0.005 and oi > 0"))
    sig, seen = _run(c, f, derivs={"funding": _obs(f, 0.01), "oi": _obs(f, 5.0)})
    assert seen == [] and sig is not None and sig.action.value == "BUY"
    # an extra, unrelated derivative never substitutes for a required one
    sig, seen = _run(compile_spec(_funding_spec()), f,
                     derivs={"funding": _obs(f, 0.01), "oi": _obs(f, 1.0)})
    assert seen == [] and sig is not None


def test_derivative_with_no_observation_at_the_cut_is_refused_not_nan_silent():
    f = _frame("15m")
    late = _obs(f, 0.01)
    late["ts"] = late["ts"] + pd.Timedelta(days=365)       # all observations are after the cut
    sig, seen = _run(compile_spec(_funding_spec()), f, derivs={"funding": late})
    assert sig is None and seen == ["required_input_missing:funding"]
    empty = _obs(f, 0.01).iloc[:0]
    sig, seen = _run(compile_spec(_funding_spec()), f, derivs={"funding": empty})
    assert sig is None and seen == ["required_input_missing:funding"]


def test_required_ref_absent_while_other_refs_exist_is_refused():
    f = _frame("15m")
    c = compile_spec(_spec(entry_long='ref("spx", close) > 0', filters=[]))
    other = pd.DataFrame({"ts": f["ts"], "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": 1.0, "volume": 1.0})
    sig, seen = _run(c, f, market={"vix": other})
    assert sig is None and seen == ["required_input_missing:ref:spx"]


def test_exact_world_dependency_absent_while_other_world_data_exists_is_refused(retained):
    from tests.test_wrld05_normal_context import EXPR, NOW, SYMBOL, query
    from trader.world.context import load_context
    path, df = retained
    context = load_context(path)
    assert query(context).quality.value == "VALID"      # world data DOES exist
    ok = compile_spec(_spec(timeframe="4h", entry_long=EXPR + " > 1", entry_short="", filters=[]))
    assert ok.entries_detail({"4h": df}, symbol=SYMBOL, world=context)[2]["missing"] == []
    for expr, code in ((EXPR.replace("volume_anomaly", "no_such_kind") + " > 1",
                        "world:no_such_kind:intraday"),
                       (EXPR.replace("intraday", "swing") + " > 1",
                        "world:volume_anomaly:swing")):
        c = compile_spec(_spec(timeframe="4h", entry_long=expr, entry_short="", filters=[]))
        assert c.entries_detail({"4h": df}, symbol=SYMBOL, world=context)[2]["missing"] == [code]
        snap = Snapshot(symbol=SYMBOL, ts=pd.Timestamp(NOW, unit="ms", tz="UTC").isoformat(),
                        price=float(df.close.iloc[-1]), dfs={"4h": df})
        snap.world = context
        seen, diag = _diag()
        assert c.to_evaluator()(None, snap, diagnostic=diag) is None
        assert seen == ["required_input_missing:" + code]


def test_dependency_check_is_the_same_for_live_replay_and_vectorized_paths():
    f = _frame("15m")
    c = compile_spec(_spec(direction="long", filters=[], entry_short="",
                           entry_long="funding > 0.005 and oi > 0"))
    derivs = {"funding": _obs(f, 0.01)}
    replay = f.copy(deep=True)
    replay.attrs["read_mode"] = "replay"
    codes = []
    for frame in (f, replay):
        _, _, detail = c.entries_detail({"15m": frame}, derivs=derivs)
        codes.append(detail["missing"])
    sig, seen = _run(c, f, derivs=derivs)
    assert codes[0] == codes[1] == ["oi"]
    assert seen == ["required_input_missing:oi"] and sig is None
    # a historical cut gives the same answer as the full series
    _, _, early = c.entries_detail({"15m": f.iloc[:300].reset_index(drop=True)}, derivs=derivs)
    assert early["missing"] == ["oi"]
