"""SDD-FORCED-LIQUIDATION-RECORDER-COVERAGE-V1: fixtures only, no live Binance."""
from __future__ import annotations

import ast
import asyncio
import json
import socket
from pathlib import Path

import pytest

from trader.core.instrument_registry import InstrumentId
from trader.core.types import MarketType
from trader.observability import forced_liquidation as F
from trader.observability import forced_liquidation_coverage as C
from trader.observability import forced_liquidation_recorder as R
from trader.observability import forced_liquidation_store as S

ROOT = Path(__file__).resolve().parents[1]
BTC = InstrumentId("binance_usdm", MarketType.FUTURES, "BTCUSDT")
EXINFO = json.dumps({"symbols": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]}).encode()
START, END = 1_000_000, 1_100_000
# Test fixtures only: production has no frozen coverage timing policy, no retained
# Binance transport documentation and no clock-health source. These bytes are
# labelled fixtures, not documentation or clock evidence.
DOC = b"TEST FIXTURE transport documentation - not Binance documentation"
CLOCK_RAW = b"TEST FIXTURE clock-health receipt - not real clock evidence"
POLICY = C.CoveragePolicy("test-fixture-not-production", F.sha256(DOC), 20, 20, 40_000, 1_000)
CLOCK = C.ClockHealthEvidence("test-fixture-clock", "test-fixture-not-production",
                              F.sha256(CLOCK_RAW), 0, START - 100_000, END + 100_000, True)
REG = START - 60_000
H = {"protocol_hash": "a" * 64, "baseline_hash": "b" * 64}


def reg_receipt(fields: dict, **overrides) -> bytes:
    """TEST FIXTURE frozen registration receipt binding the case identity."""
    body = {k: fields[k] for k in C.REGISTRATION_FIELDS if k != "instrument_id"}
    body["instrument_id"] = fields["instrument_id"].value
    return F.encode({**body, "fixture": True, **overrides}).encode()


def register(store, c=None, raw=None, kind=C.REGISTRATION_RECEIPT_KIND, archived=1):
    raw = raw if raw is not None else reg_receipt(_case_fields(c or case()))
    return store.put_blob(raw, {"kind": kind, "archived_utc_ms": archived, "fixture": True})


def archive_fixtures(store):
    store.put_blob(DOC, {"kind": C.DOCUMENTATION_KIND, "archived_utc_ms": 1, "fixture": True})
    store.put_blob(CLOCK_RAW, {"kind": C.CLOCK_RECEIPT_KIND, "archived_utc_ms": 1, "fixture": True})
    register(store)
    return store


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    real = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"network access attempted: {address}")
        return real(self, address)
    monkeypatch.setattr(socket.socket, "connect", guarded)


def symbols(raw=EXINFO):
    return F.SymbolMap.from_exchange_info(raw, source_url=F.METADATA_SOURCE_URL,
                                          environment=F.ENVIRONMENT, received_utc_ms=1)


def fo(side="BUY", st=1, s="BTCUSDT", E=5, T=4, drop=(), **extra) -> bytes:
    order = {"s": s, "S": side, "o": "LIMIT", "f": "IOC", "q": "0.014", "p": "9910",
             "ap": "9910", "X": "FILLED", "l": "0.014", "z": "0.014", "T": T}
    item = {"e": "forceOrder", "E": E, "st": st, "o": order, **extra}
    for key in drop:
        item.pop(key)
    return json.dumps(item).encode()


def case(start=START, end=END, iid=BTC, reg=REG, **overrides):
    fields = dict(case_id="case-1", protocol_id=F.PROTOCOL_ID, investigation_id="inv-1",
                  registration_utc_ms=reg, window_id="win-1", instrument_id=iid,
                  window_start_ms=start, window_end_ms=end, **H)
    fields.update(overrides)
    fields.setdefault("registration_receipt_sha256", F.sha256(reg_receipt(fields)))
    return C.WindowCase(**fields)


def _case_fields(c):
    return {k: getattr(c, k) for k in C.WindowCase.__dataclass_fields__}


class Log:
    """Direct log builder; utc ms == monotonic ms (no drift)."""

    def __init__(self, tmp_path, exinfo=EXINFO, mono_shift=0):
        self.mono_shift = mono_shift
        self.store = S.LogStore(tmp_path / "fl.db")
        self.symbols = symbols(exinfo)
        self.key = self.store.put_blob(exinfo, self.symbols.envelope())
        archive_fixtures(self.store)
        self.n = 0

    def add(self, kind, sid, t, body, raw=None, mono=None):
        mono = t - self.mono_shift if mono is None else mono
        return self.store.append(kind, sid, t, mono * 1_000_000, body, raw)

    def open(self, sid, t, url=F.STREAM_URL, env=F.ENVIRONMENT):
        self.add(S.SESSION_OPEN, sid, t, {"url": url, "environment": env, "attempt": 1,
                                          "transport": {"ping_interval_s": 20, "ping_timeout_s": 20},
                                          "mapping": {"blob": self.key}})
        self.add(S.HANDSHAKE, sid, t, {"status": "OK", "http_status": 101})

    def live(self, sid, t):
        self.n += 1
        payload = f"{self.n:08x}"
        self.add(S.PING_SENT, sid, t, {"payload_hex": payload})
        self.add(S.PONG, sid, t, {"payload_hex": payload, "solicited": True})

    def lives(self, sid, t0, t1, step=20_000):
        for t in range(t0, t1 + 1, step):
            self.live(sid, t)

    def frame(self, sid, t, raw):
        return self.add(S.FRAME, sid, t, F.classify_frame(raw, self.symbols), raw)

    def close(self, sid, t):
        self.add(S.SESSION_CLOSE, sid, t, {"reason": "operator_stop"})

    def evaluate(self, c=None, policy=POLICY, clock=CLOCK):
        records, blobs = S.load(self.store.path)
        return C.evaluate(records, blobs, c or case(), policy, clock)


