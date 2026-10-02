"""SDD-STAGE-3-OPPORTUNITY-CONTEXT-V1.

One immutable, versioned Opportunity Context binds existing persisted
evidence for one candidate. References only: no score, no authority.
"""
import ast
import json
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest

from trader.cognition import investigation as I
from trader.cognition import opportunity_context as oc
from trader.cognition.opportunity_context import OpportunityContext, OpportunityContextRefused, build
from trader.core.journal import Journal
from trader.core.types import Action, StrategySignal
from trader.observability import investigation as C
from trader.observability.selection_persistence import load_selection, record_selection
from trader.world import HierarchyNode, Scope, ScopeLevel, WorldModel
from trader.world.replay import WorldModelRecord
from tests.test_attention_selection_persistence import SNAP, _sel
from tests.test_market_investigation import prefix  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "trader/cognition/opportunity_context.py"


def _signal(symbol, close_ms, action="BUY", spec_id="s1", fp="f" * 64, **params):
    p = {"spec_id": spec_id, "spec_fingerprint": fp, "signal_timeframe": "4h",
         "signal_bar_close_ms": close_ms}
    p.update(params)
    return StrategySignal(f"spec:{spec_id}", spec_id, symbol, Action(action), .5, "r", p)


@pytest.fixture
def evidence(prefix):  # noqa: F811
    source, snapshot, inv = prefix
    scan = source[0]
    state = C.ledger_state([], 0, 0)
    allocation = C.allocate(snapshot, state, C.digest(scan), scan["as_of_ms"])
    update = I.advance(inv, I.measure(inv, (), inv.registered_ms, inv.registered_ms))
    symbol = inv.state.symbol
    cut = max(inv.registered_ms, update.observed_ms)
    return dict(scan=scan, allocation=allocation, inv=inv, update=update,
                symbol=symbol, cut=cut)


def _full(e, **over):
    kw = dict(as_of_ms=e["cut"], symbol=e["symbol"], attention_scan=e["scan"],
              allocation=e["allocation"], investigation=e["inv"],
              investigation_update=e["update"],
              signals=[_signal(e["symbol"], e["scan"]["as_of_ms"])])
    kw.update(over)
    return build(**kw)


# ── contract ────────────────────────────────────────────────────────────

def test_binds_existing_evidence_with_explicit_status(evidence):
    ctx = _full(evidence)
    d = ctx.to_dict()
    assert d["schema_version"] == "opportunity-context.v1"
    assert d["authority"] == "NONE"
    assert "SIGNAL_OCCURRENCE_IS_NOT_OPPORTUNITY_EPISODE" in d["boundaries"]
    att = d["attention"]
    assert att["status"] == "AVAILABLE"
    assert att["scan_id"] == evidence["scan"]["scan_id"]
    assert att["config_id"] == evidence["scan"]["config_id"]
    assert att["candidate"]["selection_reason"] == "selected" and att["candidate"]["rank"] >= 1
    assert att["allocation"]["decision_id"] == evidence["allocation"]["decision_id"]
    assert att["allocation"]["candidate_allocated"] is True
    inv = d["investigation"]
    assert inv["investigation_id"] == evidence["inv"].investigation_id
    assert inv["episode_id"] == evidence["inv"].episode_id
    assert inv["latest_update"]["event_id"] == evidence["update"].event_id
    occ = d["signal_occurrences"]["items"]
    assert len(occ) == 1 and occ[0]["status"] == "AVAILABLE"
    assert occ[0]["key"]["signal_bar_close_ms"] == evidence["scan"]["as_of_ms"]
    # absent sources are UNKNOWN with a reason, never inferred
    assert d["world_model"] == {"status": "UNKNOWN", "reason": "not_supplied"}
    assert d["registry_selection"] == {"status": "UNKNOWN", "reason": "not_supplied"}
    assert d["instrument"]["status"] == "UNKNOWN"
    assert d["instrument"]["reason"] == "canonical_instrument_id_not_supplied"
    assert ctx.context_id.startswith("opportunity-context.v1:")


def test_no_score_or_salience_is_added_or_copied(evidence):
    text = _full(evidence).canonical_json
    for word in ("salience", "components", "probability", "expected", "score",
                 "usefulness", "direction", "size", "leverage", "order"):
        assert f'"{word}' not in text, word


def test_all_unknown_when_nothing_supplied():
    d = build(as_of_ms=10, symbol="BTC/USDT:USDT").to_dict()
    for section in ("attention", "registry_selection", "investigation", "world_model",
                    "signal_occurrences", "instrument"):
        assert d[section]["status"] == "UNKNOWN" and d[section]["reason"], section
    assert d["instrument"]["symbol_key"] == "BTCUSDT"


def test_candidate_missing_from_scan_is_unknown(evidence):
    other = "ZZZ/USDT:USDT"
    d = build(as_of_ms=evidence["cut"], symbol=other, attention_scan=evidence["scan"]).to_dict()
    assert d["attention"]["candidate"] == {"status": "UNKNOWN", "reason": "candidate_not_in_scan"}


