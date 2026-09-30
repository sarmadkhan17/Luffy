"""Owner Interface boundaries: chat safety, cross-process authority, secrets.

Plus negative controls: deliberately broken mutants that the checks here and
in tests/test_owner_interface.py must catch.
"""
import ast
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from trader.core.journal import Journal
from trader.owner.authz import Authorizer
from trader.owner.contract import OwnerRequest, Status, new_request_id

ROOT = Path(__file__).resolve().parents[1]
PHRASES = ["what would make you halt?", "explain freeze", "resume explaining this",
           "what does panic mode do?", "should I unhalt?", "freeze entries",
           "please freeze entries", "halt", "resume", "unhalt now", "panic",
           "close all", "go live", "stop trading", "FREEZE!", "/freeze entries please",
           "can you /halt", "  CONFIRM  ", "confirm abcdef"]


def _journal(tmp_path):
    return Journal(tmp_path / "j.db")


def _controls(j):
    return (j.kv_get("control_state"), j.kv_get("panic_requested"),
            j.query("SELECT COUNT(*) n FROM control_events")[0]["n"])


# ═══ 13. chat safety ════════════════════════════════════════════════════════
@pytest.mark.parametrize("phrase", PHRASES)
def test_chat_engine_phrases_make_zero_control_requests(tmp_path, monkeypatch, phrase):
    from trader.chat.engine import ChatEngine
    j = _journal(tmp_path)
    before = _controls(j)
    monkeypatch.setattr("trader.chat.agent.AnalystAgent.run",
                        lambda self, m, h=None: "ANSWER")
    from tests.test_chat_agent import _cfg
    assert ChatEngine(j, _cfg()).handle(phrase) == "ANSWER"
    assert _controls(j) == before == (None, None, 0)


class RecordingClient:
    def __init__(self):
        self.calls = []

    def call(self, req):
        self.calls.append(req)
        from trader.owner.contract import OwnerResult
        return OwnerResult(req.request_id, req.operation, Status.ACCEPTED, data={})


@pytest.mark.parametrize("phrase", PHRASES)
def test_openclaw_conversation_makes_zero_control_requests(phrase):
    from trader.owner.adapters.openclaw import OpenClawAdapter
    authz = Authorizer.from_config(
        {"owner_interface": {"identities": {"whatsapp": {"+15550000001": "owner"}}}})
    client, said = RecordingClient(), []
    a = OpenClawAdapter(authz, {"whatsapp": client},
                        conversation=lambda t: said.append(t) or "chat")
    out = a.handle({"provider": "whatsapp", "message_id": "m1", "sender": "+15550000001",
                    "text": phrase, "timestamp": time.time()})
    assert out.request is None and out.kind in ("conversation", "refused")
    assert client.calls == []


@pytest.mark.parametrize("phrase", PHRASES)
def test_telegram_free_text_is_not_a_command(phrase):
    """Only a message that starts with an exact slash command is one."""
    from trader.owner.adapters import telegram
    expected = "freeze" if phrase == "/freeze entries please" else None
    assert telegram.command_of(phrase) == expected


def test_chat_package_cannot_reach_control_authority():
    for path in (ROOT / "trader" / "chat").glob("*.py"):
        src = path.read_text()
        for forbidden in ("ControlStateMachine", "engine.state", "engine.supervisor",
                          "owner.service", "owner.ipc", "OwnerClient", "panic_requested",
                          "set_control_state", "_set_state", "kv_set"):
            assert forbidden not in src, (path.name, forbidden)


def test_dashboard_chat_endpoint_is_never_operational():
    src = (ROOT / "trader" / "dashboard" / "server.py").read_text()
    body = src[src.index('@app.post("/api/chat")'):src.index('@app.post("/api/brain/autopsy")')]
    assert "ChatEngine(journal, cfg).handle(msg, history)" in body
    assert "do_ops" not in body and "owner" not in body.replace("never operational", "")