def covered(tmp_path, frames=()):
    log = Log(tmp_path)
    log.open("s1", START - 30_000)
    events = [(t, 0, None) for t in range(START - 20_000, END + 20_001, 20_000)]
    events += [(t, 1, raw) for t, raw in frames]
    for t, _, raw in sorted(events, key=lambda e: (e[0], e[1])):
        log.live("s1", t) if raw is None else log.frame("s1", t, raw)
    log.close("s1", END + 30_000)
    return log


def codes(receipt):
    return {r["code"] for r in receipt["coverage"]["reasons"]}


# 1-3 sides, no direction inference ---------------------------------------------

def test_valid_buy_snapshot_accepted(tmp_path):
    r = covered(tmp_path, [(START + 50_000, fo("BUY"))]).evaluate()
    assert r["coverage"]["status"] == C.PASS
    assert r["observed_order_sides"] == ["BUY"] and r["result"] == "BUY_FORCE_ORDER_OBSERVED"


def test_valid_sell_snapshot_accepted(tmp_path):
    r = covered(tmp_path, [(START + 50_000, fo("SELL"))]).evaluate()
    assert r["observed_order_sides"] == ["SELL"] and r["result"] == "SELL_FORCE_ORDER_OBSERVED"


def test_both_sides_retained_without_direction_inference(tmp_path):
    r = covered(tmp_path, [(START + 50_000, fo("SELL")), (START + 51_000, fo("BUY"))]).evaluate()
    assert r["observed_order_sides"] == ["BUY", "SELL"] and r["result"] == "BOTH_ORDER_SIDES_OBSERVED"
    text = json.dumps(r).lower()
    for forbidden in ("long", "short", "notional", "intensity", "count\""):
        assert forbidden not in text


# 4-6 filter ----------------------------------------------------------------------

def test_st2_excluded(tmp_path):
    assert F.classify_frame(fo(st=2), symbols())["items"][0]["disposition"] == F.EXCLUDED_ST2
    r = covered(tmp_path, [(START + 50_000, fo(st=2))]).evaluate()
    assert r["coverage"]["status"] == C.PASS and r["observed_order_sides"] == []
    assert r["filter_decisions"][0]["relevance"] == "EXCLUDED_ST2"


@pytest.mark.parametrize("raw", [fo(drop=("st",)), fo(st="1"), fo(st=True), fo(st=1.0), fo(st=3)])
def test_missing_or_malformed_st_fails_closed(tmp_path, raw):
    assert F.classify_frame(raw, symbols())["items"][0]["disposition"] == F.UNCLASSIFIABLE_ST
    r = covered(tmp_path, [(START + 50_000, raw)]).evaluate()
    assert r["coverage"]["status"] == C.UNKNOWN and "ST_UNCLASSIFIABLE" in codes(r)
    assert r["result"] == C.UNASSESSABLE and r["empty_window_manifest"] is None


@pytest.mark.parametrize("raw", [b"{not json", b'{"e":"forceOrder","e":"x"}', b"[]",
                                 fo(side="LONG"), fo(E=-1), fo(E="5"), fo(T=None),
                                 fo(e="aggTrade"), b'{"e":"forceOrder","st":1,"o":{"s":"BTCUSDT","q":"NaN"}}'])
def test_malformed_payload_is_not_admissible(tmp_path, raw):
    r = covered(tmp_path, [(START + 50_000, raw)]).evaluate()
    assert r["admissible_snapshots"] == [] and r["result"] == C.UNASSESSABLE
    assert r["coverage"]["status"] in (C.UNKNOWN, C.INVALID)


# 7-8 identity ----------------------------------------------------------------------

def test_exact_canonical_instrument_mapping():
    m = symbols()
    assert m.resolve("BTCUSDT") == {"status": F.EXACT, "instrument_id": BTC.value}
    assert BTC.value == "binance_usdm:futures:BTCUSDT"
    for variant in ("btcusdt", "BTCUSDT ", "BTC/USDT", "BTCUSD_PERP", "BTC"):
        assert m.resolve(variant)["status"] == F.NOT_LISTED
    with pytest.raises(ValueError):
        F.SymbolMap.from_exchange_info(EXINFO, source_url="https://demo-fapi.binance.com/fapi/v1/exchangeInfo",
                                       environment="demo", received_utc_ms=1)


def test_identity_ambiguity_fails_closed(tmp_path):
    dup = json.dumps({"symbols": [{"symbol": "BTCUSDT"}, {"symbol": "BTCUSDT"}]}).encode()
    assert F.classify_frame(fo(), symbols(dup))["items"][0]["disposition"] == F.IDENTITY_AMBIGUOUS
    log = Log(tmp_path, exinfo=dup)
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, END + 20_000)
    log.close("s1", END + 30_000)
    r = log.evaluate()
    assert r["coverage"]["status"] == C.INVALID and "IDENTITY_AMBIGUITY" in codes(r)
    # A case-variant symbol string for the case instrument blocks rather than drops.
    r = covered(tmp_path / "v", [(START + 50_000, fo(s="btcusdt"))]).evaluate()
    assert "SYMBOL_VARIANT_AMBIGUOUS" in codes(r) and r["result"] == C.UNASSESSABLE


def test_other_instrument_excluded_without_blocking(tmp_path):
    r = covered(tmp_path, [(START + 50_000, fo(s="ETHUSDT"))]).evaluate()
    assert r["coverage"]["status"] == C.PASS and r["observed_order_sides"] == []
    assert r["filter_decisions"][0]["relevance"] == "EXCLUDED_OTHER_INSTRUMENT"


# 9-11 raw preservation and sequencing -------------------------------------------------

def test_raw_bytes_and_hash_preserved(tmp_path):
    raw = fo() + b"  "  # exact bytes, including insignificant whitespace
    log = covered(tmp_path, [(START + 50_000, raw)])
    records, _ = S.load(log.store.path)
    frame = next(r for r in records if r["kind"] == S.FRAME)
    assert frame["raw"] == raw and frame["raw_sha256"] == F.sha256(raw)
    body = json.loads(frame["body"])["items"][0]
    assert (body["E"], body["T"], body["st"], body["s"], body["S"], body["q"], body["p"],
            body["ap"], body["z"]) == (5, 4, 1, "BTCUSDT", "BUY", "0.014", "9910", "9910", "0.014")
    with pytest.raises(Exception):
        log.store.db.execute("UPDATE records SET raw=x'00' WHERE seq=?", (frame["seq"],))
    with pytest.raises(Exception):
        log.store.db.execute("DELETE FROM records")


