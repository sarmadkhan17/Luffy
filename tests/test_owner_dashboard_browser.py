"""W2 (revision 5): cross-tab unresolved request ids, in a real browser.

Headless Chromium via Playwright; two pages of one origin in one browser
context share real localStorage. localStorage is NOT assumed to be locked:
exact interleavings are forced by tab A opening tab B (window.open, same
origin, same renderer) and hooking A's *own* Storage writes so that, right
before A writes (after A has read), B's code runs synchronously — the
lost-update window that parallel tabs have in practice.

The page is the real dashboard owner-control block (index.html) with stubbed
gql/toast/refresh. Every scenario also runs against the revision-4 block
(tests/fixtures/owner_block_rev4.js, whole-map read-modify-write), which
must fail it: the negative controls.

Assertions are implementation-independent: an id that a tab SENT and that
never got a definitive answer must be recoverable by a freshly loaded tab
(a reload), which re-sends exactly that id.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "trader" / "dashboard" / "web" / "index.html").read_text()
CURRENT = HTML[HTML.index("/* Owner controls: typed requests executed by the kernel"):
               HTML.index("/* ── refresh ── */")]
REV4 = (ROOT / "tests" / "fixtures" / "owner_block_rev4.js").read_text()
ORIGIN = "http://luffy.test/"

sync_api = pytest.importorskip("playwright.sync_api")

PAGE = """<!doctype html><html><body><button class="owner-op owner-rec"></button><script>
window.__calls = []; window.__toasts = []; window.__pending = {}; window.__mode = 'hold';
window.__allow = true;
window.confirm = () => window.__allow;
function toast(m) { window.__toasts.push(m); }
function refresh() {}
const RES = s => ({res: {status: s, message: s, replayed: false, reasons: []}});
function gql(q, v) {
  const op = v.o || (q.includes('panic(') ? 'panic' : 'close_trade');
  window.__calls.push({r: v.r, op});
  if (window.__mode === 'fail') return Promise.reject(new Error('network'));
  if (window.__mode === 'ok') return Promise.resolve(RES('ACCEPTED'));
  return new Promise(res => { (window.__pending[v.r] = window.__pending[v.r] || []).push(res); });
}
window.__resolve = (id, status) => (window.__pending[id] || []).splice(0).forEach(r => r(RES(status)));
/* Run fn synchronously immediately BEFORE this tab's next Storage write
   (setItem/removeItem): whatever this tab read earlier is then stale — the
   lost-update window of two tabs running in parallel. Only this realm is hooked. */