def test_is_immutable(evidence):
    ctx = _full(evidence)
    with pytest.raises(FrozenInstanceError):
        ctx.context_id = "x"


# ── determinism / replay ────────────────────────────────────────────────

def test_deterministic_and_order_independent(evidence):
    s = evidence["symbol"]
    a, b = _signal(s, 100, spec_id="a"), _signal(s, 200, spec_id="b")
    one = _full(evidence, signals=[a, b, a])
    two = _full(evidence, signals=[b, a])
    assert one.context_id == two.context_id and one.canonical_json == two.canonical_json


def test_occurrences_are_listed_not_merged_or_counted(evidence):
    s = evidence["symbol"]
    d = _full(evidence, signals=[_signal(s, 100), _signal(s, 100 + I.TF)]).to_dict()
    items = d["signal_occurrences"]["items"]
    assert [o["key"]["signal_bar_close_ms"] for o in items] == [100, 100 + I.TF]
    assert "count" not in json.dumps(d["signal_occurrences"])


def test_replay_from_persisted_forms_reproduces_context(evidence, tmp_path):
    """Round-trip every input through its persisted form and rebuild."""
    e = evidence
    j = Journal(tmp_path / "luffy.db")
    sel = _sel()
    record_selection(j, sel, selection_id=sel.selection_id, recorded_at_ms=sel.cycle_as_of_ms)
    sig = _signal(sel.selected_symbol, 100)
    live = build(as_of_ms=e["cut"] + sel.cycle_as_of_ms, symbol=sel.selected_symbol,
                 registry_selection=sel, signals=[sig])
    signals_json = json.dumps([{"symbol": sig.symbol, "action": sig.action.value,
                                "params": sig.params}])
    replay = build(as_of_ms=e["cut"] + sel.cycle_as_of_ms, symbol=sel.selected_symbol,
                   registry_selection=load_selection(j, sel.selection_id),
                   signals=json.loads(signals_json))
    assert replay.context_id == live.context_id
    assert live.to_dict()["instrument"]["canonical_id"] == sel.selected_id
    assert live.to_dict()["instrument"]["source"] == "registry_selection"

    # investigation case/update and scan payloads as stored in their ledgers
    inv_payload = json.loads(I.encode(asdict(e["inv"])))
    upd_payload = json.loads(I.encode(asdict(e["update"])))
    scan_payload = json.loads(json.dumps(e["scan"]))
    rebuilt = _full(e, attention_scan=scan_payload, allocation=json.loads(I.encode(e["allocation"])),
                    investigation=I.investigation_from_dict(inv_payload),
                    investigation_update=I.update_from_dict(upd_payload))
    assert rebuilt.context_id == _full(e).context_id


def test_from_json_verifies_identity(evidence):
    ctx = _full(evidence)
    assert OpportunityContext.from_json(ctx.canonical_json) == ctx
    tampered = ctx.to_dict()
    tampered["attention"]["candidate"]["rank"] = 99
    with pytest.raises(OpportunityContextRefused) as err:
        OpportunityContext.from_json(oc.canonical(tampered))
    assert err.value.reason == "corrupt_evidence"


def test_world_model_reference_only_when_supplied():
    model = WorldModel(200, [HierarchyNode(Scope(ScopeLevel.GLOBAL, "world"))])
    record = WorldModelRecord.from_model(model)
    d = build(as_of_ms=300, symbol="BTCUSDT", world_model=record).to_dict()["world_model"]
    assert d == {"status": "AVAILABLE", "reason": None, "as_of_ms": 200,
                 "model_id": model.model_id, "record_id": record.record_id}
    with pytest.raises(OpportunityContextRefused) as err:
        build(as_of_ms=199, symbol="BTCUSDT", world_model=model)
    assert err.value.reason == "future_evidence"


# ── fail-closed binding ────────────────────────────────────────────────

def test_legacy_signal_without_occurrence_identity_stays_unknown():
    legacy = StrategySignal("lib", "lib", "BTC/USDT", Action.BUY, .5, "r", {})
    items = build(as_of_ms=10, symbol="BTCUSDT", signals=[legacy]).to_dict()["signal_occurrences"]["items"]
    assert items == [{"status": "UNKNOWN", "reason": "no_closed_bar_identity", "key": None,
                      "occurrence_schema": "strategy-signal-occurrence.v1",
                      "spec_id": None, "action": "BUY"}]