def test_deterministic_durable_sequencing_and_tamper_detection(tmp_path):
    log = covered(tmp_path, [(START + 50_000, fo())])
    records, blobs = S.load(log.store.path)
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    assert all(b["prev_hash"] == a["record_hash"] for a, b in zip(records, records[1:]))
    tampered = [dict(r) for r in records]
    frame = next(r for r in tampered if r["kind"] == S.FRAME)
    frame["raw"] = fo("SELL")
    r = C.evaluate(tampered, blobs, case(), POLICY, CLOCK)
    assert r["coverage"]["status"] == C.INVALID and "INTEGRITY_FAILURE" in codes(r)
    r = C.evaluate(records[:3] + records[4:], blobs, case(), POLICY, CLOCK)
    assert "INTEGRITY_FAILURE" in codes(r)


def test_duplicate_deliveries_preserved(tmp_path):
    raw = fo("BUY")
    r = covered(tmp_path, [(START + 50_000, raw), (START + 50_000, raw)]).evaluate()
    assert len(r["admissible_snapshots"]) == 2
    assert len({a["seq"] for a in r["admissible_snapshots"]}) == 2
    assert {a["raw_sha256"] for a in r["admissible_snapshots"]} == {F.sha256(raw)}
    assert r["observed_order_sides"] == ["BUY"]


# 12-16 coverage ---------------------------------------------------------------------

def test_fully_covered_empty_window(tmp_path):
    r = covered(tmp_path).evaluate()
    assert r["coverage"]["status"] == C.PASS and r["coverage"]["reasons"] == []
    assert r["result"] == "NO_PUBLISHED_SNAPSHOT_OBSERVED"
    assert r["empty_window_manifest"]["admissible_snapshots"] == []
    assert r["ordered_window_hash"]


def test_production_has_no_frozen_policy(tmp_path):
    assert C.PRODUCTION_POLICY is None
    r = covered(tmp_path).evaluate(policy=C.PRODUCTION_POLICY)
    assert r["coverage"]["status"] == C.POLICY_REQUIRED
    assert r["result"] == C.UNASSESSABLE and r["empty_window_manifest"] is None


def test_disconnect_gap_unassessable(tmp_path):
    log = Log(tmp_path)
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, START + 40_000)
    log.add(S.SESSION_CLOSE, "s1", START + 45_000, {"reason": "connection_closed", "code": 1006})
    log.add(S.HANDSHAKE_FAILURE, "s2", START + 46_000, {"error": "OSError"})
    log.open("s3", START + 70_000)
    log.lives("s3", START + 70_000, END + 20_000)
    log.close("s3", END + 30_000)
    r = log.evaluate()
    assert r["coverage"]["status"] == C.GAP and "WINDOW_NOT_COVERED" in codes(r)
    assert "RECONNECT_ATTEMPT_FAILED" in codes(r) and r["result"] == C.UNASSESSABLE


def test_overlapping_sessions_can_cover_rollover(tmp_path):
    log = Log(tmp_path)
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, START + 20_000)
    log.open("s2", START + 30_000)
    for t in (START + 40_000, START + 60_000):
        log.live("s1", t)
        log.live("s2", t)
    log.close("s1", START + 61_000)
    log.lives("s2", START + 80_000, END + 20_000)
    log.close("s2", END + 30_000)
    r = log.evaluate()
    assert r["coverage"]["status"] == C.PASS, r["coverage"]
    assert len(r["sessions"]) == 2


def test_missing_liveness_unassessable(tmp_path):
    log = Log(tmp_path)
    log.open("s1", START - 30_000)
    log.frame("s1", START + 1, fo("ETHUSDT"))
    log.close("s1", END + 30_000)
    r = log.evaluate()
    assert "MISSING_LIVENESS" in codes(r) and r["result"] == C.UNASSESSABLE
    # silent half-open session: pings sent, no pongs
    log = Log(tmp_path / "b")
    log.open("s1", START - 30_000)
    for t in range(START - 20_000, END + 20_000, 20_000):
        log.add(S.PING_SENT, "s1", t, {"payload_hex": f"{t:x}"})
    log.close("s1", END + 30_000)
    assert "MISSING_LIVENESS" in codes(log.evaluate())


def test_liveness_gap_unassessable(tmp_path):
    log = Log(tmp_path)
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, START + 20_000)
    log.lives("s1", START + 80_000, END + 20_000)
    log.close("s1", END + 30_000)
    r = log.evaluate()
    assert r["coverage"]["status"] == C.GAP and "LIVENESS_GAP" in codes(r)


def test_write_failure_unassessable(tmp_path):
    log = Log(tmp_path)
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, START + 40_000)
    log.add(S.WRITE_FAILURE, "s1", START + 41_000, {"failed_kind": "frame", "error": "OperationalError"})
    log.lives("s1", START + 60_000, END + 20_000)  # later pongs cannot restore coverage
    r = log.evaluate()
    assert "WRITE_FAILURE" in codes(r) and r["result"] == C.UNASSESSABLE
    assert "WINDOW_NOT_COVERED" in codes(r)


def test_crash_without_end_seal_unassessable(tmp_path):
    log = Log(tmp_path)
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, START + 60_000)  # then the process dies: no close
    r = log.evaluate()
    assert "SESSION_UNSEALED" in codes(r) and "END_NOT_BRACKETED" in codes(r)
    assert r["result"] == C.UNASSESSABLE


def test_clock_regression_and_drift_unassessable(tmp_path):
    log = Log(tmp_path)
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, START + 40_000)
    log.add(S.PING_SENT, "s1", START + 50_000, {"payload_hex": "aa"}, mono=START + 55_000)
    log.lives("s1", START + 60_000, END + 20_000)
    log.close("s1", END + 30_000)
    assert "CLOCK_OFFSET_DRIFT" in codes(log.evaluate())
    log = covered(tmp_path / "b")
    log.add(S.FRAME, "s1", START + 10, F.classify_frame(fo(), log.symbols), fo(), mono=END + 40_000)
    r = log.evaluate()
    assert "CLOCK_REGRESSION" in codes(r) and r["result"] == C.UNASSESSABLE


