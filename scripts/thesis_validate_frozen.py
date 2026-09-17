"""Validate one thesis against a FROZEN candidate dossier, with no database.

    ./venv/bin/python scripts/thesis_validate_frozen.py \\
        --dossier /tmp/luffy-candidate-dossier-20260915-v2 \\
        --thesis  /tmp/luffy-thesis-55243573515adc3b.json \\
        --output-dir docs/superpowers/artifacts/2026-09-16-candidate-...

`scripts/candidate_dossier.py` builds its thesis context from a read-only
SQLite read. This tool does the same job from an already-frozen dossier
directory instead, so a thesis can be policed with **no ledger read at all**.
It opens no database, no network and no candle store.

It does not re-implement the contract. `trader.research.thesis.validate` and
`.verdict` are called unchanged, and the thesis bytes are parsed by
`candidate_dossier.load_thesis` — the same strict loader (duplicate JSON keys
and non-finite numbers are refusals). `dossier.json` and `manifest.json` are
read through the same strictness here.

**Every context field is bound to hash-pinned data.** A frozen dossier's
`identity` and `evidence` are the only two objects a thesis pins, via
`identity_sha256` and `evidence_sha256`. `observed_pattern` is NOT pinned by
either hash, so nothing is taken from it on its own word: the rule's entries,
the window, the discovery cut and symbols come from `identity`; the window
control and the candidate's own discovery status come from `evidence`; and the
parts, which only `observed_pattern` carries at leg resolution, are admitted
only after they are proved to reconstruct the pinned entry expressions. A
dossier whose `observed_pattern` was edited and re-hashed in the manifest,
leaving `identity` and `evidence` untouched, fails these checks.

What it must prove before it certifies anything:

1. The frozen directory verifies against its own `manifest.json` file hashes,
   the manifest covers `dossier.json`, and every path it names stays inside
   the dossier directory (no `..`, no absolute path, no symlink).
2. `sha256_json(dossier["identity"])` reproduces the recorded
   `identity_sha256`, and `sha256_json(dossier["evidence"])` reproduces the
   recorded `evidence_sha256`.
3. The rule's parts reconstruct the pinned entries, re-parse in the live DSL,
   and re-derive both the recorded `data_requires` and the pinned window key.
4. The candidate's recorded discovery status is `survivor`. Anything else, or
   a missing/ill-typed status, is a non-survivor: **fail closed**.

Where the offline context is genuinely thinner than the database one, it
FAILS CLOSED rather than guessing. A frozen dossier records the window control
for the candidate's own window only. A live context holds every window at that
horizon, and there a recorded-but-unpowered window is a hard refusal
(`unavailable_observable`) while an unrecorded one is only a review item. So a
prediction whose derived window key is absent from the frozen map is reported
as an offline context gap and the run is NOT certified; the validation report
is still written, unedited.

**Certification is context integrity only.** It says the rebuilt context is
provably the frozen one and that every prediction lands where this dossier can
speak. It is not statistical support, not evidence that anything in the thesis
is true, and not a gate pass. When certification fails the effective verdict is
forced to `research_only` with `demo_eligible` false, whatever the contract
would otherwise have said.

The input is frozen before it is judged: the thesis bytes are snapshotted once,
written to `thesis.input.json` with an exclusive create BEFORE `validate` is
called, and never rewritten. A rejected thesis is preserved and reported, never
silently patched.

Exit codes: 0 certified context and a well-formed thesis; 1 judged and written
but not certified, or a thesis that is rejected or needs review; 2 refused
before anything was judged.

Research only. It writes no gate, spends no error budget, reads no held-out
number, and has no path that can emit `reason_passed`.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trader.research import thesis as th                        # noqa: E402
from trader.strategy import dsl                                 # noqa: E402

TOOL_VERSION = "2"
SCHEMA = "luffy.thesis_validation.v2"
OUTPUT_FILES = ("thesis.input.json", "validation.json",
                "dossier.validated.md", "manifest.json")
REQUIRED_INPUTS = ("dossier.json", "manifest.json")
#: the manifest must hash the file the whole context is rebuilt from
MANIFEST_MUST_COVER = ("dossier.json",)
#: the pinned evidence id carrying the candidate's own discovery status
STATUS_EVIDENCE = "E.combo.verdict"
SURVIVOR = "survivor"

NOTICE = ("RESEARCH ONLY. Offline validation against a frozen discovery "
          "dossier. No database, network or held-out number is read; no gate "
          "is written and no error budget is spent.")
CERTIFICATION_MEANS = (
    "CONTEXT INTEGRITY ONLY: the rebuilt context provably matches the "
    "hash-pinned frozen identity and evidence, the candidate is a recorded "
    "discovery survivor, and every prediction's window is one this frozen "
    "dossier can speak about. Certification is NOT statistical support, NOT "
    "evidence that anything in the thesis is true, and NOT a gate pass.")


class FrozenError(Exception):
    """A user-facing refusal: bad paths, a broken freeze, an unusable rule."""


def _load_candidate_dossier():
    """Import scripts/candidate_dossier.py without a package."""
    path = ROOT / "scripts" / "candidate_dossier.py"
    spec = importlib.util.spec_from_file_location("candidate_dossier", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def strict_json(data: bytes, what: str):
    """The loader `candidate_dossier.load_thesis` applies to a thesis, applied
    to the frozen source too: duplicate keys and non-finite numbers are
    refusals, not last-value-wins and not `Infinity`."""

    def pairs(kv):
        keys = [k for k, _ in kv]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate JSON key")
        return dict(kv)

    def const(name):
        raise ValueError(f"non-finite number {name}")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=const)
    except (UnicodeDecodeError, ValueError) as e:
        raise FrozenError(f"{what} is not strict JSON: {e}") from None


# ── the frozen input ─────────────────────────────────────────────────────
def _inside(a: Path, b: Path) -> bool:
    return b == a or b in a.parents


def manifest_member(root: Path, name: str) -> Path:
    """A manifest entry resolved inside the dossier, or a refusal.

    A manifest is untrusted input: it is read before anything is verified, and
    its keys are used as paths. Absolute paths, `..`, and symlinks anywhere on
    the way down all leave the directory being certified, so all three are
    refused rather than normalised."""
    if not isinstance(name, str) or not name or name != name.strip():
        raise FrozenError(f"frozen manifest names an invalid path: {name!r}")
    if name.startswith("/") or "\\" in name or ":" in name:
        raise FrozenError(f"frozen manifest path is not relative: {name!r}")
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part == ".." for part in pure.parts) \
            or not pure.parts:
        raise FrozenError(f"frozen manifest path escapes the dossier: {name!r}")
    cur = root
    for part in pure.parts:
        cur = cur / part
        if cur.is_symlink():
            raise FrozenError(
                f"frozen manifest path traverses a symlink: {name!r}")
    p = root / pure
    if not _inside(p.resolve(), root):
        raise FrozenError(f"frozen manifest path escapes the dossier: {name!r}")
    return p


def read_frozen(d: Path) -> dict:
    d = Path(d).resolve()
    if not d.is_dir():
        raise FrozenError(f"frozen dossier directory not found: {d}")
    for name in REQUIRED_INPUTS:
        p = d / name
        if p.is_symlink() or not p.is_file():
            raise FrozenError(f"{d} is missing {name}")
    manifest = strict_json((d / "manifest.json").read_bytes(),
                           "the frozen manifest")
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files:
        raise FrozenError("frozen manifest records no file hashes")
    missing_cover = [n for n in MANIFEST_MUST_COVER if n not in files]
    if missing_cover:
        raise FrozenError(
            f"frozen manifest does not cover {missing_cover}; the context "
            "would be rebuilt from an unhashed file")
    checked, blobs = {}, {}
    for name, want in sorted(files.items()):
        p = manifest_member(d, name)
        if p.is_symlink() or not p.is_file():
            raise FrozenError(f"frozen manifest names a missing file: {name}")
        blob = p.read_bytes()                       # snapshot once
        got = hashlib.sha256(blob).hexdigest()
        if not isinstance(want, str):
            raise FrozenError(f"frozen manifest hash is not a string: {name}")
        blobs[name] = blob
        checked[name] = {"recorded": want, "recomputed": got,
                         "match": got == want}
    bad = sorted(n for n, r in checked.items() if not r["match"])
    if bad:
        raise FrozenError(f"frozen dossier does not verify: {bad}")
    dossier = strict_json(blobs["dossier.json"], "the frozen dossier")
    if not isinstance(dossier, dict):
        raise FrozenError("the frozen dossier is not a JSON object")
    return {"dir": str(d), "dossier": dossier, "manifest": manifest,
            "files": checked}


# ── the context, rebuilt from pinned fields and then proved ──────────────
def _conjuncts(expr: str) -> list:
    """The `and`-separated conjuncts of an expression, flattened and rendered
    canonically. `(a and b) and c` and `a and b and c` give the same list, so a
    part boundary can never change the answer — but the conjuncts themselves,
    and their order, must match exactly."""
    def walk(node):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            return [c for v in node.values for c in walk(v)]
        return [ast.unparse(node)]
    return walk(dsl.parse(expr).body)


def _get(obj, *path, default=None):
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj


def build_ctx(dossier: dict) -> dict:
    """The thesis context, rebuilt from the hash-pinned fields and proved.

    `identity` and `evidence` are the two objects a thesis pins. Everything the
    contract is handed is derived from those, except the per-leg part
    expressions, which only `observed_pattern` carries and which are therefore
    admitted only after they are proved to rebuild the pinned entries.
    """
    ident = _get(dossier, "identity")
    ev_list = _get(dossier, "evidence")
    obs = _get(dossier, "observed_pattern", default={})
    if not isinstance(ident, dict) or not isinstance(ev_list, list):
        raise FrozenError("the frozen dossier has no identity/evidence objects")

    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    # 1. the two hashes a thesis pins
    id_sha = th.sha256_json(ident)
    ev_sha = th.sha256_json(ev_list)
    check("identity_sha256 recomputes from the frozen identity",
          id_sha == dossier.get("identity_sha256"),
          f"recomputed {id_sha}, recorded {dossier.get('identity_sha256')}")
    check("evidence_sha256 recomputes from the frozen evidence list",
          ev_sha == dossier.get("evidence_sha256"),
          f"recomputed {ev_sha}, recorded {dossier.get('evidence_sha256')}")

    ids = [e.get("id") for e in ev_list if isinstance(e, dict)]
    check("every pinned evidence id is unique and well formed",
          len(ids) == len(ev_list) and len(set(ids)) == len(ids)
          and all(isinstance(i, str) for i in ids),
          f"{len(ev_list)} entries, {len(set(ids))} distinct ids")
    ev = {e["id"]: e.get("value") for e in ev_list
          if isinstance(e, dict) and isinstance(e.get("id"), str)}

    # 2. the rule. Entries are pinned; parts must rebuild them.
    pinned_long = ident.get("entry_long")
    pinned_short = ident.get("entry_short")
    parts = _get(obs, "rule", "parts", default=None)
    if not isinstance(parts, list) or not parts or not all(
            isinstance(p, dict) and all(isinstance(p.get(s), str)
                                        for s in th.LEGS) for p in parts):
        raise FrozenError("the frozen dossier records no usable rule parts")
    try:
        entry_terms = {s: _conjuncts(ident[f"entry_{s}"]) for s in th.LEGS}
        part_terms = {s: [t for p in parts for t in _conjuncts(p[s])]
                      for s in th.LEGS}
    except (KeyError, TypeError, dsl.SpecError) as e:
        raise FrozenError(f"the frozen rule does not parse: {e}") from None

    check("the frozen parts reconstruct the PINNED entry expressions",
          all(part_terms[s] == entry_terms[s] for s in th.LEGS),
          "; ".join(f"{s}: parts -> {part_terms[s]} vs identity "
                    f"{entry_terms[s]}" for s in th.LEGS))
    check("the observed rule's entries equal the pinned entries",
          _get(obs, "rule", "entry_long") == pinned_long
          and _get(obs, "rule", "entry_short") == pinned_short,
          "observed_pattern.rule entries match identity.entry_long/short")
    part_keys = [p.get("key") for p in parts]
    check("the part keys equal the pinned identity.parts",
          part_keys == list(ident.get("parts") or []),
          f"parts {part_keys}, identity {list(ident.get('parts') or [])}")

    # 3. what the rule needs, and the window that follows from it
    derived = list(dsl.data_requires(
        *[dsl.parse(p[s]) for p in parts for s in th.LEGS]))
    recorded_requires = _get(obs, "code_availability", "data_requires",
                             default=None)
    check("re-derived data_requires equals the frozen record",
          isinstance(recorded_requires, list)
          and derived == list(recorded_requires),
          f"derived {derived}, frozen {recorded_requires}")
    window = ident.get("window")
    check("the derived window key equals the PINNED identity window",
          th.window_key(derived) == window,
          f"derived {th.window_key(derived)!r}, pinned {window!r}")
    check("the observed window and the pinned window agree",
          _get(obs, "statistical_testability", "window") == window
          and ev.get("E.combo.window") == window,
          f"observed {_get(obs, 'statistical_testability', 'window')!r}, "
          f"evidence {ev.get('E.combo.window')!r}, pinned {window!r}")

    # 4. the window control, from PINNED evidence
    powered_id = f"E.control.{window}.powered"
    powered = ev.get(powered_id)
    obs_powered = _get(obs, "statistical_testability", "window_control",
                       "powered", default=None)
    have_ctl = check(
        "the window control is read from pinned evidence, not the summary",
        isinstance(powered, bool)
        and (obs_powered is None or bool(obs_powered) == powered),
        f"{powered_id} = {powered!r}, observed summary {obs_powered!r}")
    controls = {window: bool(powered)} if have_ctl else {}

    # 5. cut and universe, pinned in identity and cross-checked in evidence
    cut_ms = ident.get("cut_ms")
    check("the discovery cut is pinned and matches the pinned slice evidence",
          isinstance(cut_ms, int) and ev.get("E.slice.cut_ms") == cut_ms,
          f"identity.cut_ms {cut_ms!r}, E.slice.cut_ms "
          f"{ev.get('E.slice.cut_ms')!r}")
    syms = ident.get("discovery_symbols")
    ok_syms = (isinstance(syms, list) and syms
               and all(isinstance(s, str) for s in syms)
               and len(set(syms)) == len(syms))
    check("the discovery universe is pinned and matches its pinned count",
          ok_syms and ev.get("E.slice.discovery.symbols") == len(syms),
          f"{len(syms) if ok_syms else '?'} pinned symbols, "
          f"E.slice.discovery.symbols {ev.get('E.slice.discovery.symbols')!r}")

    # 6. the candidate's own discovery status — fail closed
    status = ev.get(STATUS_EVIDENCE)
    survivor = isinstance(status, str) and status == SURVIVOR
    label = status if isinstance(status, str) else "unknown"
    check("the candidate's pinned discovery status is 'survivor'", survivor,
          f"{STATUS_EVIDENCE} = {status!r}")

    ctx = {"candidate_hash": ident.get("hash"),
           "identity_sha256": dossier.get("identity_sha256"),
           "evidence_sha256": dossier.get("evidence_sha256"),
           "evidence": ev,
           "rule": {"parts": parts, "entry_long": pinned_long,
                    "entry_short": pinned_short},
           "requires": derived,
           "discovery_symbols": sorted(syms) if ok_syms else [],
           "cut_ms": cut_ms,
           "controls": controls,
           "provenance_rival_required": any(r.startswith("ref:")
                                            for r in derived)}
    return {"ctx": ctx, "checks": checks,
            "context_verified": all(c["ok"] for c in checks),
            "survivor": survivor, "survivor_label": label,
            "controls_scope": (
                "frozen dossiers record the candidate's own window control "
                "only; a live context holds every window at this horizon, so "
                "a prediction landing on any other window cannot be judged "
                "offline and is refused certification here")}


def window_coverage(thesis_obj, ctx: dict) -> list:
    """Every prediction condition's derived window, and whether the offline
    controls map can speak about it. A gap is fail-closed, not a guess."""
    out = []
    preds = thesis_obj.get("predictions") if isinstance(thesis_obj, dict) else None
    for i, p in enumerate(preds or []):
        if not isinstance(p, dict) or "condition" not in p:
            continue
        row = {"id": p.get("id"), "index": i, "window": None, "known": None}
        try:
            tree = dsl.parse(p["condition"])
            req = set(dsl.data_requires(tree)) | set(ctx["requires"])
            row["window"] = th.window_key(sorted(req))
            row["known"] = row["window"] in ctx["controls"]
        except (dsl.SpecError, TypeError) as e:
            row["window"] = f"unparseable: {e}"
            row["known"] = True      # the contract refuses it on its own
        out.append(row)
    return out


def effective_verdict(contract: dict, problems: list) -> dict:
    """What this run is allowed to conclude. Certification failures override
    the contract: an unverified context cannot support any state but
    `research_only`, whatever the thesis itself looks like."""
    out = dict(contract)
    if problems:
        out.update(
            state="research_only",
            demo_eligible=False,
            gate_writes="none",
            blocking_condition="context not certified: " + "; ".join(problems),
            next_action="repair the frozen context, or judge this thesis "
                        "against a dossier that verifies; nothing here may be "
                        "read as support")
    out["overridden"] = bool(problems)
    return out


# ── output ───────────────────────────────────────────────────────────────
def render_markdown(v: dict) -> str:
    d, r, ver = v["context"], v["thesis_report"], v["verdict"]
    L = [f"# Validated thesis dossier — {v['candidate_hash']}", "",
         f"> {NOTICE}", "",
         f"- frozen dossier: `{v['frozen']['dir']}`",
         f"- identity_sha256 `{v['identity_sha256']}`",
         f"- evidence_sha256 `{v['evidence_sha256']}`",
         f"- thesis_input_sha256 `{v['thesis_input_sha256']}` "
         f"(bytes, hashed and frozen before validation)",
         f"- thesis_sha256 `{r.get('thesis_sha256')}` (canonical JSON, "
         f"computed by the contract)",
         f"- candidate discovery status: `{v['candidate_status']}` "
         f"(survivor: {v['survivor']})",
         f"- **thesis status: {r['status']}**",
         f"- **effective verdict: {ver['state']}** — "
         f"{ver['blocking_condition']}",
         f"- next action: {ver['next_action']}",
         f"- demo eligible: {ver['demo_eligible']}; gate writes: "
         f"{ver['gate_writes']}",
         f"- certified: {v['certified']}"
         + ("" if v["certified"] else
            f" — {'; '.join(v['certification_problems'])}"),
         "", f"> **What certification means.** {CERTIFICATION_MEANS}", ""]
    if ver.get("overridden"):
        cv = v["contract_verdict"]
        L += [f"> The contract alone would have said `{cv['state']}`; the "
              "certification failure above overrides it.", ""]
    L += ["## 1. Context proofs (offline, against the frozen dossier)", ""]
    for c in d["checks"]:
        L.append(f"- [{'ok' if c['ok'] else 'FAIL'}] {c['check']} — "
                 f"{c['detail']}")
    L += ["", f"- controls scope: {d['controls_scope']}", ""]
    if v["window_coverage"]:
        L.append("### Prediction windows")
        L.append("")
        for w in v["window_coverage"]:
            L.append(f"- {w['id']}: window `{w['window']}` — "
                     f"{'recorded offline' if w['known'] else 'NOT RECORDED OFFLINE'}")
        L.append("")
    mech = v["mechanism"] or {}
    L += ["## 2. Proposed mechanism (unverified)", "",
          f"- tag: `{mech.get('tag')}`",
          f"- status: {r.get('mechanism_status')}",
          f"- statement: {mech.get('statement')}", "",
          "## 3. Premises", ""]
    for p in r["premises"]:
        L.append(f"- **{p['id']}** ({p['declared_kind']}): {p['status']}")
        if p.get("supported_statement"):
            L.append(f"  - verified: `{p['supported_statement']}`")
        nar = p.get("narrative") or {}
        if nar.get("text"):
            L.append(f"  - narrative ({nar['status']}, never verified as "
                     f"prose): {nar['text']}")
        for f in nar.get("flags") or []:
            L.append(f"    - flag: {f}")
        for why in p.get("reasons") or []:
            L.append(f"  - {why}")
    L += ["", "## 4. Rivals", ""]
    for rv in v["rivals"] or []:
        L.append(f"- **{rv.get('id')}** ({rv.get('kind')}): {rv.get('text')}")
    L += ["", "## 5. Predictions", ""]
    for p in r["predictions"]:
        L.append(f"- **{p['id']}**: {p['status']}")
        if p.get("canonical"):
            c = p["canonical"]
            L.append(f"  - target `{c['target']}`, metric {c['metric']} "
                     f"{c['relation']} {c['baseline']}")
        for code in p.get("codes") or []:
            L.append(f"  - refusal `{code['code']}`: {code['detail']}")
        for item in p.get("review") or []:
            L.append(f"  - review: {item}")
    L += ["", f"- novelty: {r.get('novelty')}", ""]
    if r["errors"]:
        L += ["## 6. Errors", ""]
        for e in r["errors"]:
            L.append(f"- `{e['code']}` at {e['path']}: {e['detail']}")
        L.append("")
    if r["review_items"]:
        L += ["## 7. Review items", ""] + \
             [f"- {i}" for i in r["review_items"]] + [""]
    L += ["## 8. Kill condition", "", f"> {v['kill_condition']}", ""]
    return "\n".join(L)


def prepare_output(out: Path, dossier_dir: Path, thesis: Path) -> Path:
    out = Path(out).resolve()
    if _inside(out, (ROOT / "data").resolve()):
        raise FrozenError("refusing to write inside data/")
    if _inside(out, Path(dossier_dir).resolve()):
        raise FrozenError("refusing to write inside the frozen dossier")
    if _inside(Path(thesis).resolve(), out):
        raise FrozenError("the thesis file must live outside the output dir")
    if out.exists():
        if not out.is_dir():
            raise FrozenError(f"not a directory: {out}")
        if any(out.iterdir()):
            raise FrozenError(f"output directory is not empty: {out}")
    else:
        out.mkdir(parents=True)
    return out


def execute(dossier_dir: Path, thesis_path: Path, output_dir: Path) -> dict:
    cd = _load_candidate_dossier()
    frozen = read_frozen(dossier_dir)
    built = build_ctx(frozen["dossier"])
    ctx = built["ctx"]

    # The thesis bytes are snapshotted once and frozen on disk BEFORE anything
    # judges them, so what was validated is exactly what was recorded.
    raw_bytes, thesis_obj, thesis_error = cd.load_thesis(Path(thesis_path))
    input_sha = hashlib.sha256(raw_bytes).hexdigest()
    out_dir = prepare_output(output_dir, dossier_dir, thesis_path)
    with open(out_dir / "thesis.input.json", "xb") as fh:
        fh.write(raw_bytes)                                   # verbatim

    problems = []
    if not built["context_verified"]:
        failed = [c["check"] for c in built["checks"] if not c["ok"]]
        problems.append("the frozen context did not verify: " + "; ".join(failed))
    if not built["survivor"]:
        problems.append(
            f"the candidate's pinned discovery status is "
            f"{built['survivor_label']!r}, not {SURVIVOR!r}")
    if thesis_error:
        report = {"schema": th.SCHEMA, "status": "rejected",
                  "errors": [{"code": "thesis_unparseable", "path": "$",
                              "detail": thesis_error}],
                  "premises": [], "rivals": [], "predictions": [],
                  "review_items": [], "thesis_sha256": None,
                  "mechanism_status": "absent"}
        coverage = []
    else:
        report = th.validate(thesis_obj, ctx)
        coverage = window_coverage(thesis_obj, ctx)
    for w in coverage:
        if not w["known"]:
            problems.append(
                f"{w['id']}: window {w['window']!r} is not recorded in the "
                "frozen dossier; offline context cannot judge it")

    contract_v = th.verdict(survivor=built["survivor"],
                            survivor_label=built["survivor_label"],
                            thesis_report=report)
    verdict = effective_verdict(contract_v, problems)
    obj = thesis_obj if isinstance(thesis_obj, dict) else {}
    out = {
        "schema": SCHEMA, "tool_version": TOOL_VERSION,
        "notice": NOTICE,
        "certification_means": CERTIFICATION_MEANS,
        "candidate_hash": ctx["candidate_hash"],
        "candidate_status": built["survivor_label"],
        "survivor": built["survivor"],
        "identity_sha256": ctx["identity_sha256"],
        "evidence_sha256": ctx["evidence_sha256"],
        "thesis_input_sha256": input_sha,
        "thesis_input_bytes": len(raw_bytes),
        "frozen": {"dir": frozen["dir"], "files": frozen["files"],
                   "tool_version": frozen["manifest"].get("tool_version")},
        "context": {"checks": built["checks"],
                    "context_verified": built["context_verified"],
                    "controls_scope": built["controls_scope"],
                    "controls": ctx["controls"],
                    "requires": ctx["requires"],
                    "discovery_symbols": ctx["discovery_symbols"],
                    "cut_ms": ctx["cut_ms"]},
        "window_coverage": coverage,
        "mechanism": obj.get("mechanism"),
        "rivals": obj.get("rivals"),
        "kill_condition": obj.get("kill_condition"),
        "thesis_report": report,
        "verdict": verdict,
        "contract_verdict": contract_v,
        "certified": not problems,
        "certification_problems": problems,
        "reads": {"database": "none", "network": "none",
                  "heldout": "none", "gate_writes": "none",
                  "budget_spent": "none"},
    }

    with open(out_dir / "validation.json", "x") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
        fh.write("\n")
    with open(out_dir / "dossier.validated.md", "x") as fh:
        fh.write(render_markdown(out))
    manifest = {
        "schema": "luffy.thesis_validation_manifest.v2",
        "tool": "scripts/thesis_validate_frozen.py",
        "tool_version": TOOL_VERSION,
        "tool_sha256": sha256_file(Path(__file__)),
        "contract_sha256": sha256_file(ROOT / "trader/research/thesis.py"),
        "dossier_tool_sha256": sha256_file(ROOT / "scripts/candidate_dossier.py"),
        "candidate_hash": ctx["candidate_hash"],
        "candidate_status": built["survivor_label"],
        "identity_sha256": ctx["identity_sha256"],
        "evidence_sha256": ctx["evidence_sha256"],
        "thesis_input_sha256": input_sha,
        "thesis_sha256": report.get("thesis_sha256"),
        "frozen_dossier_dir": frozen["dir"],
        "frozen_dossier_files": {n: r["recorded"]
                                 for n, r in frozen["files"].items()},
        "thesis_status": report["status"],
        "verdict_state": verdict["state"],
        "contract_verdict_state": contract_v["state"],
        "certified": not problems,
        "certification_means": CERTIFICATION_MEANS,
        "files": {n: sha256_file(out_dir / n) for n in OUTPUT_FILES
                  if n != "manifest.json"},
        "not_read": ["data/luffy.db", "data/candles.db", "data/derivs.db",
                     "research_tests", "research_candidates", "held-out bars",
                     "network"],
    }
    with open(out_dir / "manifest.json", "x") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return {"validation": out, "manifest": manifest, "dir": out_dir}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dossier", required=True, type=Path,
                    help="a frozen candidate_dossier.py output directory")
    ap.add_argument("--thesis", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    a = ap.parse_args(argv)
    try:
        res = execute(a.dossier, a.thesis, a.output_dir)
    except FrozenError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    v = res["validation"]
    print(f"wrote {res['dir']}")
    print(f"  candidate     : {v['candidate_hash']} ({v['candidate_status']})")
    print(f"  thesis status : {v['thesis_report']['status']}")
    print(f"  verdict       : {v['verdict']['state']}")
    print(f"  certified     : {v['certified']} (context integrity only)")
    for p in v["certification_problems"]:
        print(f"  problem       : {p}")
    for e in v["thesis_report"]["errors"]:
        print(f"  error         : {e['code']} {e['path']}: {e['detail']}")
    for i in v["thesis_report"]["review_items"]:
        print(f"  review        : {i}")
    ok = v["certified"] and v["thesis_report"]["status"] == "well_formed_untested"
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