@pytest.mark.parametrize("case,reason", [
    ("signal_other_symbol", "instrument_mismatch"),
    ("signal_future_bar", "future_evidence"),
    ("scan_future", "future_evidence"),
    ("investigation_other_symbol", "instrument_mismatch"),
    ("allocation_other_scan", "source_mismatch"),
    ("allocation_corrupt", "corrupt_evidence"),
    ("update_without_investigation", "invalid_input"),
    ("bad_instrument_id", "invalid_input"),
    ("instrument_id_other_symbol", "instrument_mismatch"),
])
def test_refuses_evidence_it_cannot_bind_truthfully(evidence, case, reason):
    e = evidence
    kw = {}
    if case == "signal_other_symbol":
        kw["signals"] = [_signal("QQQ/USDT:USDT", 100)]
    elif case == "signal_future_bar":
        kw["signals"] = [_signal(e["symbol"], e["cut"] + 1)]
    elif case == "scan_future":
        kw["as_of_ms"] = e["scan"]["as_of_ms"] - 1
        kw["investigation"] = kw["investigation_update"] = None
        kw["allocation"] = None
        kw["signals"] = ()
    elif case == "investigation_other_symbol":
        kw["symbol"] = "QQQUSDT"
        kw["attention_scan"] = kw["allocation"] = None
        kw["signals"] = ()
    elif case == "allocation_other_scan":
        kw["allocation"] = dict(e["allocation"], source_scan_id="other")
        body = {k: v for k, v in kw["allocation"].items() if k not in ("decision_id", "reused")}
        kw["allocation"]["decision_id"] = oc._sha(body)
    elif case == "allocation_corrupt":
        kw["allocation"] = dict(e["allocation"], selected=[])
    elif case == "update_without_investigation":
        kw["investigation"] = None
    elif case == "bad_instrument_id":
        kw["instrument_id"] = "BTCUSDT"
    elif case == "instrument_id_other_symbol":
        kw["instrument_id"] = "binance_usdm:futures:QQQUSDT"
    with pytest.raises(OpportunityContextRefused) as err:
        _full(e, **kw)
    assert err.value.reason == reason


def test_registry_selection_for_other_instrument_is_refused():
    sel = _sel()
    other = next(r for r in SNAP.records if r.instrument_id.value != sel.selected_id)
    with pytest.raises(OpportunityContextRefused) as err:
        build(as_of_ms=sel.cycle_as_of_ms, symbol=other.instrument_id.venue_symbol,
              registry_selection=sel)
    assert err.value.reason == "instrument_mismatch"


# ── boundaries ─────────────────────────────────────────────────────────

def test_module_has_no_authority_network_llm_or_clock_imports():
    tree = ast.parse(MODULE.read_text())
    names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    # Source-contract verifiers only: pure decoders/ID functions, no clock/IO.
    allowed = {"__future__", "hashlib", "json", "re", "dataclasses", "trader.cognition",
               "trader.core.instrument_registry", "trader.engine.protective",
               "trader.strategy.signal_occurrence", "trader.observability.registry_selector",
               "trader.observability.selection_persistence",
               "trader.world.model", "trader.world.replay"}
    assert names <= allowed, names - allowed
    src = MODULE.read_text()
    for forbidden in ("time.time", "datetime.now", "requests", "ccxt", "BrainLLM"):
        assert forbidden not in src


def test_live_path_is_unchanged_when_context_is_absent():
    """Nothing in the live trading path consumes the context yet."""
    for rel in ("trader/kernel.py", "trader/engine/orchestrator.py", "trader/engine/risk.py",
                "trader/engine/executor.py"):
        assert "opportunity_context" not in (ROOT / rel).read_text(), rel


# ── Astra blockers: source-contract integrity ─────────────────────────────

def _refused(reason, **kw):
    with pytest.raises(OpportunityContextRefused) as err:
        build(**kw)
    assert err.value.reason == reason, err.value
    return err.value


def _rehash_allocation(alloc):
    body = {k: v for k, v in alloc.items() if k not in ("decision_id", "reused")}
    return dict(body, decision_id=oc._sha(body))


def _with_ledger(alloc, active):
    state = {k: v for k, v in alloc["ledger_state"].items() if k != "sha256"}
    state = dict(state, active=active, active_count=len(active))
    return _rehash_allocation(dict(alloc, ledger_state=dict(state, sha256=oc._sha(state))))


def _inv_dict(inv):
    return json.loads(I.encode(asdict(inv)))