# 17-18 membership --------------------------------------------------------------------

def test_receipt_exactly_at_window_end_excluded(tmp_path):
    r = covered(tmp_path, [(END - 1, fo("SELL"))]).evaluate()
    assert r["observed_order_sides"] == ["SELL"]
    log = Log(tmp_path / "b")
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, END - 20_000)
    log.frame("s1", END, fo("SELL"))
    log.lives("s1", END, END + 20_000)
    log.close("s1", END + 30_000)
    r = log.evaluate()
    assert r["coverage"]["status"] == C.PASS and r["observed_order_sides"] == []
    assert r["result"] == "NO_PUBLISHED_SNAPSHOT_OBSERVED"


def test_post_cut_message_with_earlier_exchange_time_excluded(tmp_path):
    log = Log(tmp_path)
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, END + 20_000)
    log.frame("s1", END + 25_000, fo("BUY", E=START + 1, T=START))
    log.close("s1", END + 30_000)
    r = log.evaluate()
    assert r["observed_order_sides"] == [] and r["filter_decisions"] == []


# 19 seal / replay -----------------------------------------------------------------------

def test_seal_is_immutable_and_replay_reproduces(tmp_path):
    log = covered(tmp_path, [(START + 50_000, fo("BUY")), (START + 51_000, fo(st=2)),
                             (START + 52_000, fo(s="ETHUSDT"))])
    sealed = C.seal_fixture(log.store, case(), POLICY, CLOCK, sealed_utc_ms=END + 40_000)
    assert sealed["result"] == "BUY_FORCE_ORDER_OBSERVED" and sealed["authority"] == C.NON_PRODUCTION
    assert S.load_seal(log.store.path, case().window_key) is None  # production key untouched
    text = S.load_seal(log.store.path, "fixture|" + case().window_key)
    records, blobs = S.load(log.store.path)
    again = C.replay(records, blobs, text)
    assert again["match"] and again["differences"] == []
    assert again["receipt"]["ordered_window_hash"] == sealed["ordered_window_hash"]
    # later appends after the cut do not change the sealed prefix result
    log.frame("s1", END + 50_000, fo("SELL"))
    records, blobs = S.load(log.store.path)
    assert C.replay(records, blobs, text)["match"]
    # a forged receipt is detected
    forged = json.loads(text)
    forged["observed_order_sides"] = ["BUY", "SELL"]
    assert not C.replay(records, blobs, F.encode(forged))["match"]
    with pytest.raises(ValueError):
        log.store.put_seal("fixture|" + case().window_key, 1, "0" * 64, "{}")
    included = [r["seq"] for r in sealed["included_records"]]
    assert included == sorted(included)


def test_window_end_is_bar_close_not_assessment_deadline():
    with pytest.raises(ValueError):
        case(START, START)
    with pytest.raises(ValueError):
        case(protocol_id="other.v1")
    fields = set(C.WindowCase.__dataclass_fields__)
    assert not fields & {"deadline_ms", "expires_ms", "grace_ms", "guard_ms"}


# registration binding ----------------------------------------------------------------------

@pytest.mark.parametrize("reg", [START, START + 1, END])
def test_late_registration_cannot_seal_assessably(tmp_path, reg):
    log = covered(tmp_path, [(START + 50_000, fo("BUY"))])
    sealed = C.seal_fixture(log.store, case(reg=reg), POLICY, CLOCK, END + 40_000)
    assert sealed["coverage"]["status"] == C.INVALID
    assert "REGISTRATION_NOT_PROSPECTIVE" in codes(sealed)
    assert sealed["result"] == C.UNASSESSABLE and sealed["observed_order_sides"] is None


@pytest.mark.parametrize("field,value", [("investigation_id", ""), ("protocol_hash", ""),
                                         ("baseline_hash", "B" * 64), ("protocol_hash", "a" * 63),
                                         ("registration_receipt_sha256", None),
                                         ("registration_utc_ms", 0), ("registration_utc_ms", 1.0)])
def test_missing_registration_identity_fails_closed(field, value):
    with pytest.raises(ValueError):
        case(**{field: value})
    fields = dict(case().as_dict())
    fields.pop("baseline_hash")
    with pytest.raises((KeyError, ValueError)):
        C.WindowCase.from_dict(fields)


@pytest.mark.parametrize("field,value", [("protocol_hash", "d" * 64), ("baseline_hash", "d" * 64),
                                         ("registration_receipt_sha256", "d" * 64),
                                         ("investigation_id", "inv-2"),
                                         ("registration_utc_ms", START + 1)])
def test_altered_registration_identity_breaks_replay(tmp_path, field, value):
    log = covered(tmp_path, [(START + 50_000, fo("BUY"))])
    C.seal_fixture(log.store, case(), POLICY, CLOCK, END + 40_000)
    text = S.load_seal(log.store.path, "fixture|" + case().window_key)
    records, blobs = S.load(log.store.path)
    forged = json.loads(text)
    forged["case"][field] = value
    again = C.replay(records, blobs, F.encode(forged))
    assert not again["match"] and "ordered_window_hash" in again["differences"]
    forged["case"].pop(field)
    assert not C.replay(records, blobs, F.encode(forged))["match"]


# production seal authority -------------------------------------------------------------------