# ═══ 14. cross-process safety ═══════════════════════════════════════════════
ADAPTER_SIDE = ["trader/owner/adapters", "trader/owner/contract.py", "trader/owner/authz.py",
                "trader/owner/ipc.py", "trader/owner/cli.py", "trader/api/graphql_schema.py",
                "trader/chat"]
FORBIDDEN_IMPORTS = ("trader.engine.state", "trader.engine.supervisor", "trader.engine.risk",
                     "trader.engine.executor", "trader.engine.reconcile",
                     "trader.engine.protective", "trader.data.feed", "trader.kernel",
                     "trader.owner.service", "ccxt")
FORBIDDEN_NAMES = ("ControlStateMachine", "Supervisor", "RiskManager", "Executor",
                   "make_exchange", "binance_keys", "set_if_current", "_set_fenced")


def _files(rel):
    p = ROOT / rel
    return [p] if p.is_file() else sorted(p.rglob("*.py"))


def _imports(path):
    pkg = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = pkg.split(".")[:len(pkg.split(".")) - node.level + 1]
                base = ".".join(parts + ([base] if base else []))
            out.add(base)
            out |= {f"{base}.{a.name}" for a in node.names}
    return out


@pytest.mark.parametrize("rel", ADAPTER_SIDE)
def test_adapter_side_code_cannot_import_execution_authority(rel):
    for path in _files(rel):
        imports = _imports(path)
        bad = {i for i in imports for f in FORBIDDEN_IMPORTS if i == f or i.startswith(f + ".")}
        assert not bad, (path.relative_to(ROOT), sorted(bad))
        names = {n.id for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(ast.parse(path.read_text()))
                  if isinstance(n, ast.Attribute)}
        assert not names & set(FORBIDDEN_NAMES), (path.relative_to(ROOT),
                                                  sorted(names & set(FORBIDDEN_NAMES)))


def test_dashboard_owner_gateway_holds_no_authority():
    import inspect
    from trader.dashboard import server
    src = inspect.getsource(server._owner_gateway)
    for forbidden in FORBIDDEN_NAMES + ("state_machine", "kv_set", "control_state",
                                        "log_control_event", "Env"):
        assert forbidden not in src, forbidden
    assert "client.call(req)" in src


def test_graphql_has_no_direct_control_setter():
    from trader.api.graphql_schema import make_graphql_router
    schema = make_graphql_router(_journal_mem()).schema
    fields = {f.name for f in schema.get_type_by_name("Mutation").fields}
    assert "set_control_state" not in fields and "owner_control" in fields
    src = (ROOT / "trader" / "api" / "graphql_schema.py").read_text()
    assert "ControlStateMachine" not in src and 'kv_set("control_state"' not in src


def _journal_mem():
    import tempfile
    d = tempfile.mkdtemp(prefix="oi-gql-")
    return Journal(Path(d) / "j.db")


def test_adapter_import_graph_loads_no_execution_modules():
    code = ("import sys; import trader.owner.adapters.telegram, trader.owner.adapters.dashboard,"
            " trader.owner.adapters.openclaw, trader.owner.ipc, trader.owner.authz;"
            " bad=[m for m in sys.modules if m in (%r)]; print(bad)" % (FORBIDDEN_IMPORTS,))
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_only_owner_service_executes_owner_transitions():
    """Outside trader/engine and the kernel's own risk/panic/macro paths, the
    only module that applies a control transition is trader/owner/service.py."""
    hits = []
    for path in (ROOT / "trader").rglob("*.py"):
        rel = str(path.relative_to(ROOT))
        if rel.startswith("trader/engine/") or rel == "trader/kernel.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                    node.func.attr in ("apply", "set", "set_if_current") and \
                    any("ControlState." in ast.unparse(a) for a in node.args):
                hits.append(rel)
    assert sorted(set(hits)) == ["trader/owner/service.py"], hits
    src = (ROOT / "trader" / "owner" / "service.py").read_text()
    assert "ControlState.ACTIVE" not in src           # activation only via the Supervisor


