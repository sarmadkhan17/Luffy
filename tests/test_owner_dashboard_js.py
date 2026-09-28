"""Dashboard owner controls, executed: the real JS block from index.html runs
under Node with stubbed gql / localStorage / confirm / DOM (Astra H, W6, W8).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "trader" / "dashboard" / "web" / "index.html").read_text()
NODE = shutil.which("node") or shutil.which("nodejs")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _owner_block() -> str:
    start = HTML.index("/* Owner controls: typed requests executed by the kernel")
    return HTML[start:HTML.index("/* ── refresh ── */")]


HARNESS = r"""
const vm = require('vm');
const store = {};
globalThis.localStorage = {getItem: k => (k in store ? store[k] : null),
                           setItem: (k, v) => { store[k] = String(v); },
                           removeItem: k => { delete store[k]; },
                           key: i => Object.keys(store)[i] ?? null,
                           get length() { return Object.keys(store).length; }};
if (!globalThis.crypto) globalThis.crypto = require('crypto').webcrypto;
let confirms = 0; globalThis.confirm = () => { confirms++; return true; };
const toasts = []; globalThis.toast = (m, err) => toasts.push([m, !!err]);
globalThis.refresh = () => {};
const buttons = [{disabled: false}, {disabled: false}];
globalThis.document = {querySelectorAll: sel => sel === '.owner-rec' ? buttons : []};
const calls = []; let plan = () => null;
globalThis.gql = (q, v) => { calls.push({op: v.o || (q.includes('panic(') ? 'panic' : 'close_trade'),
                                         r: v.r, t: v.t}); return plan(q, v); };
vm.runInThisContext(OWNER_BLOCK);
const ok = s => ({res: {status: s, message: s, replayed: false, reasons: []}});
(async () => {
  const out = {};
  // 1. no answer twice → unknown; the next click re-sends the SAME id and time, no new consent
  plan = () => Promise.reject(new Error('network'));
  await ownerControl('halt');
  const first = calls.slice();
  plan = () => Promise.resolve(ok('ACCEPTED'));
  await ownerControl('halt');
  out.unknown = {confirms, calls: calls.slice(), toasts: toasts.slice(),
                 pending_after: Object.keys(store).filter(k => k.startsWith('luffy.owner.req.'))};
  // 2. while a resume is in flight, freeze/halt/panic still go out; a 2nd resume does not
  calls.length = 0; toasts.length = 0;
  let release; const held = new Promise(r => { release = r; });
  plan = (q, v) => v.o === 'resume' ? held.then(() => ok('CONTAINED')) : Promise.resolve(ok('ACCEPTED'));
  const resume = ownerControl('resume');
  await new Promise(r => setTimeout(r, 10));
  out.rec_buttons_disabled = buttons.every(b => b.disabled);
  await ownerControl('freeze'); await ownerControl('halt'); await ownerControl('panic');
  await ownerControl('unhalt');
  out.during = calls.map(c => c.op);
  out.second_recovery_toast = toasts.some(([m]) => m.includes('already running'));
  release(); await resume;
  out.rec_buttons_after = buttons.every(b => !b.disabled);
  // 3. an unknown close keeps its id per trade; a different trade gets its own id
  calls.length = 0;
  plan = () => Promise.resolve(null);
  await closeTrade('pos_1', 'UNI'); await closeTrade('pos_1', 'UNI'); await closeTrade('pos_2', 'SOL');
  out.close = calls.map(c => [c.op, c.r]);
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def run():
    script = HARNESS.replace("OWNER_BLOCK", json.dumps(_owner_block()))
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_unknown_outcome_retry_reuses_the_original_id_and_time(run):
    calls = run["unknown"]["calls"]
    assert [c["op"] for c in calls] == ["halt"] * 3            # 2 failed attempts + retry
    assert len({c["r"] for c in calls}) == 1 and len({c["t"] for c in calls}) == 1
    assert run["unknown"]["confirms"] == 1                      # retry needs no new consent
    assert any("outcome unknown" in m for m, _ in run["unknown"]["toasts"])
    assert run["unknown"]["pending_after"] == []                # cleared on a definitive answer


def test_containment_is_never_blocked_by_a_recovery_in_flight(run):
    assert run["rec_buttons_disabled"] is True
    assert run["during"] == ["resume", "freeze", "halt", "panic"]   # no second recovery sent
    assert run["second_recovery_toast"] is True
    assert run["rec_buttons_after"] is True


def test_unresolved_close_is_keyed_per_trade(run):
    (op1, r1), (op2, r2), (op3, r3) = run["close"][0], run["close"][2], run["close"][4]
    assert op1 == op2 == op3 == "close_trade"
    assert r1 == r2 and r3 != r1


def test_refresh_has_no_removed_buttons_and_drops_stale_responses():
    body = HTML[HTML.index("async function refresh(){"):HTML.index("// ── live position cards ──")]
    for gone in ("#b-active", "#b-frozen", "#b-halted", "'active','frozen','halted'"):
        assert gone not in body
    assert "seq<refreshShown" in body and "refreshShown=seq" in body
    assert "set_control_state" not in HTML and "actor" not in _owner_block()


JS_MUTANTS = {
    # a fresh id per click (the reviewed defect)
    "new_id_per_click": ("let p=ownerGet(action);", "let p=null;",
                         test_unknown_outcome_retry_reuses_the_original_id_and_time),
    # containment disabled while a recovery runs (the reviewed defect)
    "block_containment": ("if(recovery&&recoveryInFlight)", "if(recoveryInFlight)",
                          test_containment_is_never_blocked_by_a_recovery_in_flight),
}


@pytest.mark.parametrize("name", sorted(JS_MUTANTS))
def test_negative_control_js_mutant_is_caught(name):
    old, new, check = JS_MUTANTS[name]
    block = _owner_block()
    assert old in block
    script = HARNESS.replace("OWNER_BLOCK", json.dumps(block.replace(old, new, 1)))
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    with pytest.raises(AssertionError):
        check(json.loads(out.stdout))