def test_fixture_policy_cannot_authorize_production_seal(tmp_path):
    import inspect
    assert list(inspect.signature(C.seal).parameters) == ["store", "case", "sealed_utc_ms"]
    log = covered(tmp_path, [(START + 50_000, fo("BUY"))])
    with pytest.raises(TypeError):
        C.seal(log.store, case(), POLICY, END + 40_000)
    # A fixture PASS relabelled PRODUCTION does not replay: the policy is not canonical.
    C.seal_fixture(log.store, case(), POLICY, CLOCK, END + 40_000)
    text = S.load_seal(log.store.path, "fixture|" + case().window_key)
    assert json.loads(text)["coverage"]["status"] == C.PASS
    forged = json.loads(text)
    forged["authority"] = C.PRODUCTION
    records, blobs = S.load(log.store.path)
    again = C.replay(records, blobs, F.encode(forged))  # top-level label vs archived state
    assert not again["match"] and again["differences"] == ["provenance"]
    # relabelling the archived state too: canonical rules come from that state
    for canonical in (None, POLICY):
        forged["authority_state"] = {**C.production_authority_state(),
                                     "production_policy": C._slot(canonical)}
        again = C.replay(records, blobs, F.encode(forged))
        assert not again["match"]
        assert "TEST_ONLY_CLOCK_HEALTH" in codes(again["receipt"])
        assert again["receipt"]["result"] == C.UNASSESSABLE
        if canonical is None:
            assert "POLICY_NOT_CANONICAL" in codes(again["receipt"])


def test_production_seal_without_frozen_policy_never_passes(tmp_path):
    assert C.PRODUCTION_POLICY is None and C.PRODUCTION_CLOCK_HEALTH_POLICY is None
    log = covered(tmp_path, [(START + 50_000, fo("BUY"))])
    assert log.evaluate()["coverage"]["status"] == C.PASS  # same log passes under fixtures
    sealed = C.seal(log.store, case(), END + 40_000)
    assert sealed["authority"] == C.PRODUCTION
    assert sealed["coverage"]["status"] == C.POLICY_REQUIRED
    assert {"COVERAGE_POLICY_NOT_FROZEN", "CLOCK_HEALTH_POLICY_NOT_FROZEN"} <= codes(sealed)
    assert sealed["result"] == C.UNASSESSABLE and sealed["observed_order_sides"] is None
    text = S.load_seal(log.store.path, case().window_key)
    records, blobs = S.load(log.store.path)
    assert C.replay(records, blobs, text)["match"]


def test_unverified_documentation_hash_cannot_pass(tmp_path):
    log = covered(tmp_path)
    unarchived = C.CoveragePolicy("fixture", F.sha256(b"never archived"), 20, 20, 40_000, 1_000)
    r = log.evaluate(policy=unarchived)
    assert r["coverage"]["status"] == C.POLICY_REQUIRED and r["result"] == C.UNASSESSABLE
    assert "TRANSPORT_DOCUMENTATION_UNVERIFIED" in codes(r)
    # archived after the window started, or under the wrong kind, is not verified either
    for raw, env in ((b"late doc", {"kind": C.DOCUMENTATION_KIND, "archived_utc_ms": START}),
                     (b"wrong kind", {"kind": "other", "archived_utc_ms": 1})):
        log.store.put_blob(raw, env)
        p = C.CoveragePolicy("fixture", F.sha256(raw), 20, 20, 40_000, 1_000)
        assert "TRANSPORT_DOCUMENTATION_UNVERIFIED" in codes(log.evaluate(policy=p))


# clock health ------------------------------------------------------------------------------------

def test_stable_but_wrong_utc_without_clock_health_cannot_pass(tmp_path):
    # UTC a constant hour ahead of the monotonic mapping: zero drift, yet nothing
    # in the log shows whether either reading is correct absolute UTC.
    log = Log(tmp_path, mono_shift=3_600_000)
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, END + 20_000)
    log.close("s1", END + 30_000)
    assert log.evaluate()["coverage"]["status"] == C.PASS  # only with clock evidence
    r = log.evaluate(clock=None)
    assert "CLOCK_OFFSET_DRIFT" not in codes(r)
    assert r["coverage"]["status"] == C.CLOCK_REQUIRED and "ABSOLUTE_UTC_UNVERIFIED" in codes(r)
    assert r["result"] == C.UNASSESSABLE
    # evidence that does not cover the window, or whose receipt is not archived, fails too
    short = C.ClockHealthEvidence("c", "fixture", CLOCK.receipt_sha256, 0, START, END - 1, True)
    assert "CLOCK_HEALTH_NOT_COVERING_WINDOW" in codes(log.evaluate(clock=short))
    missing = C.ClockHealthEvidence("c", "fixture", "e" * 64, 0, 0, END * 2, True)
    assert "CLOCK_HEALTH_RECEIPT_UNVERIFIED" in codes(log.evaluate(clock=missing))


def test_fixture_clock_health_uncertainty_bounds_membership_and_coverage(tmp_path):
    assert covered(tmp_path, [(START + 50_000, fo("BUY"))]).evaluate()["coverage"]["status"] == C.PASS
    wide = C.ClockHealthEvidence("c", "fixture", CLOCK.receipt_sha256, 1_000,
                                 START - 100_000, END + 100_000, True)
    r = covered(tmp_path / "a", [(START + 50_000, fo("BUY"))]).evaluate(clock=wide)
    assert r["coverage"]["status"] == C.PASS and r["clock_health"]["max_abs_uncertainty_ms"] == 1_000
    for t in (START + 500, END - 500, START - 500, END + 500):
        r = covered(tmp_path / str(t), [(t, fo("SELL"))]).evaluate(clock=wide)
        assert "RECEIPT_TIME_MEMBERSHIP_AMBIGUOUS" in codes(r) and r["result"] == C.UNASSESSABLE
    # an unrelated instrument near the boundary does not block
    r = covered(tmp_path / "eth", [(END - 500, fo(s="ETHUSDT"))]).evaluate(clock=wide)
    assert r["coverage"]["status"] == C.PASS
    # coverage must span the uncertainty band: liveness ending exactly at END is not enough
    log = Log(tmp_path / "edge")
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, END)
    log.close("s1", END + 30_000)
    assert log.evaluate()["coverage"]["status"] == C.PASS
    assert "END_NOT_BRACKETED" in codes(log.evaluate(clock=wide))


