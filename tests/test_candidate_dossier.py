"""Candidate dossier + thesis contract — temporary databases only, no network."""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import sqlite3
from pathlib import Path

import pytest

from scripts import candidate_dossier as cd
from trader.research import thesis as th
from trader.research.ledger import SCHEMA as LEDGER_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
CAND = "55243573515adc3b"
SENTINEL = "HELDOUT_SENTINEL_7f3a"
ALTS_L, ALTS_S = 'ref("alts", zscore(close, 96))', '0 - ref("alts", zscore(close, 96))'
VIX_L, VIX_S = 'ref("vix", ret(30))', '0 - ref("vix", ret(30))'
LONG = f"{ALTS_L} > 1.232842 and {VIX_L} > 0.155056"
SHORT = f"{ALTS_S} > 0.994184 and {VIX_S} > 0.151715"
SYMS = [f"S{i:02d}/USDT" for i in range(19)]
CUT = 1741452480000                                      # 2025-03-08


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def make_db(path: Path, *, verdict="survivor", entry_long=LONG) -> Path:
    c = sqlite3.connect(path)
    c.executescript(LEDGER_SCHEMA)
    ablation = {"r_alts_z96>p75": {"p": 0.00145, "total_pct": 28.755,
                                   "verdict": "scored", "drop_p": -0.00145,
                                   "drop_pct": 186.905, "leak": SENTINEL},
                "r_vix_ret30>p75": {"p": 3.73e-06, "total_pct": 66.249,
                                    "verdict": "scored", "drop_p": -3.73e-06,
                                    "drop_pct": 149.411},
                SENTINEL: {"p": 0.5}}
    c.execute(
        "INSERT INTO research_combos (hash, tf, geo, k, round, parent, trigger, "
        "window, parts, entry_long, entry_short, status, label, verdict, reason, "
        "consistency_p, median_pf, total_pct, max_dd_pct, trades, scored_symbols, "
        "testable, ablation, result, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (CAND, "4h", "trail", 2, "grow", "784f14c6cf3e43f7", "r_alts_z96>p75",
         "ref:alts|ref:vix", json.dumps(["r_alts_z96>p75", "r_vix_ret30>p75"]),
         entry_long, SHORT, "scored", "scored", verdict,
         "every part earns its place", 5.16e-17, 2.273, 215.66, 12.413, 758, 19,
         1, json.dumps(ablation),
         json.dumps({"heldout": SENTINEL, "per_symbol": {SENTINEL: 1}}),
         "2026-09-14T17:59:20+00:00"))
    for i in range(3):
        c.execute("INSERT INTO research_combos (hash, tf, geo, k, verdict, result) "
                  "VALUES (?,?,?,?,?,?)", (f"{i:016x}", "4h", "trail", 1, "prune",
                                           SENTINEL))
    counts = {"discovery": {s: 7000 + i for i, s in enumerate(SYMS)},
              "heldout_a": {SENTINEL: 99}, "heldout_b": {s: 3 for s in SYMS}}
    c.execute("INSERT INTO research_slices VALUES (?,?,?,?)",
              ("4h", CUT, json.dumps(counts), "2026-09-13T05:40:52+00:00"))
    g = [(ALTS_L, 1.23284225), (ALTS_S, 0.99418429), (VIX_L, 0.15505615),
         (VIX_S, 0.15171544)]
    for expr, p75 in g:
        c.execute("INSERT INTO research_gauges (tf, expr, p10, p25, p75, p90, n, "
                  "finite_frac, usable, measured_at, min, max) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("4h", expr, -1, -0.5, p75, 2, 128000, 0.99, 1, "m", -9, 9))
    for w, pw in (("ref:alts|ref:vix", 1), ("ohlcv", 1),
                  ("funding|ref:alts|ref:vix", 0)):
        c.execute("INSERT INTO research_controls (tf, window, consistency_p, "
                  "powered, status, detail, measured_at) VALUES (?,?,?,?,?,?,?)",
                  ("4h", w, 0.0024, pw, "measured", SENTINEL, "m"))
    c.execute("INSERT INTO research_tests (hash, tf, geo, gate, p, alpha_t, "
              "rejected, braked, detail, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (CAND, "4h", "trail", "gate1", 0.001, 0.0025, 1, 0, SENTINEL, "t"))
    c.execute("INSERT INTO research_candidates (hash, tf, geo, state, gate1, "
              "gate3, entries, reason, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
              (CAND, "4h", "trail", "reason_passed", SENTINEL, SENTINEL,
               SENTINEL, SENTINEL, "t"))
    c.commit()
    c.close()
    return path


@pytest.fixture
def db(tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    return make_db(d / "ledger.db")


@pytest.fixture
def ctx(db):
    return cd.build(cd.read_ledger(db, CAND))["ctx"]


def good_thesis(ctx) -> dict:
    return {
        "schema": th.SCHEMA, "candidate_hash": CAND,
        "identity_sha256": ctx["identity_sha256"],
        "evidence_sha256": ctx["evidence_sha256"],
        "mechanism": {"tag": "vol_context_trend",
                      "statement": "A rising volatility context may separate "
                                   "alt-market trends that persist from ones "
                                   "that fade.",
                      "premise_ids": ["P1", "P2"]},
        "premises": [
            {"id": "P1", "kind": "proposed",
             "text": "Volatility shocks change how long alt trends persist."},
            {"id": "P2", "kind": "observed",          # structure only, no prose
             "claim": {"evidence_id": "E.combo.scored_symbols", "op": "==",
                       "value": 19}},
        ],
        "rivals": [
            {"id": "R1", "kind": "shared_market_regime",
             "text": "One market-wide episode counted once per symbol."},
            {"id": "R2", "kind": "selection_from_many_variants",
             "text": "A lucky pair of quantile cuts among thousands tried.",
             "evidence_ids": ["E.ledger.trail.prune"]},
            {"id": "R3", "kind": "data_provenance",
             "text": "Daily VIX spans six 4h bars, clustering entries."},
        ],
        "predictions": [
            {"id": "H1", "condition": "adx(14) > 25", "metric": "mean_r",
             "relation": ">", "baseline": "complement",
             "premise_ids": ["P1"], "distinguishes": ["R1"]},
            {"id": "H2", "subset": {"kind": "leg", "value": "short"},
             "metric": "hit_rate", "relation": "<", "baseline": "all",
             "premise_ids": ["P1"], "distinguishes": ["R2"]},
        ],
        "kill_condition": "Retire if held-out B mean_r on H1 is not above "
                          "its complement.",
    }


def pred(ctx, **kw) -> dict:
    t = good_thesis(ctx)
    p = {"id": "H1", "metric": "mean_r", "relation": ">",
         "baseline": "complement", "premise_ids": ["P1"], "distinguishes": ["R1"]}
    p.update(kw)
    t["predictions"][0] = p
    return t


def codes(rep) -> set:
    out = {e["code"] for e in rep["errors"]}
    for p in rep["predictions"]:
        out |= {c["code"] for c in p["codes"]}
    return out


def run(db, out, thesis=None):
    return cd.execute(db, CAND, out, thesis)


def all_bytes(d: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(d.iterdir()))


# ── read-only access ─────────────────────────────────────────────────────
def test_connection_is_read_only_and_allowlisted(db):
    conn = cd.connect_readonly(db)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("INSERT INTO research_gauges (tf, expr) VALUES ('x','y')")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("CREATE TABLE t (a)")
        for sql in ("SELECT detail FROM research_tests",
                    "SELECT state FROM research_candidates",
                    "SELECT result FROM research_combos",
                    "SELECT detail FROM research_controls",
                    "SELECT * FROM research_combos"):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_missing_db_is_not_created(tmp_path):
    missing = tmp_path / "nope.db"
    out = tmp_path / "out"
    with pytest.raises(cd.DossierError):
        run(missing, out)
    assert not missing.exists() and not out.exists()
    assert cd.main(["--db", str(missing), "--hash", CAND,
                    "--output-dir", str(out)]) == 2
    assert not missing.exists()


def test_source_database_is_unchanged(db, tmp_path):
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    listing = sorted(p.name for p in db.parent.iterdir())
    run(db, tmp_path / "out")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in db.parent.iterdir()) == listing


def test_hostile_sentinels_never_read_or_exported(db, tmp_path, monkeypatch):
    seen = []
    real = cd._authorizer

    def spy(action, a1, a2, dbn, src):
        if action == sqlite3.SQLITE_READ:
            seen.append((a1, a2))
        return real(action, a1, a2, dbn, src)
    monkeypatch.setattr(cd, "_authorizer", spy)
    out = tmp_path / "out"
    res = run(db, out)
    assert seen
    for table, col in seen:
        assert col in cd.ALLOWED_COLUMNS[table]
    assert not {t for t, _ in seen} & {"research_tests", "research_candidates"}
    assert ("research_combos", "result") not in seen
    blob = all_bytes(out)
    assert SENTINEL.encode() not in blob
    assert res["dossier"]["verdict"]["state"] == "research_only"
    # the hostile reason_passed candidate row did not leak into the verdict
    assert res["manifest"]["verdict"] == "research_only"


def test_unknown_hash_and_bad_hash_refused(db, tmp_path):
    with pytest.raises(cd.DossierError):
        cd.execute(db, "0" * 16, tmp_path / "a")        # a prune row with no parts
    with pytest.raises(cd.DossierError):
        cd.execute(db, "ffffffffffffffff", tmp_path / "b")
    with pytest.raises(cd.DossierError):
        cd.execute(db, "../etc", tmp_path / "c")


# ── identity and determinism ─────────────────────────────────────────────
def test_deterministic_outputs_and_hashes(db, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    ra, rb = run(db, a), run(db, b)
    for name in sorted(p.name for p in a.iterdir()):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    assert ra["dossier"]["identity_sha256"] == rb["dossier"]["identity_sha256"]
    files = json.loads((a / "manifest.json").read_text())["files"]
    for name, h in files.items():
        assert hashlib.sha256((a / name).read_bytes()).hexdigest() == h


def test_identity_pins_rule_and_detects_tampering(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    good = cd.build(cd.read_ledger(make_db(tmp_path / "x" / "l.db"), CAND))
    bad_long = LONG.replace("1.232842", "1.1")
    bad = cd.build(cd.read_ledger(
        make_db(tmp_path / "y" / "l.db", entry_long=bad_long), CAND))
    assert good["dossier"]["integrity_problems"] == []
    assert good["ctx"]["identity_sha256"] != bad["ctx"]["identity_sha256"]
    assert bad["dossier"]["integrity_problems"]
    assert bad["dossier"]["verdict"]["state"] == "research_only"


# ── output guards and preservation ───────────────────────────────────────
def test_output_guards(db, tmp_path):
    full = tmp_path / "full"
    full.mkdir()
    (full / "keep.txt").write_text("original")
    with pytest.raises(cd.DossierError):
        run(db, full)
    assert (full / "keep.txt").read_text() == "original"
    with pytest.raises(cd.DossierError):
        run(db, db.parent / "inside")                 # overlaps the source dir
    with pytest.raises(cd.DossierError):
        run(db, tmp_path)                             # contains the source
    target = ROOT / "data" / "candidate-dossier-guard-test"
    with pytest.raises(cd.DossierError):
        run(db, target)
    assert not target.exists()
    out = tmp_path / "o"
    out.mkdir()
    th_in = out / "t.json"
    with pytest.raises(cd.DossierError):
        cd.prepare_output(out, db, th_in)
    with pytest.raises(cd.DossierError):
        cd.prepare_output(tmp_path / "p", db, db)


def test_empty_existing_dir_is_accepted(db, tmp_path):
    out = tmp_path / "empty"
    out.mkdir()
    run(db, out)
    assert {"dossier.json", "dossier.md", "manifest.json",
            "thesis.scaffold.json"} == {p.name for p in out.iterdir()}


def test_thesis_input_preserved_verbatim(db, tmp_path, ctx):
    src = tmp_path / "thesis.json"
    raw = json.dumps(good_thesis(ctx), indent=3).encode()
    src.write_bytes(raw)
    mtime = src.stat().st_mtime_ns
    out = tmp_path / "out"
    res = run(db, out, src)
    assert src.read_bytes() == raw and src.stat().st_mtime_ns == mtime
    assert (out / "thesis.input.json").read_bytes() == raw
    assert not (out / "thesis.scaffold.json").exists()
    assert res["dossier"]["verdict"]["state"] == "waiting_for_evidence"


# ── the verdicts this slice can reach ────────────────────────────────────
def test_missing_thesis_gives_scaffold_and_exact_next_test(db, tmp_path):
    out = tmp_path / "out"
    d = run(db, out)["dossier"]
    v = d["verdict"]
    assert v["state"] == "research_only"
    assert v["blocking_condition"] == "no thesis supplied"
    assert v["demo_eligible"] is False and v["gate_writes"] == "none"
    assert v["external_validation"]["status"] == "not_consulted"
    nt = d["next_test"]
    assert "held-out A" in nt["required_test"] and "2025-03-08" in nt["required_test"]
    assert "E.ablation.r_vix_ret30>p75.p" in nt["already_seen"]["evidence_ids"]
    assert any("referee" in b for b in nt["blockers"])
    assert d["proposed_mechanism"]["status"] == "absent"
    kinds = {r["kind"] for r in d["rivals"]}
    assert {"shared_market_regime", "selection_from_many_variants",
            "data_provenance"} <= kinds
    assert d["rivals"][0]["applies"] == "strongly"
    scaffold = json.loads((out / "thesis.scaffold.json").read_text())
    assert cd.build(cd.read_ledger(db, CAND))["ctx"]["identity_sha256"] == \
        scaffold["identity_sha256"]


def test_scaffold_cannot_be_submitted_as_is(ctx):
    rep = th.validate(cd.scaffold(ctx), ctx)
    assert rep["status"] == "rejected"


def test_well_formed_thesis_waits_for_evidence(db, ctx, tmp_path):
    rep = th.validate(good_thesis(ctx), ctx)
    assert rep["status"] == "well_formed_untested", rep["errors"]
    assert rep["mechanism_status"] == "proposed_unverified"
    src = tmp_path / "t.json"
    src.write_text(json.dumps(good_thesis(ctx)))
    d = run(db, tmp_path / "out", src)["dossier"]
    assert d["verdict"]["state"] == "waiting_for_evidence"
    assert "untested" in d["verdict"]["blocking_condition"]
    assert d["next_test"]["thesis_predictions_to_test"] == ["H1", "H2"]


def test_verdict_never_reaches_admission_states():
    reps = [None, {"status": "rejected", "errors": [{"code": "x"}]},
            {"status": "needs_review", "review_items": ["a"]},
            {"status": "well_formed_untested"},
            {"status": "passed"}]                     # a forged status
    for survivor in (True, False):
        for r in reps:
            v = th.verdict(survivor=survivor, survivor_label="x", thesis_report=r)
            assert v["state"] in th.VERDICTS
            assert v["state"] not in th.UNREACHABLE_HERE
            assert v["demo_eligible"] is False


def test_non_survivor_is_research_only(tmp_path):
    (tmp_path / "s").mkdir()
    d = cd.build(cd.read_ledger(make_db(tmp_path / "s" / "l.db",
                                        verdict="prune"), CAND))["dossier"]
    assert d["verdict"]["state"] == "research_only"
    assert "not a discovery survivor" in d["verdict"]["blocking_condition"]


# ── premises: supported vs unsupported ───────────────────────────────────
CANON_P2 = "E.combo.scored_symbols == 19 (recorded: 19)"


def test_supported_premise(ctx):
    rep = th.validate(good_thesis(ctx), ctx)
    p2 = next(p for p in rep["premises"] if p["id"] == "P2")
    assert p2["status"] == "supported_observation"
    assert p2["supported_statement"] == CANON_P2
    assert p2["observation"] == {"evidence_id": "E.combo.scored_symbols",
                                 "op": "==", "value": 19,
                                 "recorded_value": 19, "verified": True}
    assert p2["narrative"]["status"] == "absent"
    p1 = next(p for p in rep["premises"] if p["id"] == "P1")
    assert p1["status"] == "proposed_unverified"
    assert p1["supported_statement"] is None
    assert p1["narrative"]["status"] == "proposed_unverified"


def test_exact_canonical_text_is_the_only_supported_wording(ctx):
    t = good_thesis(ctx)
    t["premises"][1]["text"] = f"  {CANON_P2} "
    t["premises"].append({
        "id": "P3", "kind": "observed",                # regex terms can't gate
        "text": 'E.combo.reason == "every part earns its place" '
                '(recorded: "every part earns its place")',
        "claim": {"evidence_id": "E.combo.reason", "op": "==",
                  "value": "every part earns its place"}})
    t["premises"].append({"id": "P4", "kind": "proposed", "text": CANON_P2})
    by = {p["id"]: p for p in th.validate(t, ctx)["premises"]}
    assert by["P2"]["status"] == "supported_observation"
    assert by["P2"]["narrative"]["status"] == "canonical"
    assert by["P3"]["status"] == "supported_observation"
    assert by["P3"]["narrative"]["flags"] == []
    assert by["P4"]["status"] == "proposed_unverified"   # verbatim, still a proposal


@pytest.mark.parametrize("text", [
    # true numeric predicate, unrelated non-causal narrative
    "Bitcoin dominance fell for most of that calendar year.",
    # causal paraphrase using no term the advisory regexes know
    "Nineteen symbols scored since volatility shocks make alt trends persist.",
    "Volatility spikes are what let alt trends run on nineteen symbols.",
    # a faithful-sounding paraphrase is still not the checked statement
    "The rule scored on all nineteen discovery symbols.",
    # canonical-looking but not what was checked
    "E.combo.scored_symbols == 19 (recorded: 19) and trends persist",
    "E.combo.scored_symbols >= 19 (recorded: 19)",
])
def test_true_claim_never_promotes_its_narrative(db, ctx, tmp_path, text):
    t = good_thesis(ctx)
    t["premises"][1]["text"] = text
    rep = th.validate(t, ctx)
    p2 = rep["premises"][1]
    assert p2["status"] == "unsupported", text
    assert p2["narrative"] == {"text": text, "status": "unverified",
                               "flags": p2["narrative"]["flags"]}
    # the structured observation stays verified and usable
    assert p2["observation"]["verified"] is True
    assert p2["supported_statement"] == CANON_P2
    assert rep["status"] == "well_formed_untested"     # unsupported != invalid
    src = tmp_path / "t.json"
    src.write_text(json.dumps(t))
    out = tmp_path / "out"
    d = run(db, out, src)["dossier"]
    assert d["proposed_mechanism"]["premises"][1]["status"] == "unsupported"
    md = (out / "dossier.md").read_text()
    assert f"P2 (observed) → **unsupported**" in md
    assert f"narrative (unverified, never verified as prose): {text}" in md
    assert f"supported statement (validator-rendered): `{CANON_P2}`" in md


def test_unsupported_causal_story_not_upgraded_by_label_or_metric(ctx):
    t = good_thesis(ctx)
    t["premises"].append({
        "id": "P3", "kind": "observed",
        "text": "Nineteen symbols scored because traders chase alt strength.",
        "claim": {"evidence_id": "E.combo.scored_symbols", "op": "==", "value": 19},
        "evidence_ids": ["E.combo.consistency_p"]})
    t["premises"].append({"id": "P4", "kind": "observed",
                          "text": "Short squeezes fuel the trend legs."})
    t["premises"].append({
        "id": "P5", "kind": "observed", "text": "The funding rate was elevated.",
        "claim": {"evidence_id": "E.made.up", "op": ">", "value": 0}})
    t["premises"].append({
        "id": "P6", "kind": "observed", "text": "Whales accumulate in these bars.",
        "claim": {"evidence_id": "E.combo.trades", "op": ">", "value": 100}})
    rep = th.validate(t, ctx)
    by = {p["id"]: p for p in rep["premises"]}
    for pid in ("P3", "P4", "P5", "P6"):
        assert by[pid]["status"] == "unsupported", pid
    assert by["P3"]["narrative"]["flags"] and by["P6"]["narrative"]["flags"]
    assert by["P4"]["supported_statement"] is None
    assert by["P5"]["supported_statement"] is None
    assert rep["status"] == "well_formed_untested"     # unsupported != invalid
    assert rep["mechanism_status"] == "proposed_unverified"


def test_contradicted_observation_rejects(ctx):
    t = good_thesis(ctx)
    t["premises"][1]["claim"]["value"] = 25
    rep = th.validate(t, ctx)
    assert rep["status"] == "rejected"
    assert "observation_contradicts_evidence" in codes(rep)


# ── strict schema and reference integrity ────────────────────────────────
def test_strict_schema(ctx):
    t = good_thesis(ctx)
    t["verdict"] = "reason_passed"
    assert "schema_unknown_key" in codes(th.validate(t, ctx))
    for n in (1, 5):
        t = good_thesis(ctx)
        base = t["predictions"][0]
        t["predictions"] = [dict(base, id=f"H{i + 1}",
                                 condition=f"adx(14) > {20 + i}")
                            for i in range(n)]
        assert "prediction_count" in codes(th.validate(t, ctx))
    t = pred(ctx, condition="adx(14) > 25", subset={"kind": "leg", "value": "long"})
    assert th.validate(t, ctx)["status"] == "rejected"
    t = pred(ctx, condition="adx(14) > 25", metric="sharpe")
    assert th.validate(t, ctx)["status"] == "rejected"
    t = good_thesis(ctx)
    t["predictions"][0]["extra"] = 1
    assert "schema_unknown_key" in codes(th.validate(t, ctx))
    t = good_thesis(ctx)
    del t["kill_condition"]
    assert "schema_missing_key" in codes(th.validate(t, ctx))
    assert th.validate([], ctx)["status"] == "rejected"


def test_reference_integrity(ctx):
    t = good_thesis(ctx)
    t["predictions"][0]["premise_ids"] = ["P9"]
    assert "dangling_reference" in codes(th.validate(t, ctx))
    t = good_thesis(ctx)
    t["predictions"][1]["distinguishes"] = ["R9"]
    assert "dangling_reference" in codes(th.validate(t, ctx))
    t = good_thesis(ctx)
    t["identity_sha256"] = "0" * 64
    assert "identity_mismatch" in codes(th.validate(t, ctx))
    t = good_thesis(ctx)
    t["evidence_sha256"] = "0" * 64
    assert "identity_mismatch" in codes(th.validate(t, ctx))
    t = good_thesis(ctx)
    t["mechanism"]["premise_ids"] = ["P2"]
    assert "mechanism_without_proposal" in codes(th.validate(t, ctx))
    t = good_thesis(ctx)
    t["rivals"] = t["rivals"][:2]
    assert "missing_rival" in codes(th.validate(t, ctx))
    t = good_thesis(ctx)
    t["premises"][1]["id"] = "P1"
    assert "duplicate_id" in codes(th.validate(t, ctx))


# ── predictions: tautologies, restatements, duplicates, availability ─────
@pytest.mark.parametrize("cond", [
    f"{ALTS_L} > 1.232842",                 # a part, verbatim
    f"{VIX_L} > 0.1",                       # implied by the long cut
    LONG,                                   # the whole long rule
    f"not ({SHORT})",
    f"{VIX_L} < 0.155056",                  # empty on long, implied on short
])
def test_entry_rule_tautology_refused(ctx, cond):
    rep = th.validate(pred(ctx, condition=cond), ctx)
    assert rep["status"] == "rejected"
    assert "entry_rule_tautology" in codes(rep)


def test_threshold_recut_needs_review_not_valid(ctx):
    rep = th.validate(pred(ctx, condition=f"{VIX_L} > 0.3"), ctx)
    assert rep["status"] == "needs_review"
    v = th.verdict(survivor=True, survivor_label="survivor", thesis_report=rep)
    assert v["state"] == "research_only" and "review" in v["blocking_condition"]


def test_duplicates_and_contradictory_pairs(ctx):
    t = good_thesis(ctx)
    t["predictions"][1] = dict(t["predictions"][0], id="H2",
                               condition="adx(14)  >  25")
    assert "duplicate_prediction" in codes(th.validate(t, ctx))
    t["predictions"][1]["relation"] = "<"
    assert "contradictory_pair" in codes(th.validate(t, ctx))


@pytest.mark.parametrize("kw,code", [
    ({"condition": "made_up_feature(3) > 1"}, "unsupported_expression"),
    ({"condition": "__import__('os')"}, "unsupported_expression"),
    ({"condition": "funding_z(360) > 1"}, "unavailable_observable"),
    ({"condition": "adx(14)"}, "not_a_condition"),
    ({"condition": "1 > 0"}, "constant_condition"),
    ({"subset": {"kind": "markets", "value": ["NOPE/USDT"]}}, "unavailable_market"),
    ({"subset": {"kind": "markets", "value": SYMS}}, "discovery_restatement"),
    ({"subset": {"kind": "regime", "value": "MOON"}}, "unavailable_regime"),
    ({"subset": {"kind": "era", "value": {"from": "2023-01-01",
                                          "to": "2024-01-01"}}},
     "discovery_era_already_seen"),
    ({"subset": {"kind": "era", "value": {"from": "2025-01-01",
                                          "to": "2025-06-01"}}},
     "era_straddles_cut"),
])
def test_unavailable_or_restated_refused(ctx, kw, code):
    rep = th.validate(pred(ctx, **kw), ctx)
    assert rep["status"] == "rejected"
    assert code in codes(rep)


def test_unmeasured_window_needs_review(ctx):
    rep = th.validate(pred(ctx, condition='ref("btcdom", ret(30)) > 0.05'), ctx)
    assert rep["status"] == "needs_review"


def test_later_era_and_regime_subsets_accepted(ctx):
    for sub in ({"kind": "era", "value": {"from": "2025-06-01", "to": "2026-01-01"}},
                {"kind": "regime", "value": "VOLATILE"}):
        rep = th.validate(pred(ctx, subset=sub), ctx)
        assert rep["status"] == "well_formed_untested", rep


def test_unparseable_thesis_file_is_rejected_not_crashed(db, tmp_path):
    for i, body in enumerate(('{"a": 1, "a": 2}', '{"x": NaN}', "not json")):
        src = tmp_path / f"t{i}.json"
        src.write_text(body)
        d = run(db, tmp_path / f"out{i}", src)["dossier"]
        assert d["thesis"]["validation"]["errors"][0]["code"] == "thesis_unparseable"
        assert d["verdict"]["state"] == "research_only"


def test_interval_relation_edges():
    a = th._against
    assert a(("x", ">", 1.0), ("x", ">", 1.0)) == "implied"
    assert a(("x", ">=", 1.0), ("x", ">", 1.0)) == "implied"
    assert a(("x", ">", 1.0), ("x", ">=", 1.0)) == "unknown"
    assert a(("x", "<=", 1.0), ("x", ">", 1.0)) == "empty"
    assert a(("x", "<=", 1.0), ("x", ">=", 1.0)) == "unknown"
    assert a(("x", "<", 5.0), ("x", "<", 2.0)) == "implied"
    assert a(("x", ">", 2.0), ("x", "<", 2.0)) == "empty"
    assert a(("y", ">", 0.0), ("x", ">", 1.0)) == "unknown"
    assert th.bound(th.dsl.parse("0 - adx(14) > 1").body) == ("adx(14)", "<", -1.0)
    assert th.bound(th.dsl.parse("25 < adx(14)").body) == ("adx(14)", ">", 25.0)
    assert th.bound(th.dsl.parse("adx(14) > rsi(14)").body) is None


def test_validation_is_pure(ctx):
    t = good_thesis(ctx)
    frozen, c0 = copy.deepcopy(t), copy.deepcopy(ctx)
    r1, r2 = th.validate(t, ctx), th.validate(t, ctx)
    assert t == frozen and ctx == c0 and r1 == r2