def test_kernel_telegram_owner_branch_uses_the_gateway_only():
    import inspect
    from trader.kernel import Kernel
    src = inspect.getsource(Kernel._handle_tg_command)
    assert "service.execute(" in src
    for forbidden in ("state_machine", "ControlState", "owner_resume", "OwnerContext",
                      "panic_requested", "kv_set"):
        assert forbidden not in src, forbidden


# ═══ 16. secret boundary ════════════════════════════════════════════════════
SECRET_MARKERS = ("os.environ", "getenv", "Env.", "dotenv", ".env", "BINANCE", "binance",
                  "API_KEY", "API_SECRET", "TELEGRAM_TOKEN", "bot_token", "DASH_TOKEN")


@pytest.mark.parametrize("path", sorted((ROOT / "trader" / "owner").rglob("*.py")),
                         ids=lambda p: str(p.relative_to(ROOT)))
def test_owner_package_reads_no_secrets(path):
    src = path.read_text()
    for marker in SECRET_MARKERS:
        assert marker not in src, (path.name, marker)


def test_audit_and_records_carry_no_secrets(tmp_path, monkeypatch):
    """Full Telegram → OwnerService flow with sentinel secrets in the environment."""
    from tests.test_kernel_boot_recovery import _kernel, _protected
    from tests.test_owner_recovery import HookVenue
    for var in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "TELEGRAM_TOKEN", "DASH_TOKEN"):
        monkeypatch.setenv(var, f"SENTINEL-{var}")
    journal, venue = Journal(tmp_path / "j.db"), HookVenue()
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.boot()
    k.notifier.chat_id, k.notifier.token = "1", "SENTINEL-TELEGRAM_TOKEN"
    monkeypatch.setattr("requests.post", lambda *a, **kw: None)
    base = "https://api.telegram.org/botSENTINEL-TELEGRAM_TOKEN"
    from tests.test_owner_recovery_risk_guard import tg_update
    for cmd in ("/freeze", "/resume", "/halt", "/unhalt", "/status", "/panic"):
        k._handle_tg_command(cmd, base, update=tg_update())
    dump = json.dumps([dict(r) for r in journal.query("SELECT * FROM control_events")] +
                      [dict(r) for r in journal.query("SELECT * FROM owner_requests")] +
                      [dict(r) for r in journal.query("SELECT * FROM owner_audit")])
    assert "owner_interface_result" in dump
    assert "SENTINEL" not in dump


# ═══ negative controls: the checks must catch broken implementations ═══════
def _mutant_no_idempotency(service):
    """Every delivery gets a fresh reservation: the record is ignored."""
    service._existing = lambda req, principal: None
    real = service.reserve

    def reserve(req, **kw):
        with service.journal._tx() as c:
            c.execute("DELETE FROM owner_requests WHERE request_id=?", (req.request_id,))
        return real(req, **kw)
    service.reserve = reserve


def _mutant_direct_resume(service):
    from trader.core.types import ControlState

    def direct(req, principal):
        if req.operation in ("resume", "unhalt"):
            with service.state_machine.fenced() as f:
                before = f.state
                f.apply(ControlState.ACTIVE, "operator", "mutant")
            from trader.owner.contract import OwnerResult
            return OwnerResult(req.request_id, req.operation, Status.ACTIVATED,
                               control_state_before=before.value,
                               control_state_after="ACTIVE")
        return orig(req, principal)
    orig = service._execute
    service._execute = direct


def _mutant_no_authz(service):
    service.authorizer.authorize = lambda channel, identity, op: ("tester", None)


def _mutant_no_stale_check(service):
    service.max_age_s = float("inf")
    service.future_skew_s = float("inf")


def _mutant_conflict_on_unresolved(service):
    """The reviewed defect: a same-boot unresolved claim answered as a conflict."""
    from trader.owner.contract import refused
    real = service._existing

    def existing(req, principal):
        row = service._row(req.request_id)
        if row is not None and row["state"] == "PENDING":
            return refused(req, "request_id_conflict")
        return real(req, principal)
    service._existing = existing