def test_seal_replay_retains_registration_policy_and_clock_provenance(tmp_path):
    log = covered(tmp_path, [(START + 50_000, fo("BUY"))])
    sealed = C.seal_fixture(log.store, case(), POLICY, CLOCK, END + 40_000)
    text = S.load_seal(log.store.path, "fixture|" + case().window_key)
    stored = json.loads(text)
    assert stored["case"] == case().as_dict()
    assert stored["case_sha256"] == F.digest(case().as_dict())
    assert stored["policy"]["documentation_sha256"] == F.sha256(DOC)
    assert stored["clock_health"]["receipt_sha256"] == F.sha256(CLOCK_RAW)
    assert stored["clock_health"]["test_only"] is True
    records, blobs = S.load(log.store.path)
    assert C.replay(records, blobs, text)["match"]
    for key, mutate in (("policy", lambda d: d.update(max_liveness_gap_ms=39_999)),
                        ("clock_health", lambda d: d.update(max_abs_uncertainty_ms=1))):
        forged = json.loads(text)
        mutate(forged[key])
        again = C.replay(records, blobs, F.encode(forged))
        assert not again["match"] and "ordered_window_hash" in again["differences"]
    forged = json.loads(text)
    forged["clock_health"] = None
    assert not C.replay(records, blobs, F.encode(forged))["match"]
    assert sealed["ordered_window_hash"] == stored["ordered_window_hash"]


# recorder with fixture transports ---------------------------------------------------------

class FakeConn:
    ping_interval = ping_timeout = 20  # as the library defaults would report

    def __init__(self, script, clock):
        self.script, self.clock, self.closed = list(script), clock, None

    async def recv(self, decode=None):
        from websockets.exceptions import ConnectionClosedError
        from websockets.frames import Close
        while self.script:
            t, action = self.script.pop(0)
            self.clock.t = t
            if action == "live":
                payload = f"{t:x}".encode()
                self.hook.on_ping_sent(payload)
                self.hook.on_pong(payload, True)
                continue
            return action
        raise ConnectionClosedError(Close(1006, ""), None)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


class Clock:
    t = 0

    def __call__(self):
        return self.t, self.t * 1_000_000


def fake_connector(scripts, clock):
    def connector(recorder, url, kwargs):
        async def gen():
            for script in scripts:
                conn = FakeConn(script, clock)
                conn.hook = recorder.new_session()
                yield conn
        return gen()
    return connector


def run_recorder(tmp_path, scripts, store=None):
    clock = Clock()
    store = archive_fixtures(store or S.LogStore(tmp_path / "rec.db"))
    rec = R.Recorder(store, symbols(), EXINFO, connector=fake_connector(scripts, clock), clock=clock)
    return rec, asyncio.run(rec.run())


def test_recorder_session_receipts_and_telemetry(tmp_path):
    script = [(START - 20_000, "live")] + [(t, "live") for t in range(START, END + 20_001, 20_000)]
    script.insert(3, (START + 30_000, fo("BUY")))
    rec, telemetry = run_recorder(tmp_path, [script])
    records, blobs = S.load(rec.store.path)
    kinds = [r["kind"] for r in records]
    assert kinds[:2] == [S.SESSION_OPEN, S.HANDSHAKE] and kinds[-1] == S.SESSION_CLOSE
    opened = json.loads(records[0]["body"])
    assert opened["url"] == F.STREAM_URL and opened["environment"] == "production"
    assert opened["subscription_ack"] == "NOT_APPLICABLE_RAW_STREAM_URL"
    assert set(opened["code_sha256"]) and opened["dependencies"]["websockets"]
    closing = json.loads(records[-1]["body"])
    assert closing["code"] == 1006 and closing["telemetry"]["frames"] == 1
    assert telemetry["connection_attempts"] == 1 and telemetry["wire_bytes_received"] == "UNKNOWN"
    assert telemetry["payload_bytes_received"] == len(fo("BUY"))
    r = C.evaluate(records, blobs, case(), POLICY, CLOCK)
    assert r["coverage"]["status"] == C.PASS and r["result"] == "BUY_FORCE_ORDER_OBSERVED"


def test_recorder_write_failure_stops_and_is_recorded(tmp_path):
    class FailingStore(S.LogStore):
        def append(self, kind, *a, **k):
            if kind == S.FRAME:
                raise OSError("disk full")
            return super().append(kind, *a, **k)
    store = FailingStore(tmp_path / "rec.db")
    script = [(START - 20_000, "live"), (START + 1, fo("BUY")), (START + 20_000, "live")]
    with pytest.raises(R.RecorderStopped):
        run_recorder(tmp_path, [script], store=store)
    records, blobs = S.load(store.path)
    assert [r["kind"] for r in records][-1] == S.WRITE_FAILURE
    r = C.evaluate(records, blobs, case(), POLICY, CLOCK)
    assert "WRITE_FAILURE" in codes(r) and r["result"] == C.UNASSESSABLE


def test_loopback_websocket_records_real_ping_pong(tmp_path):
    """Real websockets library over loopback; URL mismatch keeps it non-evidence."""
    from websockets.asyncio.server import serve

    async def main():
        async def handler(ws):
            await ws.send(fo("SELL").decode())
            await asyncio.sleep(0.3)
            await ws.close()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            store = S.LogStore(tmp_path / "loop.db")
            rec = R.Recorder(store, symbols(), EXINFO, url=f"ws://127.0.0.1:{port}/market/ws/!forceOrder@arr",
                             connect_kwargs={"ping_interval": 0.05})
            stop = asyncio.Event()
            task = asyncio.create_task(rec.run(stop))
            await asyncio.sleep(0.5)
            stop.set()
            await asyncio.wait_for(task, 5)
            return store
    store = asyncio.run(main())
    records, blobs = S.load(store.path)
    kinds = [r["kind"] for r in records]
    assert S.FRAME in kinds and S.PING_SENT in kinds and S.PONG in kinds and S.SESSION_CLOSE in kinds
    opened = json.loads(records[0]["body"])
    assert opened["transport"]["ping_interval_s"] == 0.05
    assert opened["connect_overrides"] == {"ping_interval": 0.05}
    frame = next(r for r in records if r["kind"] == S.FRAME)
    assert frame["raw"] == fo("SELL")
    t0 = records[0]["utc_ms"]
    r = C.evaluate(records, blobs, case(t0, t0 + 200, reg=t0 - 1), POLICY, CLOCK)
    assert "SESSION_URL_MISMATCH" in codes(r) and r["result"] == C.UNASSESSABLE