def _reidentify(d):
    """Recompute every source-defined ID of an investigation payload (a forger's best effort)."""
    inv = I.investigation_from_dict(d)
    s = inv.state
    fields = {k: getattr(s, k) for k in s.__dataclass_fields__ if k not in ("state_id", "schema_version")}
    state_id = I.stable_id("state", fields)
    ep = I.stable_id("episode", inv.measurement.catalog_id, s.symbol, inv.primary_trigger,
                     s.as_of_ms // I.TF * I.TF - I.TF)
    iid = I.stable_id("investigation", ep, state_id, inv.registered_ms)
    return dict(d, investigation_id=iid, episode_id=ep, state=dict(d["state"], state_id=state_id))


def test_source_contract_mirrors_are_pinned_to_their_owners():
    from trader.observability import attention as A
    assert oc.ATTENTION_SCHEMA == A.SCHEMA
    assert (oc.ALLOCATION_SCHEMA, oc.ALLOCATION_POLICY) == (C.ALLOCATION_SCHEMA, C.ALLOCATION_POLICY)
    assert set(oc.ALLOCATION_MODES) == {C.ALLOCATION_POLICY, C.LEGACY}


@pytest.mark.parametrize("mutate,reason", [
    (lambda s: dict(s, schema_version="attention.telemetry.v0"), "invalid_input"),
    (lambda s: dict(s, timeframe="1h"), "invalid_input"),
    (lambda s: {k: v for k, v in s.items() if k != "code_manifest"}, "invalid_input"),
    (lambda s: {k: v for k, v in s.items() if k != "persisted_at_ms"}, "invalid_input"),
    (lambda s: dict(s, config_id="0" * 64), "corrupt_evidence"),
    (lambda s: dict(s, capture_settings_id="0" * 64), "corrupt_evidence"),
    (lambda s: dict(s, config=dict(s["config"], k=99)), "corrupt_evidence"),   # config_id no longer derives
    (lambda s: dict(s, rows=s["rows"] + [s["rows"][0]]), "corrupt_evidence"),
])
def test_attention_scan_schema_and_derived_identity_are_verified(evidence, mutate, reason):
    e = evidence
    _refused(reason, as_of_ms=e["cut"], symbol=e["symbol"], attention_scan=mutate(dict(e["scan"])))


def test_scan_hash_is_provenance_only_when_a_persisted_allocation_binds_it(evidence):
    e = evidence
    bare = build(as_of_ms=e["cut"], symbol=e["symbol"], attention_scan=e["scan"]).to_dict()
    assert bare["attention"]["payload_binding"] == {
        "status": "UNKNOWN", "reason": "scan_payload_not_bound_by_persisted_record"}
    assert _full(e).to_dict()["attention"]["payload_binding"]["bound_by"] == "allocation_decision"
    # a forged scan (arbitrary rows, fresh SHA) is not bound by the persisted allocation
    forged = dict(e["scan"], rows=[dict(r, rank=1) for r in e["scan"]["rows"]])
    _refused("source_mismatch", as_of_ms=e["cut"], symbol=e["symbol"], attention_scan=forged,
             allocation=e["allocation"])


@pytest.mark.parametrize("field", ["persisted_at_ms", "membership", "input_versions"])
def test_future_scan_capture_is_refused(evidence, field):
    e = evidence
    scan = dict(e["scan"])
    late = e["cut"] + 1
    if field == "persisted_at_ms":
        scan[field] = late
    elif field == "membership":
        scan[field] = [dict(scan[field][0], available_ms=late)] + scan[field][1:]
    else:
        scan[field] = [dict(scan[field][0], first_seen_ms=late)] + scan[field][1:]
    _refused("future_evidence", as_of_ms=e["cut"], symbol=e["symbol"], attention_scan=scan)


def test_allocation_schema_and_future_registration_or_decision_are_refused(evidence):
    e = evidence
    kw = dict(as_of_ms=e["cut"], symbol=e["symbol"], attention_scan=e["scan"])
    a = e["allocation"]
    _refused("invalid_input", allocation=_rehash_allocation(dict(a, schema_version="x")), **kw)
    _refused("invalid_input", allocation=_rehash_allocation(dict(a, extra=1)), **kw)
    _refused("future_evidence", allocation=_rehash_allocation(dict(a, decision_cut_ms=e["cut"] + 1)), **kw)
    entry = {"investigation_id": "investigation_" + "0" * 16, "episode_id": "episode_" + "0" * 16,
             "symbol": "ZZZ/USDT", "registered_ms": e["cut"] + 1, "payload_sha256": "0" * 64}
    _refused("future_evidence", allocation=_with_ledger(a, [entry]), **kw)
    # ledger_state carries its own sha256; editing it without rehashing is corrupt
    bad = dict(a, ledger_state=dict(a["ledger_state"], active_count=7))
    _refused("corrupt_evidence", allocation=_rehash_allocation(bad), **kw)


@pytest.mark.parametrize("forge", ["investigation_id", "episode_id", "state_id",
                                   "episode_recomputed", "catalog", "schema", "extra_field"])
def test_forged_investigation_identities_are_refused(evidence, forge):
    e = evidence
    d = _inv_dict(e["inv"])
    reason = "corrupt_evidence"
    if forge == "investigation_id":
        d["investigation_id"] = "investigation_" + "a" * 16
    elif forge == "episode_id":
        d["episode_id"] = "episode_" + "a" * 16
    elif forge == "state_id":
        d["state"] = dict(d["state"], state_id="state_" + "a" * 16)
    elif forge == "episode_recomputed":        # consistent inv id over a forged episode id
        d["episode_id"] = "episode_" + "a" * 16
        d["investigation_id"] = I.stable_id("investigation", d["episode_id"],
                                            d["state"]["state_id"], d["registered_ms"])
    elif forge == "catalog":
        d = _reidentify(dict(d, measurement=dict(d["measurement"], catalog_id="catalog_" + "a" * 16)))
    elif forge == "schema":
        d, reason = dict(d, schema_version="investigation.v1"), "invalid_input"
    else:
        d, reason = dict(d, note="x"), "invalid_input"
    _refused(reason, as_of_ms=e["cut"], symbol=e["symbol"], investigation=d)


def test_internally_consistent_forged_state_must_match_its_scan(evidence):
    """Every ID recomputed over an arbitrary state still fails its source relationship."""
    e = evidence
    d = _inv_dict(e["inv"])
    forged = _reidentify(dict(d, state=dict(d["state"], config_id="b" * 64)))
    I.investigation_from_dict(forged)            # well-formed, IDs consistent
    _refused("source_mismatch", as_of_ms=e["cut"], symbol=e["symbol"],
             attention_scan=e["scan"], investigation=forged)


def test_investigation_must_match_persisted_ledger_payload_hash(evidence):
    e = evidence
    inv = e["inv"]
    entry = {"investigation_id": inv.investigation_id, "episode_id": inv.episode_id,
             "symbol": inv.state.symbol, "registered_ms": inv.registered_ms,
             "payload_sha256": oc._sha(I.encode(asdict(inv)))}
    kw = dict(as_of_ms=e["cut"], symbol=e["symbol"], attention_scan=e["scan"], investigation=inv)
    build(allocation=_with_ledger(e["allocation"], [entry]), **kw)          # bound: accepted
    _refused("source_mismatch", allocation=_with_ledger(
        e["allocation"], [dict(entry, payload_sha256="0" * 64)]), **kw)


@pytest.mark.parametrize("forge", ["event_id", "evidence_id", "other_investigation",
                                   "input_ids", "as_of_mismatch", "schema"])
def test_forged_update_identities_are_refused(evidence, forge):
    e = evidence
    u = json.loads(I.encode(asdict(e["update"])))
    reason = "corrupt_evidence"
    if forge == "event_id":
        u["event_id"] = "update_" + "a" * 16
    elif forge == "evidence_id":
        u["evidence"] = dict(u["evidence"], evidence_id="evidence_" + "a" * 16)
    elif forge == "other_investigation":
        u["investigation_id"], reason = "investigation_" + "a" * 16, "source_mismatch"
    elif forge == "input_ids":
        u["input_ids"] = ["v_forged"]
    elif forge == "as_of_mismatch":
        u["as_of_ms"] = u["as_of_ms"] - 1
    else:
        u["schema_version"], reason = "investigation.v1", "invalid_input"
    _refused(reason, as_of_ms=e["cut"], symbol=e["symbol"], investigation=e["inv"],
             investigation_update=u)


def test_future_nested_update_and_evidence_timestamps_are_refused(evidence):
    e = evidence
    inv = e["inv"]
    later = I.advance(inv, I.measure(inv, (), inv.registered_ms, inv.registered_ms + 1000))
    _refused("future_evidence", as_of_ms=inv.registered_ms, symbol=e["symbol"], investigation=inv,
             investigation_update=later)
    u = json.loads(I.encode(asdict(e["update"])))
    u["evidence"] = dict(u["evidence"], available_ms=e["cut"] + 1)   # not part of evidence_id
    _refused("future_evidence", as_of_ms=e["cut"], symbol=e["symbol"], investigation=inv,
             investigation_update=u)
    # a planned (future) deadline is not evidence availability and is accepted
    assert inv.measurement.deadline_ms > e["cut"]
    assert _full(e).to_dict()["investigation"]["status"] == "AVAILABLE"


def test_forged_world_model_identities_are_refused():
    model = WorldModel(200, [HierarchyNode(Scope(ScopeLevel.GLOBAL, "world"))])
    record = WorldModelRecord.from_model(model)
    for forged in (WorldModelRecord(record.schema_version, record.model_json, "forged-model",
                                    record.relationship_evidence, record.record_id),
                   WorldModelRecord(record.schema_version, record.model_json, record.model_id,
                                    record.relationship_evidence, "world-model-record.v1:" + "0" * 64)):
        _refused("corrupt_evidence", as_of_ms=300, symbol="BTCUSDT", world_model=forged)
    bad = WorldModel(200, [HierarchyNode(Scope(ScopeLevel.GLOBAL, "world"))])
    object.__setattr__(bad, "model_id", "forged-model")
    _refused("corrupt_evidence", as_of_ms=300, symbol="BTCUSDT", world_model=bad)


def test_registry_selection_is_verified_before_its_instrument_is_trusted():
    from dataclasses import replace
    sel = _sel()
    key = sel.selected_symbol
    kw = dict(as_of_ms=sel.cycle_as_of_ms, symbol=key)
    venue_symbol = sel.selected_id.split(":")[2]
    _refused("invalid_input", registry_selection=replace(sel, selected_id=f"evil:futures:{venue_symbol}"), **kw)
    _refused("invalid_input", registry_selection=replace(sel, selected_id=f"binance_usdm:spot:{venue_symbol}"), **kw)
    _refused("corrupt_evidence", registry_selection=replace(
        sel, selected_id="binance_usdm:futures:DOGEUSDT", selected_symbol="DOGE/USDT:USDT"),
        as_of_ms=sel.cycle_as_of_ms, symbol="DOGE/USDT:USDT")
    _refused("corrupt_evidence", registry_selection=replace(sel, selected_symbol=venue_symbol), **kw)
    _refused("invalid_input", registry_selection=replace(sel, outcome="bogus"), **kw)
    _refused("future_evidence", registry_selection=sel, as_of_ms=sel.cycle_as_of_ms - 1, symbol=key)


@pytest.mark.parametrize("iid", ["evil:futures:BTCUSDT", "binance_usdm:spot:BTCUSDT",
                                 "binance_usdm:futures:BTC/USDT", "BTC/USDT:USDT"])
def test_explicit_instrument_id_must_be_supported_canonical(iid):
    _refused("invalid_input", as_of_ms=10, symbol="BTC/USDT:USDT", instrument_id=iid)
    ok = build(as_of_ms=10, symbol="BTC/USDT:USDT", instrument_id="binance_usdm:futures:BTCUSDT")
    assert ok.to_dict()["instrument"]["canonical_id"] == "binance_usdm:futures:BTCUSDT"


# ── from_json is a contract verifier, not a checksum ──────────────────────

def _rehashed(d):
    body = {k: v for k, v in d.items() if k != "context_id"}
    return oc.canonical(dict(body, context_id=oc._context_id(body)))


def _mutations(e):
    s = e["symbol"]
    base = _full(e, signals=[_signal(s, 100, spec_id="a"), _signal(s, 200, spec_id="b")]).to_dict()

    def m(fn):
        d = json.loads(json.dumps(base))
        fn(d)
        return d
    items = base["signal_occurrences"]["items"]
    return base, {
        "authority_execution": m(lambda d: d.update(authority="EXECUTION")),
        "boundaries_missing": m(lambda d: d.update(boundaries=d["boundaries"][:-1])),
        "boundaries_empty": m(lambda d: d.update(boundaries=[])),
        "boundaries_reordered": m(lambda d: d.update(boundaries=d["boundaries"][::-1])),
        "schema_other": m(lambda d: d.update(schema_version="opportunity-context.v2")),
        "top_extra": m(lambda d: d.update(score=1.0)),
        "top_missing": m(lambda d: d.pop("world_model")),
        "section_extra": m(lambda d: d["attention"].update(salience=348.4)),
        "section_missing": m(lambda d: d["investigation"].pop("episode_id")),
        "unknown_with_extra": m(lambda d: d["world_model"].update(model_id="x")),
        "available_with_reason": m(lambda d: d["attention"].update(reason="x")),
        "bad_status": m(lambda d: d["investigation"].update(status="MAYBE")),
        "bad_unknown_reason": m(lambda d: d.update(world_model={"status": "UNKNOWN", "reason": "x"})),
        "instrument_noncanonical": m(lambda d: d.update(instrument=dict(
            d["instrument"], status="AVAILABLE", reason=None, source="supplied",
            canonical_id="evil:futures:" + d["instrument"]["symbol_key"]))),
        "instrument_other": m(lambda d: d.update(instrument=dict(
            d["instrument"], status="AVAILABLE", reason=None, source="supplied",
            canonical_id="binance_usdm:futures:QQQUSDT"))),
        "signals_reordered": m(lambda d: d["signal_occurrences"].update(items=items[::-1])),
        "signals_duplicated": m(lambda d: d["signal_occurrences"].update(items=items + items[:1])),
        "signals_empty_but_available": m(lambda d: d["signal_occurrences"].update(items=[])),
        "signal_other_symbol": m(lambda d: d["signal_occurrences"]["items"][0]["key"].update(symbol="QQQUSDT")),
        "future_update": m(lambda d: d["investigation"]["latest_update"].update(
            observed_ms=d["as_of_ms"] + 1)),
        "future_scan": m(lambda d: d["attention"].update(scan_persisted_at_ms=d["as_of_ms"] + 1)),
        "type_drift": m(lambda d: d["attention"].update(scan_as_of_ms=str(d["attention"]["scan_as_of_ms"]))),
        "binding_without_allocation": m(lambda d: d["attention"].update(
            allocation={"status": "UNKNOWN", "reason": "not_supplied"})),
    }


def test_from_json_accepts_only_the_exact_contract(evidence):
    base, cases = _mutations(evidence)
    assert OpportunityContext.from_json(_rehashed(base)).to_dict() == base
    for name, d in cases.items():
        with pytest.raises(OpportunityContextRefused) as err:
            OpportunityContext.from_json(_rehashed(d))
        assert err.value.reason == "corrupt_evidence", name


def test_from_json_rejects_noncanonical_text_and_wrong_id(evidence):
    ctx = _full(evidence)
    d = ctx.to_dict()
    for text in (json.dumps(d, indent=1), json.dumps(d, sort_keys=False),
                 oc.canonical(dict(d, context_id="opportunity-context.v1:" + "0" * 64)),
                 oc.canonical({k: v for k, v in d.items() if k != "context_id"}), "[]", "{"):
        with pytest.raises(OpportunityContextRefused) as err:
            OpportunityContext.from_json(text)
        assert err.value.reason == "corrupt_evidence"


# ── Astra review 2: payload binding, nested chronology, shared verifier ───

def _ledger_entry(inv):
    return {"investigation_id": inv.investigation_id, "episode_id": inv.episode_id,
            "symbol": inv.state.symbol, "registered_ms": inv.registered_ms,
            "payload_sha256": oc._sha(I.encode(asdict(inv)))}


def _bound_kw(e, **over):
    kw = dict(as_of_ms=e["cut"], symbol=e["symbol"], attention_scan=e["scan"],
              allocation=_with_ledger(e["allocation"], [_ledger_entry(e["inv"])]),
              investigation=e["inv"], investigation_update=e["update"])
    kw.update(over)
    return kw


def test_unbound_case_and_update_withhold_content_and_mark_chain_unknown(evidence):
    d = _full(evidence).to_dict()["investigation"]
    assert d["payload_binding"] == {"status": "UNKNOWN", "reason": "case_payload_not_bound_by_persisted_record"}
    upd = d["latest_update"]
    assert upd["payload_binding"] == {"status": "UNKNOWN", "reason": "update_payload_not_bound_by_persisted_record"}
    assert upd["chain"] == {"status": "UNKNOWN", "reason": "update_chain_and_latest_at_cut_not_verified"}
    assert upd["content"] == {"status": "UNKNOWN", "reason": "update_evidence_not_bound_or_reproduced"}
    for withheld in ("assessment", "reason_codes", "next_action", "evidence_status"):
        assert withheld not in upd


def test_ledger_bound_case_still_withholds_update_content(evidence):
    """Binding the case verifies the case, not the update's evidence input."""
    e = evidence
    d = build(**_bound_kw(e)).to_dict()["investigation"]
    assert d["payload_binding"] == {"status": "AVAILABLE", "reason": None, "bound_by": "allocation_ledger"}
    upd = d["latest_update"]
    assert upd["content"] == {"status": "UNKNOWN", "reason": "update_evidence_not_bound_or_reproduced"}
    assert upd["chain"]["status"] == "UNKNOWN"                     # still not latest-at-cut
    assert "next_action" not in upd and "assessment" not in upd


def test_forged_evidence_replayed_through_advance_is_not_verified_content(evidence):
    """Astra: a recomputed not_testable evidence replays through advance but was never measured."""
    e = evidence
    inv = e["inv"]
    ev = e["update"].evidence
    forged_ev = I.OutcomeEvidence(I.stable_id("evidence", inv.investigation_id, (), "not_testable",
                                              "invented_failure"),
                                  inv.investigation_id, ev.as_of_ms, ev.observed_ms, ev.available_ms,
                                  "not_testable", None, (), (), "invented_failure")
    forged = I.advance(inv, forged_ev)
    assert I.measure(inv, (), ev.as_of_ms, ev.observed_ms).status == "unresolved"
    upd = build(**_bound_kw(e, investigation_update=forged)).to_dict()["investigation"]["latest_update"]
    assert upd["content"] == {"status": "UNKNOWN", "reason": "update_evidence_not_bound_or_reproduced"}
    text = json.dumps(upd)
    assert "UNASSESSABLE" not in text and "not_testable" not in text and "invented" not in text


def test_threshold_edit_with_unchanged_ids_is_unbound_or_refused(evidence):
    e = evidence
    forged = _inv_dict(e["inv"])
    forged["measurement"] = dict(forged["measurement"], threshold=999)
    unbound = build(as_of_ms=e["cut"], symbol=e["symbol"], investigation=forged).to_dict()
    assert unbound["investigation"]["payload_binding"]["status"] == "UNKNOWN"
    _refused("source_mismatch", **_bound_kw(e, investigation=forged, investigation_update=None))


def test_dimension_edit_with_recomputed_ids_and_same_scan_is_unbound_or_refused(evidence):
    e = evidence
    d = _inv_dict(e["inv"])
    dims = [dict(x, value=12345.0) if i == 0 else x for i, x in enumerate(d["state"]["dimensions"])]
    forged = _reidentify(dict(d, state=dict(d["state"], dimensions=dims)))
    ctx = build(as_of_ms=e["cut"], symbol=e["symbol"], attention_scan=e["scan"],
                investigation=forged).to_dict()
    assert ctx["investigation"]["payload_binding"]["status"] == "UNKNOWN"
    _refused("source_mismatch", **_bound_kw(e, investigation=forged, investigation_update=None))


@pytest.mark.parametrize("edit", [
    lambda u: u.update(next_action=dict(u["next_action"], kind="EXECUTE")),
    lambda u: u.update(assessment=[[n, "compatible"] for n, _ in u["assessment"]]),
    lambda u: u.update(reason_codes=["forged"]),
    lambda u: u.update(rationale="trade it"),
])
def test_first_update_content_must_replay_from_case_and_evidence(evidence, edit):
    e = evidence
    u = json.loads(I.encode(asdict(e["update"])))
    edit(u)
    _refused("corrupt_evidence", as_of_ms=e["cut"], symbol=e["symbol"], investigation=e["inv"],
             investigation_update=u)
    _refused("corrupt_evidence", **_bound_kw(e, investigation_update=u))


def test_non_first_update_content_is_withheld_not_trusted(evidence):
    e = evidence
    u = json.loads(I.encode(asdict(e["update"])))
    u["previous_event_id"] = "update_" + "a" * 16
    u["next_action"] = dict(u["next_action"], kind="EXECUTE")
    u["event_id"] = I.stable_id("update", u["investigation_id"], u["previous_event_id"],
                                u["evidence"]["evidence_id"])
    upd = build(**_bound_kw(e, investigation_update=u)).to_dict()["investigation"]["latest_update"]
    assert upd["content"] == {"status": "UNKNOWN", "reason": "update_evidence_not_bound_or_reproduced"}
    assert "EXECUTE" not in json.dumps(upd)


def _scan_with_obs(scan, **obs):
    o = [dict(scan["observations"][0], **obs)] + scan["observations"][1:]
    return dict(scan, observations=o)


def test_nested_scan_observation_times_are_checked(evidence):
    e = evidence
    scan = e["scan"]
    late = _scan_with_obs(scan, available_ms=e["cut"] + 1)
    alloc = _rehash_allocation(dict(e["allocation"], source_scan_sha256=oc._sha(late)))
    _refused("future_evidence", as_of_ms=e["cut"], symbol=e["symbol"], attention_scan=late,
             allocation=alloc)
    kw = dict(as_of_ms=scan["as_of_ms"] + 10_000, symbol=e["symbol"])
    _refused("corrupt_evidence", attention_scan=_scan_with_obs(scan, available_ms=scan["as_of_ms"] + 1), **kw)
    _refused("corrupt_evidence", attention_scan=_scan_with_obs(scan, event_ms=scan["as_of_ms"] + 1), **kw)
    _refused("corrupt_evidence", attention_scan=_scan_with_obs(
        scan, event_ms=scan["as_of_ms"], available_ms=scan["as_of_ms"] - 1), **kw)
    _refused("corrupt_evidence", attention_scan=_scan_with_obs(scan, as_of_ms=scan["as_of_ms"] - 1), **kw)


def test_evidence_available_after_its_own_as_of_is_refused_even_before_the_cut(evidence):
    e = evidence
    u = json.loads(I.encode(asdict(e["update"])))
    u["evidence"] = dict(u["evidence"], available_ms=u["evidence"]["as_of_ms"] + 5)
    _refused("corrupt_evidence", as_of_ms=e["cut"] + 1000, symbol=e["symbol"], investigation=e["inv"],
             investigation_update=u)


def test_state_available_after_its_as_of_is_refused(evidence):
    e = evidence
    d = _inv_dict(e["inv"])
    forged = _reidentify(dict(d, state=dict(d["state"], available_ms=d["state"]["as_of_ms"] + 1)))
    _refused("corrupt_evidence", as_of_ms=e["cut"] + 1000, symbol=e["symbol"], investigation=forged)


def test_from_json_shares_registry_mapping_and_family_catalog_checks(evidence):
    e = evidence
    sel = _sel()
    reg = build(as_of_ms=sel.cycle_as_of_ms, symbol=sel.selected_symbol, registry_selection=sel).to_dict()
    venue_symbol = sel.selected_id.split(":")[2]
    reg["registry_selection"]["selected_symbol"] = venue_symbol          # e.g. "BTCUSDT"
    inv = build(**_bound_kw(e)).to_dict()
    cases = {"selected_symbol_spelling": reg}
    for name, fn in {
        "catalog_other": lambda d: d["investigation"].update(measurement_catalog_id="catalog_" + "0" * 16),
        "catalog_of_other_family": lambda d: d["investigation"].update(
            measurement_catalog_id=I.POSITIONING_CATALOG_ID),
        "content_claimed_execute": lambda d: d["investigation"]["latest_update"].update(content={
            "status": "AVAILABLE", "reason": None, "verified_by": "deterministic_replay",
            "assessment": [["invented", "compatible"]], "reason_codes": ["x"],
            "next_action": "EXECUTE", "evidence_status": "unresolved"}),
        "content_other_unknown_reason": lambda d: d["investigation"]["latest_update"].update(
            content={"status": "UNKNOWN", "reason": "case_payload_not_bound_by_persisted_record"}),
        "update_assessment_injected": lambda d: d["investigation"]["latest_update"].update(
            next_action="EXECUTE"),
        "case_bound_without_allocation": lambda d: d["attention"].update(
            allocation={"status": "UNKNOWN", "reason": "not_supplied"},
            payload_binding={"status": "UNKNOWN", "reason": "scan_payload_not_bound_by_persisted_record"}),
        "chain_claimed": lambda d: d["investigation"]["latest_update"].update(
            chain={"status": "AVAILABLE", "reason": None}),
        "update_binding_claimed": lambda d: d["investigation"]["latest_update"].update(
            payload_binding={"status": "AVAILABLE", "reason": None}),
    }.items():
        m = json.loads(json.dumps(inv))
        fn(m)
        cases[name] = m
    assert OpportunityContext.from_json(_rehashed(inv)).to_dict() == inv
    for name, d in cases.items():
        with pytest.raises(OpportunityContextRefused) as err:
            OpportunityContext.from_json(_rehashed(d))
        assert err.value.reason == "corrupt_evidence", name