@pytest.mark.parametrize("check, mutant", [
    ("test_h_duplicate_delivery_executes_once", _mutant_no_idempotency),
    ("test_q_telegram_duplicate_stale_and_malformed_updates", _mutant_no_idempotency),
    ("test_e_guarded_resume_refused_stays_contained", _mutant_direct_resume),
    ("test_g_explicit_unhalt_risk_refused", _mutant_direct_resume),
    ("test_k_unbound_identity_or_grant_never_executes_or_claims", _mutant_no_authz),
    ("test_w4_telegram_foreign_sender_in_owner_chat_is_refused", _mutant_no_authz),
    ("test_j_stale_future_and_missing_time", _mutant_no_stale_check),
    ("test_w8_result_persistence_failure_is_outcome_unknown_not_conflict",
     _mutant_conflict_on_unresolved),
])
def test_negative_control_mutant_is_caught(tmp_path, monkeypatch, check, mutant):
    import tests.test_owner_interface as matrix
    from trader.kernel import Kernel
    real = Kernel._owner

    def mutated(self):
        fresh = getattr(self, "_owner_service", None) is None
        s = real(self)
        if fresh:
            mutant(s)
        return s
    monkeypatch.setattr(Kernel, "_owner", mutated)
    journal = Journal(tmp_path / "j.db")
    from tests.test_owner_recovery import HookVenue
    with pytest.raises(AssertionError):
        getattr(matrix, check)((journal, HookVenue()), monkeypatch)


def _run_check(check, tmp_path, monkeypatch, **fixtures):
    import tempfile
    import tests.test_owner_interface as matrix
    fn = getattr(matrix, check)
    kwargs = {}
    for name in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
        kwargs[name] = {"tmp_path": tmp_path, "monkeypatch": monkeypatch,
                        "ipc_dir": Path(tempfile.mkdtemp(prefix="oi-")) / "ipc",
                        "world": (Journal(tmp_path / "w.db"),
                                  __import__("tests.test_owner_recovery",
                                             fromlist=["x"]).HookVenue()),
                        **fixtures}[name]
    return fn(**kwargs)


def _old_code_only_confirmations(monkeypatch):
    """Reviewed defect: pending keyed by code alone, setdefault keeps the first op."""
    from trader.owner.adapters import openclaw as oc
    real_handle = oc.OpenClawAdapter.handle

    def handle(self, raw):
        out = real_handle(self, raw)
        if out.kind == "refused" and out.text and "collision" in out.text:
            ev = oc.Inbound.parse(raw)
            code = self._code(ev.provider, ev.sender, ev.message_id)
            return oc.Outbound(f"Confirm {oc.COMMANDS[ev.text.strip().lower()].upper()}: "
                               f"reply  CONFIRM {code}", "proposal")
        return out
    monkeypatch.setattr(oc.OpenClawAdapter, "handle", handle)


def _accept_any_channel_mac(monkeypatch):
    from trader.owner import ipc
    monkeypatch.setattr(ipc.hmac, "compare_digest", lambda a, b: True)


def _chat_is_the_identity(monkeypatch):
    from trader.owner.adapters import telegram
    real = telegram.to_request

    def to_request(text, update, *, chat_id):
        req = real(text, update, chat_id=chat_id)
        return telegram.OwnerRequest(**{**req.__dict__, "identity": str(chat_id)})
    monkeypatch.setattr(telegram, "to_request", to_request)


def _post_send_failure_is_unavailable(monkeypatch):
    from trader.owner import ipc
    from trader.owner.contract import refused as _refused
    real = ipc.OwnerClient.call

    def call(self, req):
        r = real(self, req)
        if r.status == Status.ERROR:
            return _refused(req, "kernel_unavailable", Status.UNAVAILABLE)
        return r
    monkeypatch.setattr(ipc.OwnerClient, "call", call)


def _telegram_inline(monkeypatch):
    from trader.kernel import Kernel
    monkeypatch.setattr(Kernel, "_tg_dispatch",
                        lambda self, msg, base, update: self._handle_tg_command(
                            msg, base, update=update))