# 20-21 boundaries --------------------------------------------------------------------------

MODULES = ("forced_liquidation", "forced_liquidation_store", "forced_liquidation_recorder",
           "forced_liquidation_coverage")


def _imports(path):
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(("." * node.level) + (node.module or ""))
    return names


def test_no_account_or_trading_authority():
    forbidden = ("ccxt", "journal", "engine", "orchestrator", "risk", "executor", "kernel",
                 "attention", "cognition", "research", "brain", "dotenv", "config")
    for name in MODULES:
        path = ROOT / "trader" / "observability" / f"{name}.py"
        for imported in _imports(path):
            assert not any(f in imported for f in forbidden), (name, imported)
        text = path.read_text()
        for secret in ("API_KEY", "api_key", "secret", "signature", "listenKey", "/fapi/v1/order"):
            assert secret not in text, (name, secret)


def test_no_trading_or_attention_path_uses_recorder():
    for path in list((ROOT / "trader").rglob("*.py")):
        if path.stem in MODULES:
            continue
        assert "forced_liquidation" not in path.read_text(), path


# seal-time authority state -------------------------------------------------------------------

FROZEN_CLOCK_POLICY = {"policy_id": "test-fixture-clock-policy", "max_abs_uncertainty_ms": 50}


def _production_seal(tmp_path):
    log = covered(tmp_path, [(START + 50_000, fo("BUY"))])
    sealed = C.seal(log.store, case(), END + 40_000)
    text = S.load_seal(log.store.path, case().window_key)
    records, blobs = S.load(log.store.path)
    return sealed, text, records, blobs


def test_unfrozen_seal_replays_identically_after_globals_freeze(tmp_path, monkeypatch):
    sealed, text, records, blobs = _production_seal(tmp_path)
    state = sealed["authority_state"]
    assert state["authority"] == C.PRODUCTION
    for key in ("production_policy", "production_clock_health_policy"):
        assert state[key] == {"status": C.NOT_FROZEN, "value": None, "sha256": None}
    before = C.replay(records, blobs, text)
    assert before["match"]
    monkeypatch.setattr(C, "PRODUCTION_POLICY", POLICY)
    monkeypatch.setattr(C, "PRODUCTION_CLOCK_HEALTH_POLICY", FROZEN_CLOCK_POLICY)
    after = C.replay(records, blobs, text)
    assert after["match"] and after["receipt"] == before["receipt"]
    assert {"COVERAGE_POLICY_NOT_FROZEN", "CLOCK_HEALTH_POLICY_NOT_FROZEN"} <= codes(after["receipt"])


def test_frozen_policy_seal_replays_identically_after_globals_change(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "PRODUCTION_POLICY", POLICY)
    monkeypatch.setattr(C, "PRODUCTION_CLOCK_HEALTH_POLICY", FROZEN_CLOCK_POLICY)
    sealed, text, records, blobs = _production_seal(tmp_path)
    state = sealed["authority_state"]
    assert state["production_policy"]["status"] == C.FROZEN
    assert state["production_policy"]["value"]["policy_id"] == POLICY.policy_id
    assert state["production_clock_health_policy"]["sha256"] == F.digest(FROZEN_CLOCK_POLICY)
    # No production clock evidence source: still not assessable, but no policy codes.
    assert sealed["coverage"]["status"] == C.CLOCK_REQUIRED
    assert not {"COVERAGE_POLICY_NOT_FROZEN", "POLICY_NOT_CANONICAL",
                "CLOCK_HEALTH_POLICY_NOT_FROZEN"} & codes(sealed)
    before = C.replay(records, blobs, text)
    assert before["match"]
    other = C.CoveragePolicy("later-version", F.sha256(DOC), 20, 20, 30_000, 500)
    for policy, clock_policy in ((None, None), (other, {"policy_id": "later"})):
        monkeypatch.setattr(C, "PRODUCTION_POLICY", policy)
        monkeypatch.setattr(C, "PRODUCTION_CLOCK_HEALTH_POLICY", clock_policy)
        after = C.replay(records, blobs, text)
        assert after["match"] and after["receipt"] == before["receipt"]


@pytest.mark.parametrize("mutate", [
    lambda s: s["production_policy"].update(status=C.FROZEN),
    lambda s: s.update(production_policy=C._slot(POLICY)),
    lambda s: s.update(production_clock_health_policy=C._slot(FROZEN_CLOCK_POLICY)),
    lambda s: s["production_clock_health_policy"].update(value={"x": 1}),
    lambda s: s.update(authority=C.NON_PRODUCTION),
    lambda s: s.update(schema="other"),
    lambda s: s.pop("production_clock_health_policy"),
])
def test_tampered_archived_authority_state_breaks_replay(tmp_path, mutate):
    _, text, records, blobs = _production_seal(tmp_path)
    forged = json.loads(text)
    mutate(forged["authority_state"])
    assert not C.replay(records, blobs, F.encode(forged))["match"]


def test_fixture_seal_archives_non_production_state(tmp_path):
    log = covered(tmp_path, [(START + 50_000, fo("BUY"))])
    sealed = C.seal_fixture(log.store, case(), POLICY, CLOCK, END + 40_000)
    assert sealed["authority_state"] == C.NON_PRODUCTION_STATE
    text = S.load_seal(log.store.path, "fixture|" + case().window_key)
    records, blobs = S.load(log.store.path)
    forged = json.loads(text)
    forged["authority_state"]["production_policy"] = C._slot(POLICY)  # not applicable here
    assert C.replay(records, blobs, F.encode(forged))["differences"] == ["provenance"]


# registration receipt -------------------------------------------------------------------------

