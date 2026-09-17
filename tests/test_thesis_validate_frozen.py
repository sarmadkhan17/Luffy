"""Regression tests for the offline frozen-dossier thesis validator.

Every test here pins a defect the Astra review of 2026-09-16 found in the
first version of `scripts/thesis_validate_frozen.py`:

- the tool hardcoded `survivor=True` instead of deriving the candidate's
  status from the frozen dossier;
- it read the rule, the window and the window control from
  `observed_pattern`, which neither `identity_sha256` nor `evidence_sha256`
  covers, so a re-hashed `observed_pattern` was accepted against an untouched
  pinned identity and evidence;
- the manifest was not required to cover `dossier.json`, and its keys were
  used as paths with no confinement;
- `dossier.json`/`manifest.json` were parsed with plain `json.loads`;
- a certification failure did not force the verdict or the exit code;
- the thesis bytes were written AFTER `validate` ran.

The fixtures are built here rather than copied from any real dossier, so the
suite reads no database, no candle store and nothing under /tmp.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from trader.research import thesis as th

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "thesis_validate_frozen", ROOT / "scripts" / "thesis_validate_frozen.py")
tvf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tvf)

CUT_MS = 1741452480000
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "LINK/USDT"]
ENTRY_LONG = "ret(24) > 0.03 and close > ema(100)"
ENTRY_SHORT = "0 - ret(24) > 0.03 and close < ema(100)"
WINDOW = "ohlcv"


# ── fixtures ─────────────────────────────────────────────────────────────
def _dossier(status: str = "survivor", powered: bool = True) -> dict:
    identity = {
        "dossier_schema": "luffy.candidate_dossier.v1", "combo_schema": 1,
        "hash": "abcdef0123456789", "tf": "4h", "geo": "trail", "k": 2,
        "direction": "both", "window": WINDOW,
        "parts": ["ret24>p75", "above_ema100"],
        "entry_long": ENTRY_LONG, "entry_short": ENTRY_SHORT,
        "round": "grow", "parent": None, "trigger": "ret24>p75",
        "cut_ms": CUT_MS, "discovery_symbols": sorted(SYMBOLS),
        "created_at": "2026-09-14T17:59:20.275891+00:00",
    }
    evidence = [
        {"id": "E.combo.verdict", "source": "research_combos.verdict",
         "value": status},
        {"id": "E.combo.window", "source": "research_combos.window",
         "value": WINDOW},
        {"id": "E.combo.trades", "source": "research_combos.trades",
         "value": 758},
        {"id": "E.combo.median_pf", "source": "research_combos.median_pf",
         "value": 2.273},
        {"id": f"E.control.{WINDOW}.powered",
         "source": "research_controls.powered", "value": powered},
        {"id": "E.slice.cut_ms", "source": "research_slices.cut_ms",
         "value": CUT_MS},
        {"id": "E.slice.discovery.symbols",
         "source": "research_slices.counts.discovery", "value": len(SYMBOLS)},
    ]
    observed = {
        "label": "OBSERVED",
        "rule": {"timeframe": "4h", "geometry": "trail",
                 "entry_long": ENTRY_LONG, "entry_short": ENTRY_SHORT,
                 "parts": [
                     {"key": "ret24>p75", "kind": "gauge", "gauge": "ret24",
                      "long": "ret(24) > 0.03", "short": "0 - ret(24) > 0.03"},
                     {"key": "above_ema100", "kind": "gauge",
                      "gauge": "ema100", "long": "close > ema(100)",
                      "short": "close < ema(100)"}]},
        "code_availability": {"data_requires": ["ohlcv"],
                              "parts_parse_in_dsl": True},
        "statistical_testability": {
            "window": WINDOW, "scored_symbols": len(SYMBOLS), "trades": 758,
            "testable_flag": True,
            "window_control": {"powered": powered, "consistency_p": 0.002}},
        "data_coverage": {"discovery_symbols": len(SYMBOLS)},
    }
    return {"schema": "luffy.candidate_dossier.v1", "tool_version": "2",
            "identity": identity, "identity_sha256": th.sha256_json(identity),
            "evidence": evidence, "evidence_sha256": th.sha256_json(evidence),
            "observed_pattern": observed, "integrity_problems": []}


def _freeze(tmp: Path, dossier: dict, *, name: str = "frozen",
            manifest_files=None, extra_files=None) -> Path:
    """Write a dossier directory whose manifest hashes what is on disk."""
    d = tmp / name
    d.mkdir()
    (d / "dossier.json").write_text(json.dumps(dossier, indent=1,
                                               sort_keys=True))
    (d / "dossier.md").write_text("# frozen\n")
    for rel, body in (extra_files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    names = ["dossier.json", "dossier.md"] if manifest_files is None \
        else manifest_files
    files = {}
    for n in names:
        p = d / n
        files[n] = hashlib.sha256(p.read_bytes()).hexdigest() \
            if p.is_file() else "0" * 64
    (d / "manifest.json").write_text(json.dumps(
        {"schema": "luffy.candidate_dossier_manifest.v1", "tool_version": "2",
         "files": files}, indent=1, sort_keys=True))
    return d


def _thesis(dossier: dict, **over) -> dict:
    t = {
        "schema": th.SCHEMA,
        "candidate_hash": dossier["identity"]["hash"],
        "identity_sha256": dossier["identity_sha256"],
        "evidence_sha256": dossier["evidence_sha256"],
        "mechanism": {
            "tag": "local_trend_pullback",
            "statement": "The rule may select intervals of persistent drift, "
                         "within which two entry-bar states may separately "
                         "mark a higher mean R. Proposed, not established.",
            "premise_ids": ["P1", "P2"]},
        "premises": [
            {"id": "P1", "kind": "observed", "evidence_ids": ["E.combo.trades"],
             "claim": {"evidence_id": "E.combo.trades", "op": "==",
                       "value": 758}},
            {"id": "P2", "kind": "proposed",
             "text": "Entry-bar trend shape may separate realized mean R "
                     "within this unchanged trade population."},
        ],
        "rivals": [
            {"id": "R1", "kind": "shared_market_regime",
             "text": "One market-wide episode lifts many correlated symbols "
                     "and is counted once per symbol."},
            {"id": "R2", "kind": "selection_from_many_variants",
             "text": "Both conditions were written after reading the dossier, "
                     "so neither is an unseen prediction of the search."},
        ],
        "predictions": [
            {"id": "H1", "condition": "pct_rank(efficiency_ratio(30), 200) > 0.5",
             "metric": "mean_r", "relation": ">", "baseline": "complement",
             "premise_ids": ["P2"], "distinguishes": ["R1"]},
            {"id": "H2", "condition": "rel_volume(20) > 1.2",
             "metric": "mean_r", "relation": ">", "baseline": "complement",
             "premise_ids": ["P2"], "distinguishes": ["R2"]},
        ],
        "kill_condition": "Neither prediction separates mean R on any "
                          "registered prospective slice.",
    }
    t.update(over)
    return t


def _write_thesis(tmp: Path, obj: dict, name: str = "thesis.json") -> Path:
    p = tmp / name
    p.write_text(json.dumps(obj, indent=1, sort_keys=True))
    return p


def _run(tmp: Path, dossier_dir: Path, thesis: Path, out_name="out"):
    return tvf.main(["--dossier", str(dossier_dir), "--thesis", str(thesis),
                     "--output-dir", str(tmp / out_name)])


# ── the happy path, so every negative below is a real difference ─────────
def test_a_clean_frozen_dossier_certifies_and_exits_zero(tmp_path):
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) == 0
    v = json.loads((tmp_path / "out" / "validation.json").read_text())
    assert v["certified"] is True
    assert v["survivor"] is True and v["candidate_status"] == "survivor"
    assert v["thesis_report"]["status"] == "well_formed_untested"
    assert v["verdict"]["state"] == "waiting_for_evidence"
    assert v["verdict"]["overridden"] is False
    assert all(c["ok"] for c in v["context"]["checks"])
    # certification says what it is, and what it is not
    assert "NOT statistical support" in v["certification_means"]


# ── 1. the hardcoded survivor ────────────────────────────────────────────
@pytest.mark.parametrize("status", ["prune", "grow", "scored", ""])
def test_a_non_survivor_is_refused_certification(tmp_path, status):
    d = _dossier(status=status)
    frozen = _freeze(tmp_path, d)
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) != 0
    v = json.loads((tmp_path / "out" / "validation.json").read_text())
    assert v["survivor"] is False and v["candidate_status"] == status
    assert v["certified"] is False
    assert v["verdict"]["state"] == "research_only"
    assert v["verdict"]["demo_eligible"] is False
    assert any("not 'survivor'" in p for p in v["certification_problems"])


def test_a_missing_or_ill_typed_status_fails_closed(tmp_path):
    d = _dossier()
    d["evidence"] = [e for e in d["evidence"]
                     if e["id"] != "E.combo.verdict"]
    d["evidence_sha256"] = th.sha256_json(d["evidence"])
    frozen = _freeze(tmp_path, d)
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) != 0
    v = json.loads((tmp_path / "out" / "validation.json").read_text())
    assert v["candidate_status"] == "unknown" and v["survivor"] is False
    assert v["verdict"]["state"] == "research_only"


# ── 2. observed_pattern is not pinned by either hash ─────────────────────
def _rehash_dossier_file(frozen: Path, dossier: dict) -> None:
    """Rewrite dossier.json and repair the manifest, as a tamperer would."""
    (frozen / "dossier.json").write_text(json.dumps(dossier, indent=1,
                                                    sort_keys=True))
    man = json.loads((frozen / "manifest.json").read_text())
    man["files"]["dossier.json"] = hashlib.sha256(
        (frozen / "dossier.json").read_bytes()).hexdigest()
    (frozen / "manifest.json").write_text(json.dumps(man, indent=1,
                                                     sort_keys=True))


def test_a_rehashed_observed_rule_is_caught_by_the_pinned_entries(tmp_path):
    """The whole point: identity/evidence untouched, manifest repaired, only
    `observed_pattern` edited. v1 accepted this and built the context from it."""
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    tampered = copy.deepcopy(d)
    parts = tampered["observed_pattern"]["rule"]["parts"]
    parts[0]["long"] = "ret(24) > 0.001"          # a different, laxer rule
    _rehash_dossier_file(frozen, tampered)

    # the two pinned hashes still verify — nothing about them changed
    assert th.sha256_json(tampered["identity"]) == d["identity_sha256"]
    assert th.sha256_json(tampered["evidence"]) == d["evidence_sha256"]

    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) != 0
    v = json.loads((tmp_path / "out" / "validation.json").read_text())
    failed = [c["check"] for c in v["context"]["checks"] if not c["ok"]]
    assert any("reconstruct the PINNED entry" in c for c in failed)
    assert v["context"]["context_verified"] is False
    assert v["certified"] is False
    assert v["verdict"]["state"] == "research_only"
    assert v["verdict"]["overridden"] is True
    # the contract on its own would have been happy
    assert v["contract_verdict"]["state"] == "waiting_for_evidence"


def test_a_rehashed_window_control_cannot_flip_powered(tmp_path):
    """`observed_pattern`'s control summary is a summary; the pinned evidence
    row decides, and a disagreement is a failure rather than a preference."""
    d = _dossier(powered=False)
    frozen = _freeze(tmp_path, d)
    tampered = copy.deepcopy(d)
    tampered["observed_pattern"]["statistical_testability"][
        "window_control"]["powered"] = True
    _rehash_dossier_file(frozen, tampered)
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) != 0
    v = json.loads((tmp_path / "out" / "validation.json").read_text())
    failed = [c["check"] for c in v["context"]["checks"] if not c["ok"]]
    assert any("read from pinned evidence" in c for c in failed)
    assert v["certified"] is False


def test_an_unpowered_window_refuses_the_predictions(tmp_path):
    """With the pinned control false, the contract's own `unavailable_observable`
    fires — the offline tool must not paper over it."""
    d = _dossier(powered=False)
    d["observed_pattern"]["statistical_testability"]["window_control"][
        "powered"] = False
    frozen = _freeze(tmp_path, d)
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) != 0
    v = json.loads((tmp_path / "out" / "validation.json").read_text())
    assert v["context"]["context_verified"] is True      # the context is fine
    codes = {c["code"] for p in v["thesis_report"]["predictions"]
             for c in p["codes"]}
    assert "unavailable_observable" in codes
    assert v["thesis_report"]["status"] == "rejected"
    assert v["verdict"]["state"] == "research_only"


def test_a_rehashed_cut_or_universe_is_caught(tmp_path):
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    tampered = copy.deepcopy(d)
    tampered["observed_pattern"]["statistical_testability"]["window"] = "ohlcv|x"
    _rehash_dossier_file(frozen, tampered)
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) != 0
    v = json.loads((tmp_path / "out" / "validation.json").read_text())
    assert any("observed window and the pinned window agree" in c["check"]
               for c in v["context"]["checks"] if not c["ok"])


# ── 3. the manifest is untrusted input ───────────────────────────────────
def test_a_manifest_that_does_not_cover_dossier_json_is_refused(tmp_path):
    d = _dossier()
    frozen = _freeze(tmp_path, d, manifest_files=["dossier.md"])
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) == 2
    assert not (tmp_path / "out").exists() or \
        not any((tmp_path / "out").iterdir())


@pytest.mark.parametrize("bad", ["../escape.txt", "/etc/hostname",
                                 "sub/../../escape.txt", ""])
def test_a_manifest_path_outside_the_dossier_is_refused(tmp_path, bad):
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    (tmp_path / "escape.txt").write_text("outside\n")
    man = json.loads((frozen / "manifest.json").read_text())
    man["files"][bad] = hashlib.sha256(b"outside\n").hexdigest()
    (frozen / "manifest.json").write_text(json.dumps(man, sort_keys=True))
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) == 2


def test_a_manifest_path_through_a_symlink_is_refused(tmp_path):
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    (tmp_path / "secret.txt").write_text("outside\n")
    os.symlink(tmp_path / "secret.txt", frozen / "link.json")
    man = json.loads((frozen / "manifest.json").read_text())
    man["files"]["link.json"] = hashlib.sha256(b"outside\n").hexdigest()
    (frozen / "manifest.json").write_text(json.dumps(man, sort_keys=True))
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) == 2


def test_the_frozen_source_gets_the_strict_json_loader(tmp_path):
    """Duplicate keys are a refusal in the dossier, exactly as in the thesis:
    plain json.loads takes the last value and would have silently chosen one
    of two `identity_sha256` fields."""
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    body = (frozen / "dossier.json").read_text()
    dup = body.replace('"identity_sha256":',
                       '"identity_sha256": "0", "identity_sha256":', 1)
    (frozen / "dossier.json").write_text(dup)
    man = json.loads((frozen / "manifest.json").read_text())
    man["files"]["dossier.json"] = hashlib.sha256(
        dup.encode()).hexdigest()
    (frozen / "manifest.json").write_text(json.dumps(man, sort_keys=True))
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) == 2
    with pytest.raises(tvf.FrozenError):
        tvf.strict_json(b'{"a": 1, "a": 2}', "x")
    with pytest.raises(tvf.FrozenError):
        tvf.strict_json(b'{"a": Infinity}', "x")


def test_a_broken_file_hash_is_still_refused(tmp_path):
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    (frozen / "dossier.json").write_text(
        (frozen / "dossier.json").read_text().replace('"value": 758',
                                                      '"value": 757'))
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) == 2


# ── 4. a window the frozen dossier cannot speak about ────────────────────
def test_a_prediction_on_an_unrecorded_window_is_not_certified(tmp_path):
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    obj = _thesis(d)
    obj["predictions"][1]["condition"] = "oi_ret(24) > 0.0"
    t = _write_thesis(tmp_path, obj)
    assert _run(tmp_path, frozen, t) != 0
    v = json.loads((tmp_path / "out" / "validation.json").read_text())
    gap = [w for w in v["window_coverage"] if not w["known"]]
    assert [w["id"] for w in gap] == ["H2"]
    assert v["certified"] is False
    assert v["verdict"]["state"] == "research_only"
    assert v["verdict"]["demo_eligible"] is False
    assert v["verdict"]["overridden"] is True


# ── 5. the thesis is frozen before it is judged ──────────────────────────
def test_a_rejected_thesis_is_preserved_verbatim_and_exits_nonzero(tmp_path):
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    obj = _thesis(d)
    obj["identity_sha256"] = "0" * 64                    # pins the wrong thing
    obj["premises"][0]["claim"]["value"] = 757           # and lies about it
    t = _write_thesis(tmp_path, obj)
    assert _run(tmp_path, frozen, t) == 1
    written = (tmp_path / "out" / "thesis.input.json").read_bytes()
    assert written == t.read_bytes()
    v = json.loads((tmp_path / "out" / "validation.json").read_text())
    assert v["thesis_input_sha256"] == hashlib.sha256(written).hexdigest()
    codes = {e["code"] for e in v["thesis_report"]["errors"]}
    assert {"identity_mismatch", "observation_contradicts_evidence"} <= codes
    assert v["verdict"]["state"] == "research_only"


def test_an_unparseable_thesis_is_still_frozen_on_disk(tmp_path):
    """The freeze must happen before `validate`, so even input the contract
    cannot read is recorded exactly as supplied."""
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    t = tmp_path / "broken.json"
    t.write_bytes(b'{"schema": "x", "schema": "y"}')
    assert _run(tmp_path, frozen, t) == 1
    assert (tmp_path / "out" / "thesis.input.json").read_bytes() == \
        t.read_bytes()
    v = json.loads((tmp_path / "out" / "validation.json").read_text())
    assert v["thesis_report"]["errors"][0]["code"] == "thesis_unparseable"


def test_a_populated_output_directory_is_never_overwritten(tmp_path):
    d = _dossier()
    frozen = _freeze(tmp_path, d)
    t = _write_thesis(tmp_path, _thesis(d))
    assert _run(tmp_path, frozen, t) == 0
    before = {p.name: p.read_bytes()
              for p in (tmp_path / "out").iterdir()}
    assert _run(tmp_path, frozen, t) == 2
    after = {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()}
    assert before == after


# ── 6. the effective verdict is the reported one ─────────────────────────
def test_effective_verdict_overrides_the_contract_on_any_problem():
    contract = {"state": "waiting_for_evidence", "demo_eligible": False,
                "gate_writes": "none", "blocking_condition": "b",
                "next_action": "n"}
    clean = tvf.effective_verdict(contract, [])
    assert clean["state"] == "waiting_for_evidence" and not clean["overridden"]
    forced = tvf.effective_verdict(contract, ["context did not verify"])
    assert forced["state"] == "research_only"
    assert forced["demo_eligible"] is False
    assert forced["gate_writes"] == "none"
    assert forced["overridden"] is True
    assert "context did not verify" in forced["blocking_condition"]