def _unlink_whatever_is_there(monkeypatch):
    import os as _os
    from trader.owner import ipc
    real = ipc.OwnerIPCServer.stop

    def stop(self):
        real(self)
        try:
            _os.unlink(self.path)
        except OSError:
            pass
    monkeypatch.setattr(ipc.OwnerIPCServer, "stop", stop)


def _meta_fails_open(monkeypatch):
    from trader.owner import contract
    real = contract._check_meta
    monkeypatch.setattr(contract, "_check_meta",
                        lambda meta: {} if not meta else real(meta))


def _one_shared_recovery_pool(monkeypatch):
    from trader.owner import ipc
    real = ipc._class_of
    monkeypatch.setattr(ipc, "_class_of", lambda op: "recovery")


@pytest.mark.parametrize("check, mutant", [
    ("test_w1_code_collision_can_never_swap_the_confirmed_operation",
     _old_code_only_confirmations),
    ("test_w3_channel_is_authenticated_not_asserted", _accept_any_channel_mac),
    ("test_w4_telegram_foreign_sender_in_owner_chat_is_refused", _chat_is_the_identity),
    ("test_w8_failure_after_delivery_is_outcome_unknown", _post_send_failure_is_unavailable),
    ("test_w7_telegram_halt_is_not_serialized_behind_resume", _telegram_inline),
    ("test_w9_stop_never_unlinks_a_replacement_socket", _unlink_whatever_is_there),
    ("test_w7_ipc_class_slots_reserve_containment", _one_shared_recovery_pool),
])
def test_negative_control_review_counterexamples_are_caught(tmp_path, monkeypatch, check,
                                                            mutant):
    mutant(monkeypatch)
    with pytest.raises((AssertionError, StopIteration)):
        _run_check(check, tmp_path, monkeypatch)


@pytest.mark.parametrize("patch", [{"meta": []}, {"meta": False}, {"meta": 0}, {"meta": ""}])
def test_negative_control_meta_fail_open_is_caught(monkeypatch, patch):
    import tests.test_owner_interface as matrix
    _meta_fails_open(monkeypatch)
    with pytest.raises(pytest.fail.Exception):
        matrix.test_m_malformed_payload_is_refused(patch)


def test_negative_control_old_chat_ops_path_is_caught(tmp_path, monkeypatch):
    """The pre-gateway ChatEngine.handle (regex → set state) fails chat safety."""
    import re
    from trader.chat.engine import ChatEngine
    from trader.core.types import ControlState
    from trader.engine.state import ControlStateMachine

    def old_handle(self, message, history=None):
        m = message.lower()
        for pat, state in ((r"\bfreeze\b", "FROZEN"), (r"\bhalt\b", "HALTED"),
                           (r"\bresume\b", "ACTIVE")):
            if re.search(pat, m):
                ControlStateMachine(self.journal).set(ControlState(state), "chat")
                return state
        return "ANSWER"
    monkeypatch.setattr(ChatEngine, "handle", old_handle)
    caught = 0
    for phrase in ("what would make you halt?", "explain freeze", "freeze entries"):
        d = tmp_path / f"c{caught}"
        d.mkdir()
        with pytest.raises(AssertionError):
            test_chat_engine_phrases_make_zero_control_requests(d, monkeypatch, phrase)
        caught += 1
    assert caught == 3


def test_negative_control_direct_dashboard_setter_is_caught(tmp_path, monkeypatch):
    """A resurrected set_control_state-style gateway fails the authority check."""
    import inspect
    from trader.dashboard import server

    def _owner_gateway(cfg, auth):
        from trader.engine.state import ControlStateMachine  # noqa: F401 — the mutant

        def gateway(operation, request_id, request):
            ControlStateMachine(None).set(None, "dashboard")
        return gateway
    monkeypatch.setattr(server, "_owner_gateway", _owner_gateway)
    with pytest.raises(AssertionError):
        test_dashboard_owner_gateway_holds_no_authority()