def _reg_log(tmp_path, register_with=None):
    """Covered log whose store holds only the given registration blob (if any)."""
    log = Log.__new__(Log)
    log.mono_shift, log.n = 0, 0
    log.store = S.LogStore(tmp_path / "fl.db")
    log.symbols = symbols()
    log.key = log.store.put_blob(EXINFO, log.symbols.envelope())
    log.store.put_blob(DOC, {"kind": C.DOCUMENTATION_KIND, "archived_utc_ms": 1})
    log.store.put_blob(CLOCK_RAW, {"kind": C.CLOCK_RECEIPT_KIND, "archived_utc_ms": 1})
    if register_with is not None:
        register_with(log.store)
    log.open("s1", START - 30_000)
    log.lives("s1", START - 20_000, END + 20_000)
    log.close("s1", END + 30_000)
    return log


def test_archived_registration_receipt_passes_and_enters_provenance(tmp_path):
    log = _reg_log(tmp_path, register)
    r = log.evaluate()
    assert r["coverage"]["status"] == C.PASS
    reg = r["registration_receipt"]
    assert reg == {"sha256": case().registration_receipt_sha256,
                   "kind": C.REGISTRATION_RECEIPT_KIND, "archived_utc_ms": 1, "verified": True}
    sealed = C.seal_fixture(log.store, case(), POLICY, CLOCK, END + 40_000)
    assert sealed["registration_receipt"] == reg
    # the verified identity is bound into the ordered-window hash
    forged = json.loads(S.load_seal(log.store.path, "fixture|" + case().window_key))
    forged["registration_receipt"]["archived_utc_ms"] = 2
    records, blobs = S.load(log.store.path)
    again = C.replay(records, blobs, F.encode(forged))
    assert not again["match"] and "registration_receipt" in again["differences"]


@pytest.mark.parametrize("register_with,code", [
    (None, "REGISTRATION_RECEIPT_MISSING"),
    (lambda st: register(st, kind="transport_documentation"), "REGISTRATION_RECEIPT_KIND_MISMATCH"),
    (lambda st: register(st, archived=START), "REGISTRATION_RECEIPT_ARCHIVED_LATE"),
    (lambda st: register(st, archived=START + 1), "REGISTRATION_RECEIPT_ARCHIVED_LATE"),
])
def test_unverified_registration_receipt_invalid(tmp_path, register_with, code):
    r = _reg_log(tmp_path, register_with).evaluate()
    assert r["coverage"]["status"] == C.INVALID and code in codes(r)
    assert r["result"] == C.UNASSESSABLE and r["registration_receipt"]["verified"] is False


def test_wrong_registration_hash_invalid(tmp_path):
    log = _reg_log(tmp_path, register)
    r = log.evaluate(case(registration_receipt_sha256="d" * 64))
    assert "REGISTRATION_RECEIPT_MISSING" in codes(r) and r["result"] == C.UNASSESSABLE
    # bytes stored under the right key but not hashing to it
    records, blobs = S.load(log.store.path)
    key = case().registration_receipt_sha256
    blobs[key] = {**blobs[key], "raw": blobs[key]["raw"] + b" "}
    r = C.evaluate(records, blobs, case(), POLICY, CLOCK)
    assert "REGISTRATION_RECEIPT_HASH_MISMATCH" in codes(r) and r["result"] == C.UNASSESSABLE


@pytest.mark.parametrize("field,value", [
    ("case_id", "case-2"), ("investigation_id", "inv-2"), ("protocol_id", "other.v1"),
    ("protocol_hash", "d" * 64), ("baseline_hash", "d" * 64), ("window_id", "win-2"),
    ("window_start_ms", START + 1), ("window_end_ms", END + 1), ("window_start_ms", float(START)),
    ("registration_utc_ms", REG - 1),
    ("instrument_id", "binance_usdm:futures:ETHUSDT"), ("instrument_id", "binance_usdm:futures:btcusdt"),
])
def test_mismatched_registration_receipt_invalid(tmp_path, field, value):
    raw = reg_receipt(_case_fields(case()), **{field: value})
    c = case(registration_receipt_sha256=F.sha256(raw))
    log = _reg_log(tmp_path, lambda st: register(st, raw=raw))
    r = log.evaluate(c)
    assert r["coverage"]["status"] == C.INVALID and r["result"] == C.UNASSESSABLE
    assert {"code": "REGISTRATION_RECEIPT_MISMATCH", "class": C.INVALID, "field": field} \
        in r["coverage"]["reasons"]


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"case_id":"a","case_id":"b"}'])
def test_malformed_registration_receipt_invalid(tmp_path, raw):
    c = case(registration_receipt_sha256=F.sha256(raw))
    r = _reg_log(tmp_path, lambda st: register(st, raw=raw)).evaluate(c)
    assert "REGISTRATION_RECEIPT_MALFORMED" in codes(r) and r["result"] == C.UNASSESSABLE


def test_late_registration_time_in_receipt_invalid(tmp_path):
    late = case(reg=START)
    log = _reg_log(tmp_path, lambda st: register(st, late))
    r = log.evaluate(late)
    assert {"REGISTRATION_NOT_PROSPECTIVE", "REGISTRATION_RECEIPT_NOT_PROSPECTIVE"} <= codes(r)
    assert r["result"] == C.UNASSESSABLE


def test_altered_registration_bytes_break_replay_and_replay_is_deterministic(tmp_path):
    log = _reg_log(tmp_path, register)
    C.seal_fixture(log.store, case(), POLICY, CLOCK, END + 40_000)
    text = S.load_seal(log.store.path, "fixture|" + case().window_key)
    records, blobs = S.load(log.store.path)
    first, second = C.replay(records, blobs, text), C.replay(records, blobs, text)
    assert first["match"] and second["match"] and first["receipt"] == second["receipt"]
    key = case().registration_receipt_sha256
    altered = {**blobs, key: {**blobs[key], "raw": reg_receipt(_case_fields(case()), extra=1)}}
    again = C.replay(records, altered, text)
    assert not again["match"] and "REGISTRATION_RECEIPT_HASH_MISMATCH" in codes(again["receipt"])
    missing = {k: v for k, v in blobs.items() if k != key}
    assert not C.replay(records, missing, text)["match"]