window.__interleave = fn => {
  const P = Storage.prototype, set = P.setItem, rm = P.removeItem;
  let fired = false;
  const fire = () => { if (!fired) { fired = true; P.setItem = set; P.removeItem = rm; fn(); } };
  P.setItem = function (k, v) { fire(); return set.call(this, k, v); };
  P.removeItem = function (k) { fire(); return rm.call(this, k); };
};
</script><script>
OWNER_BLOCK
</script></body></html>"""


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


class Tabs:
    """One browser context (one shared localStorage) serving a given block."""

    def __init__(self, browser, block):
        self.ctx = browser.new_context()
        html = PAGE.replace("OWNER_BLOCK", block)
        self.ctx.route(ORIGIN + "**", lambda route: route.fulfill(
            status=200, content_type="text/html", body=html))

    def new(self):
        page = self.ctx.new_page()
        page.goto(ORIGIN)
        return page

    def opened_by(self, page):
        """B opened by A (A holds window.__peer: same origin, synchronous access)."""
        with self.ctx.expect_page() as info:
            page.evaluate("window.__peer = window.open(location.href)")
        peer = info.value
        peer.wait_for_load_state()
        return peer

    def close(self):
        self.ctx.close()


def sent(page):
    return [c["r"] for c in page.evaluate("window.__calls")]


def click(page, op, *, allow=True):
    page.evaluate(f"window.__allow = {json.dumps(allow)}; ownerControl({json.dumps(op)}); 0")
    page.wait_for_timeout(30)


def recovered(tabs, op, limit=4):
    """The ids a freshly loaded tab (a reload) re-sends for `op`, oldest first;
    each answer is definitive, so the next click moves on to the next one."""
    fresh = tabs.new()
    fresh.evaluate("window.__mode = 'ok'; window.__allow = true")
    for _ in range(limit):
        fresh.evaluate(f"ownerControl({json.dumps(op)})")
        fresh.wait_for_timeout(30)
    return sent(fresh)


def unresolved_recoverable(tabs, op, ids):
    """Every id in `ids` is re-sent by a fresh tab before any new id appears."""
    got = recovered(tabs, op, limit=len(ids) + 2)
    return set(ids) <= set(got)


# ── scenarios (each returns normally or raises AssertionError) ──────────────
def s1_different_actions_and_reload(browser, block):
    tabs = Tabs(browser, block)
    try:
        a, b = tabs.new(), tabs.new()
        click(a, "panic")                                  # unresolved in A
        click(b, "halt")                                   # unresolved in B
        ida, idb = sent(a)[0], sent(b)[0]
        assert unresolved_recoverable(tabs, "panic", [ida])
        assert unresolved_recoverable(tabs, "halt", [idb])
    finally:
        tabs.close()


def s2_completion_never_erases_sibling(browser, block):
    tabs = Tabs(browser, block)
    try:
        a = tabs.new()
        click(a, "halt")                                   # A: unresolved halt
        b = tabs.opened_by(a)
        idh = sent(a)[0]
        # A completes its halt; inside A's completion bookkeeping, B creates a panic
        a.evaluate("window.__interleave(() => window.__peer.ownerControl('panic'))")
        a.evaluate(f"window.__resolve({json.dumps(idh)}, 'ACCEPTED')")
        a.wait_for_timeout(50)
        idp = sent(b)[0]
        assert unresolved_recoverable(tabs, "panic", [idp])
    finally:
        tabs.close()


def s3_simultaneous_creation_same_action(browser, block):
    tabs = Tabs(browser, block)
    try:
        a = tabs.new()
        b = tabs.opened_by(a)
        a.evaluate("window.__interleave(() => window.__peer.ownerControl('freeze'))")
        click(a, "freeze")                                 # B creates inside A's read
        ids = set(sent(a)) | set(sent(b))
        assert ids and unresolved_recoverable(tabs, "freeze", ids)
    finally:
        tabs.close()


def s4_stale_tab_never_overwrites_newer_storage(browser, block):
    tabs = Tabs(browser, block)
    try:
        a = tabs.new()
        b = tabs.opened_by(a)
        click(b, "halt")                                   # B: unresolved halt (older)
        # A starts creating a panic from its read; meanwhile B writes a newer request
        a.evaluate("window.__interleave(() => window.__peer.ownerControl('freeze'))")
        click(a, "panic")
        assert unresolved_recoverable(tabs, "halt", [sent(b)[0]])
        assert unresolved_recoverable(tabs, "freeze", [sent(b)[1]])
        assert unresolved_recoverable(tabs, "panic", [sent(a)[0]])
    finally:
        tabs.close()


def s5_double_click_and_reconnect_reuse(browser, block):
    tabs = Tabs(browser, block)
    try:
        a = tabs.new()
        click(a, "halt")
        click(a, "halt")                                   # double-click while unresolved
        a.evaluate("window.__mode = 'fail'")
        click(a, "halt")                                   # reconnect attempts
        ids = sent(a)
        assert len(set(ids)) == 1, ids
        assert unresolved_recoverable(tabs, "halt", ids[:1])
    finally:
        tabs.close()


SCENARIOS = [s1_different_actions_and_reload, s2_completion_never_erases_sibling,
             s3_simultaneous_creation_same_action,
             s4_stale_tab_never_overwrites_newer_storage, s5_double_click_and_reconnect_reuse]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__)
def test_w2_current_dashboard(browser, scenario):
    scenario(browser, CURRENT)


# Negative controls: the revision-4 whole-map block must fail the interleaved
# scenarios (old whole-map overwrite / completion erases sibling / simultaneous
# creation loses an id / stale tab overwrites newer storage).
@pytest.mark.parametrize("scenario", [s2_completion_never_erases_sibling,
                                      s3_simultaneous_creation_same_action,
                                      s4_stale_tab_never_overwrites_newer_storage],
                         ids=lambda f: f.__name__)
def test_negative_control_w2_rev4_whole_map(browser, scenario):
    with pytest.raises(AssertionError):
        scenario(browser, REV4)


def test_negative_control_w2_sibling_erasing_completion(browser):
    """A completion that clears every entry of its action erases a sibling tab's id."""
    mutant = CURRENT.replace(
        "if(r&&OWNER_DEFINITIVE.includes(r.status))ownerDone(p.id);",
        "if(r&&OWNER_DEFINITIVE.includes(r.status)){ownerDone(p.id);"
        "ownerEntries().forEach(e=>ownerDone(e.id));}")
    assert mutant != CURRENT
    with pytest.raises(AssertionError):
        s2_completion_never_erases_sibling(browser, mutant)


# R6: typed unresolved replies must survive reload with their original id.
def check_typed_result_reload(browser, block, status):
    tabs = Tabs(browser, block)
    try:
        page = tabs.new()
        click(page, "halt")
        original = sent(page)[0]
        page.evaluate(f"window.__resolve({json.dumps(original)}, {json.dumps(status)})")
        page.wait_for_timeout(30)
        fresh = tabs.new()                       # a reload sharing real localStorage
        fresh.evaluate("window.__mode = 'ok'")
        click(fresh, "halt")
        retried = sent(fresh)[0]
        if status in ("IN_PROGRESS", "OUTCOME_UNKNOWN"):
            if retried != original:
                raise AssertionError((original, retried))
        else:
            assert retried != original, (original, retried)
    finally:
        tabs.close()


@pytest.mark.parametrize("status", ["IN_PROGRESS", "OUTCOME_UNKNOWN", "REFUSED", "ACCEPTED"])
def test_r6_typed_result_reload(browser, status):
    check_typed_result_reload(browser, CURRENT, status)


@pytest.mark.parametrize("old,new", [
    ("'ALREADY_SET','REFUSED'", "'ALREADY_SET','REFUSED','OUTCOME_UNKNOWN'"),
    ("let p=ownerGet(action);", "let p=null;"),
], ids=["clear_unknown", "fresh_id_on_retry"])
def test_r6_negative_control_unresolved_id(browser, old, new):
    assert old in CURRENT
    mutant = CURRENT.replace(old, new, 1)
    with pytest.raises(AssertionError) as caught:
        check_typed_result_reload(browser, mutant, "OUTCOME_UNKNOWN")
    # Semantic failure: two different request ids, not a JS/runtime failure.
    original, retried = caught.value.args[0]
    assert original != retried
