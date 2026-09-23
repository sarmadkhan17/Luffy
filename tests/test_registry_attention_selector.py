"""The registry Attention selector observes; it never grants trading permission."""
from dataclasses import FrozenInstanceError
import ast
import copy
from pathlib import Path

import pytest

from trader.core.instrument_registry import (
    AccountTrading, AssetClass, Capability, Eligibility, EligibilityBasis,
    EligibilityBasisKind, InstrumentId, InstrumentRecord, OrderConstraints,
    Presence, RegistrySnapshot,
)
from trader.core.types import MarketType
from trader.observability import registry_selector as rs
from trader.observability.registry_selector import Exclusion, SelectionRefused, select

AS_OF = 1_800_000_000_000
SCOPE = "demo-account"


def _id(sym, venue="binance_usdm", market=MarketType.FUTURES):
    return InstrumentId(venue, market, sym)


def _rec(sym="BTCUSDT", *, base=None, venue="binance_usdm", market=MarketType.FUTURES,
         eligibility=Eligibility.UNKNOWN, **over):
    iid = _id(sym, venue, market)
    basis = None
    if eligibility is Eligibility.ELIGIBLE:
        basis = EligibilityBasis(EligibilityBasisKind.ACCOUNT_SYMBOL_PERMISSION, iid, "ev:perm")
    elif eligibility is Eligibility.INELIGIBLE:
        basis = EligibilityBasis(EligibilityBasisKind.ACCOUNT_SYMBOL_REFUSAL, iid, "ev:refuse")
    fields = dict(
        instrument_id=iid, asset_class=AssetClass.UNKNOWN, contract_type="PERPETUAL",
        base_asset=base if base is not None else sym[:-4], quote_asset="USDT",
        settlement_asset="USDT", contract_multiplier=None, delivery_ms=None,
        venue_listing=Presence.PRESENT, venue_status="TRADING", onboard_ms=None,
        constraints=OrderConstraints(), shortability=Capability.UNKNOWN,
        account_eligibility=eligibility, account_state_ref=SCOPE,
        symbol_config=Presence.UNKNOWN, leverage_bracket=Presence.UNKNOWN,
        data_availability=Presence.UNKNOWN, observed_at_ms=AS_OF, source="test",
        eligibility_basis=basis)
    fields.update(over)
    return InstrumentRecord(**fields)


def _snap(*records, trading=AccountTrading.UNKNOWN, as_of=AS_OF):
    return RegistrySnapshot(as_of_ms=as_of, account_scope=SCOPE, account_trading=trading,
                            records=tuple(records), source="test")


def _sel(snapshot, scan=(), cursor=None, cycle=AS_OF, max_age=60_000):
    return select(snapshot, cycle_as_of_ms=cycle, max_snapshot_age_ms=max_age,
                  strategy_scan=scan, cursor_before=cursor)


def cid(sym):
    return _id(sym).value


def reasons(result):
    return {e.instrument_id: e.reason for e in result.exclusions}


# --- determinism, immutability, freshness -----------------------------------

def test_same_inputs_same_result():
    snap = _snap(_rec("BTCUSDT"), _rec("ETHUSDT"), _rec("SOLUSDT"))
    a = _sel(snap, ["ETH/USDT:USDT"], cid("CUSDT"))
    b = _sel(_snap(_rec("SOLUSDT"), _rec("BTCUSDT"), _rec("ETHUSDT")), ["ETH/USDT:USDT"], cid("CUSDT"))
    assert a == b
    assert a.canonical_json() == b.canonical_json()
    assert a.selection_id == b.selection_id
    assert a.rule_version == rs.RULE_VERSION
    assert (a.snapshot_id, a.snapshot_as_of_ms) == (snap.snapshot_id, AS_OF)


def test_result_is_immutable():
    result = _sel(_snap(_rec()))
    with pytest.raises(FrozenInstanceError):
        result.selected_id = None
    with pytest.raises(FrozenInstanceError):
        result.cursor_after = "x"
    assert isinstance(result.candidate_ids, tuple) and isinstance(result.exclusions, tuple)


def test_future_snapshot_refused():
    with pytest.raises(SelectionRefused) as e:
        _sel(_snap(_rec()), cycle=AS_OF - 1)
    assert e.value.reason == rs.SNAPSHOT_FUTURE


def test_stale_snapshot_selects_nothing_and_keeps_cursor():
    result = _sel(_snap(_rec()), cursor=cid("ZZZUSDT"), cycle=AS_OF + 60_001)
    assert result.outcome == rs.SNAPSHOT_STALE
    assert result.selected_id is None and result.selected_symbol is None
    assert result.cursor_after == result.cursor_before == cid("ZZZUSDT")
    assert (result.registry_age_ms, result.max_snapshot_age_ms) == (60_001, 60_000)


def test_age_exactly_at_limit_accepted():
    result = _sel(_snap(_rec()), cycle=AS_OF + 60_000)
    assert result.outcome == rs.SELECTED
    assert result.registry_age_ms == 60_000


@pytest.mark.parametrize("kwargs", [
    dict(cycle=-1), dict(cycle=True), dict(max_age=-1), dict(max_age=1.5),
    dict(scan="BTC/USDT"), dict(scan=[""]), dict(scan=[1]), dict(cursor=""), dict(cursor=3),
])
def test_invalid_inputs_refused(kwargs):
    with pytest.raises(SelectionRefused) as e:
        _sel(_snap(_rec()), **kwargs)
    assert e.value.reason == "invalid_input"


@pytest.mark.parametrize("cursor", [
    "banana",
    # incomplete or extra segments
    "binance_usdm:futures", "binance_usdm:futures:", ":futures:BTCUSDT",
    "binance_usdm::BTCUSDT", "binance_usdm:futures:BTCUSDT:x", "binance_usdm:futures:BTC:USDT",
    # market type not a MarketType value
    "binance_usdm:perp:BTCUSDT", "binance_usdm:FUTURES:BTCUSDT", "binance_usdm: futures:BTCUSDT",
])
def test_noncanonical_cursor_refused(cursor):
    with pytest.raises(SelectionRefused) as e:
        _sel(_abc(), cursor=cursor)
    assert e.value.reason == "invalid_input"


def test_valid_cursor_absent_from_snapshot_uses_lower_bound():
    # canonical but never listed: accepted, resumes at the next ID
    absent = cid("BBCUSDT")
    assert absent not in {r.instrument_id.value for r in _abc().records}
    result = _sel(_abc(), cursor=absent)
    assert result.cursor_before == absent
    assert result.selected_id == cid("CCCUSDT")
    # a canonical ID of another venue/market is equally valid and orders by value
    spot = _id("BTCUSDT", market=MarketType.SPOT).value
    assert _sel(_abc(), cursor=spot).selected_id == cid("AAAUSDT")


def test_non_snapshot_refused():
    with pytest.raises(SelectionRefused):
        select(object(), cycle_as_of_ms=AS_OF, max_snapshot_age_ms=0,
               strategy_scan=(), cursor_before=None)


# --- observation predicate ---------------------------------------------------

def test_present_trading_candidate_selected():
    result = _sel(_snap(_rec("BTCUSDT")))
    assert result.candidate_ids == (cid("BTCUSDT"),)
    assert result.selected_id == cid("BTCUSDT")
    assert result.selected_symbol == "BTC/USDT:USDT"
    assert result.exclusions == ()


@pytest.mark.parametrize("presence,reason", [
    (Presence.ABSENT, rs.VENUE_LISTING_ABSENT), (Presence.UNKNOWN, rs.VENUE_LISTING_UNKNOWN)])
def test_listing_not_present_excluded(presence, reason):
    result = _sel(_snap(_rec("BTCUSDT", venue_listing=presence)))
    assert result.candidate_ids == ()
    assert result.exclusions == (Exclusion(cid("BTCUSDT"), reason),)
    assert result.selected_id is None


@pytest.mark.parametrize("status", ["BREAK", "SETTLING", "PENDING_TRADING", "UNKNOWN", "trading"])
def test_non_trading_status_excluded(status):
    result = _sel(_snap(_rec("BTCUSDT", venue_status=status)))
    assert result.exclusions == (Exclusion(cid("BTCUSDT"), rs.VENUE_STATUS_NOT_TRADING),)
    assert result.selected_id is None


@pytest.mark.parametrize("state", list(Eligibility))
def test_every_account_eligibility_is_observable_state(state):
    result = _sel(_snap(_rec("BTCUSDT", eligibility=state)))
    assert result.selected_id == cid("BTCUSDT")
    assert result.selected_account_eligibility == state.value


def test_eligible_gets_no_priority():
    snap = _snap(_rec("AAAUSDT"), _rec("BBBUSDT", eligibility=Eligibility.ELIGIBLE))
    assert _sel(snap).selected_id == cid("AAAUSDT")


def test_ineligible_observable_and_not_permission():
    snap = _snap(_rec("AAAUSDT", eligibility=Eligibility.INELIGIBLE))
    result = _sel(snap)
    assert result.selected_id == cid("AAAUSDT")
    assert result.selected_account_eligibility == "INELIGIBLE"
    assert snap.records[0].account_eligibility is Eligibility.INELIGIBLE
    assert "INELIGIBLE" in result.canonical_json()


def test_account_cantrade_false_does_not_remove_candidate():
    result = _sel(_snap(_rec("BTCUSDT"), trading=AccountTrading.DISABLED))
    assert result.selected_id == cid("BTCUSDT")


@pytest.mark.parametrize("over", [
    dict(symbol_config=Presence.ABSENT), dict(symbol_config=Presence.PRESENT),
    dict(leverage_bracket=Presence.ABSENT), dict(data_availability=Presence.ABSENT),
    dict(shortability=Capability.UNSUPPORTED), dict(onboard_ms=AS_OF),
    dict(constraints=OrderConstraints(minimum_notional="1000000")),
])
def test_metadata_fields_do_not_gate(over):
    assert _sel(_snap(_rec("BTCUSDT", **over))).selected_id == cid("BTCUSDT")


# --- strict transport mapping ------------------------------------------------

def test_strict_mapping_success():
    result = _sel(_snap(_rec("1000PEPEUSDT", base="1000PEPE")))
    assert result.selected_symbol == "1000PEPE/USDT:USDT"


@pytest.mark.parametrize("rec,reason", [
    (lambda: _rec("BTCUSDT", venue="bybit"), rs.VENUE_NOT_BINANCE_USDM),
    (lambda: _rec("BTCUSDT", market=MarketType.SPOT), rs.MARKET_TYPE_NOT_FUTURES),
    (lambda: _rec("BTCUSDT_260925", base="BTC", contract_type="CURRENT_QUARTER"),
     rs.CONTRACT_NOT_PERPETUAL),
    (lambda: _rec("BTCUSDT", contract_type=None), rs.CONTRACT_NOT_PERPETUAL),
    (lambda: _rec("BTCUSDC", base="BTC", quote_asset="USDC"), rs.QUOTE_NOT_USDT),
    (lambda: _rec("BTCUSDT", settlement_asset="BTC"), rs.SETTLEMENT_NOT_USDT),
    (lambda: _rec("BTCUSDT", settlement_asset=None), rs.SETTLEMENT_NOT_USDT),
    (lambda: _rec("btcUSDT", base="btc"), rs.BASE_ASSET_INVALID),
    (lambda: _rec("USDT", base=""), rs.BASE_ASSET_INVALID),
    (lambda: _rec("A" * 25 + "USDT"), rs.BASE_ASSET_INVALID),
    (lambda: _rec("BTC-XUSDT", base="BTC-X"), rs.BASE_ASSET_INVALID),
    (lambda: _rec("BTCUSDT", base="ETH"), rs.VENUE_SYMBOL_BASE_MISMATCH),
    # base is never inferred by stripping a suffix
    (lambda: _rec("BTCUSDTUSDT", base="BTC"), rs.VENUE_SYMBOL_BASE_MISMATCH),
])
def test_mapping_refusals(rec, reason):
    record = rec()
    result = _sel(_snap(record))
    assert result.candidate_ids == (record.instrument_id.value,)
    assert result.exclusions == (Exclusion(record.instrument_id.value, reason),)
    assert result.selected_id is None and result.selected_symbol is None


# --- strategy scan boundary --------------------------------------------------

def test_already_in_strategy_scan():
    result = _sel(_snap(_rec("BTCUSDT"), _rec("ETHUSDT")), ["BTC/USDT:USDT"])
    assert reasons(result) == {cid("BTCUSDT"): rs.ALREADY_IN_STRATEGY_SCAN}
    assert result.selected_id == cid("ETHUSDT")
    assert result.unmatched_scan_symbols == ()


def test_bare_scan_spelling_is_ambiguous_and_covers_nothing():
    # 'BTC/USDT' may be spot; without a market mode it cannot prove USD-M coverage
    result = _sel(_snap(_rec("BTCUSDT"), _rec("ETHUSDT")), ["BTC/USDT"])
    assert rs.ALREADY_IN_STRATEGY_SCAN not in reasons(result).values()
    assert result.selected_id == cid("BTCUSDT")
    assert result.selected_symbol == "BTC/USDT:USDT"
    assert result.unmatched_scan_symbols == ("BTC/USDT",)


def test_spot_like_scan_cannot_hide_futures_observation():
    # a scan of only spot-like spellings leaves every USD-M record observable
    snap = _snap(_rec("AAAUSDT"), _rec("BBBUSDT"))
    scan = ["AAA/USDT", "BBB/USDT"]
    first = _sel(snap, scan)
    second = _sel(snap, scan, first.cursor_after)
    assert (first.selected_id, second.selected_id) == (cid("AAAUSDT"), cid("BBBUSDT"))
    assert first.exclusions == second.exclusions == ()
    assert first.unmatched_scan_symbols == ("AAA/USDT", "BBB/USDT")
    # mixing spellings: only the futures form covers
    mixed = _sel(snap, ["AAA/USDT", "BBB/USDT:USDT"], cid("BBBUSDT"))
    assert reasons(mixed) == {cid("BBBUSDT"): rs.ALREADY_IN_STRATEGY_SCAN}
    assert mixed.selected_id == cid("AAAUSDT")
    assert mixed.unmatched_scan_symbols == ("AAA/USDT",)


def test_scan_comparison_does_not_weaken_identity():
    # a raw venue symbol, bare or other spelling in the scan covers nothing
    snap = _snap(_rec("BTCUSDT"))
    for scan in (["BTCUSDT"], [cid("BTCUSDT")], ["BTC/USDC:USDC"], ["btc/usdt"],
                 ["btc/usdt:usdt"], ["BTC/USDT"], ["BTC/USDT:BTC"], ["BTC/USDC"]):
        result = _sel(snap, scan)
        assert result.selected_id == cid("BTCUSDT")
        assert result.unmatched_scan_symbols == tuple(sorted(scan))
    # a spot-listed BTCUSDT is refused on mapping, not matched against the perp scan
    spot = _rec("BTCUSDT", market=MarketType.SPOT)
    result = _sel(_snap(spot), ["BTC/USDT", "BTC/USDT:USDT"])
    assert reasons(result) == {spot.instrument_id.value: rs.MARKET_TYPE_NOT_FUTURES}


# --- cursor semantics --------------------------------------------------------

ABC = ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")


def _abc():
    return _snap(*(_rec(s) for s in ABC))


def test_cursor_exact_hit():
    result = _sel(_abc(), cursor=cid("CCCUSDT"))
    assert result.selected_id == cid("CCCUSDT")
    assert result.cursor_after == cid("DDDUSDT")


def test_cursor_missing_id_lower_bound():
    result = _sel(_abc(), cursor=cid("BBCUSDT"))
    assert result.selected_id == cid("CCCUSDT")


def test_cursor_wrap_past_end_and_after_last():
    past = _sel(_abc(), cursor=cid("ZZZUSDT"))
    assert past.selected_id == cid("AAAUSDT")
    last = _sel(_abc(), cursor=cid("DDDUSDT"))
    assert last.selected_id == cid("DDDUSDT")
    assert last.cursor_after == cid("AAAUSDT")


def test_traversal_wraps_over_exclusions_once():
    scan = ["CCC/USDT:USDT", "DDD/USDT:USDT"]
    result = _sel(_abc(), scan, cid("CCCUSDT"))
    assert result.selected_id == cid("AAAUSDT")
    assert [e.instrument_id for e in result.exclusions] == [cid("CCCUSDT"), cid("DDDUSDT")]
    assert result.cursor_after == cid("BBBUSDT")


def test_symbol_entering_scan_at_cursor_is_skipped():
    before = _sel(_abc(), [], cid("CCCUSDT"))
    after = _sel(_abc(), ["CCC/USDT:USDT"], cid("CCCUSDT"))
    assert before.selected_id == cid("CCCUSDT")
    assert after.selected_id == cid("DDDUSDT")
    assert reasons(after) == {cid("CCCUSDT"): rs.ALREADY_IN_STRATEGY_SCAN}


def test_symbol_leaving_scan_at_cursor_becomes_selectable():
    inside = _sel(_abc(), ["CCC/USDT:USDT"], cid("CCCUSDT"))
    left = _sel(_abc(), [], cid("CCCUSDT"))
    assert inside.selected_id == cid("DDDUSDT")
    assert left.selected_id == cid("CCCUSDT")
    # leaving just behind the cursor waits for the wrap, but is not skipped forever
    behind = _sel(_abc(), [], cid("DDDUSDT"))
    assert behind.selected_id == cid("DDDUSDT")
    assert _sel(_abc(), [], behind.cursor_after).selected_id == cid("AAAUSDT")


def test_membership_insertion_and_removal_around_cursor():
    # cursor ID removed from the snapshot: resume at the next ID
    without_c = _snap(*(_rec(s) for s in ABC if s != "CCCUSDT"))
    assert _sel(without_c, cursor=cid("CCCUSDT")).selected_id == cid("DDDUSDT")
    # new ID inserted just before the cursor: not visited until the wrap
    with_bb = _snap(*(_rec(s) for s in ABC), _rec("BBZUSDT"))
    assert _sel(with_bb, cursor=cid("CCCUSDT")).selected_id == cid("CCCUSDT")
    # new ID inserted between cursor and the next candidate is next
    with_cd = _snap(*(_rec(s) for s in ABC), _rec("CCDUSDT"))
    r = _sel(with_cd, cursor=cid("CCCUSDT"))
    assert r.cursor_after == cid("CCDUSDT")
    # the last candidate removed while the cursor pointed past it wraps to the first
    without_d = _snap(*(_rec(s) for s in ABC if s != "DDDUSDT"))
    assert _sel(without_d, cursor=cid("DDDUSDT")).selected_id == cid("AAAUSDT")


def test_nothing_selectable_keeps_cursor_and_all_reasons():
    snap = _snap(_rec("AAAUSDT", venue_status="BREAK"), _rec("BBBUSDT"),
                 _rec("CCCUSDT", quote_asset="BUSD"), _rec("DDDUSDT", venue_listing=Presence.ABSENT))
    result = _sel(snap, ["BBB/USDT:USDT"], cid("BBCUSDT"))
    assert result.outcome == rs.NOTHING_SELECTABLE
    assert result.selected_id is None and result.selected_symbol is None
    assert result.cursor_after == result.cursor_before == cid("BBCUSDT")
    assert reasons(result) == {cid("AAAUSDT"): rs.VENUE_STATUS_NOT_TRADING,
                               cid("BBBUSDT"): rs.ALREADY_IN_STRATEGY_SCAN,
                               cid("CCCUSDT"): rs.QUOTE_NOT_USDT,
                               cid("DDDUSDT"): rs.VENUE_LISTING_ABSENT}
    assert _sel(_snap(), cursor=None).cursor_after is None


def test_empty_snapshot_with_cursor():
    result = _sel(_snap(), cursor=cid("BBBUSDT"))
    assert result.outcome == rs.NOTHING_SELECTABLE
    assert result.cursor_after == cid("BBBUSDT")


def test_exclusions_deterministic_and_complete():
    snap = _snap(_rec("AAAUSDT", venue_status="BREAK"), _rec("BBBUSDT"), _rec("CCCUSDT"),
                 _rec("DDDUSDT", contract_type="CURRENT_QUARTER"), _rec("EEEUSDT"))
    scan = ["CCC/USDT:USDT", "BBB/USDT:USDT"]
    results = {_sel(snap, scan, cid("BBBUSDT")) for _ in range(3)}
    assert len(results) == 1
    (result,) = results
    # predicate exclusions first, then traversal exclusions in visit order
    assert result.exclusions == (
        Exclusion(cid("AAAUSDT"), rs.VENUE_STATUS_NOT_TRADING),
        Exclusion(cid("BBBUSDT"), rs.ALREADY_IN_STRATEGY_SCAN),
        Exclusion(cid("CCCUSDT"), rs.ALREADY_IN_STRATEGY_SCAN),
        Exclusion(cid("DDDUSDT"), rs.CONTRACT_NOT_PERPETUAL),
    )
    assert result.selected_id == cid("EEEUSDT")
    # every record is classified exactly once: excluded or selected
    assert len(result.exclusions) + 1 == len(snap.records)


def test_repeated_calls_do_not_starve():
    symbols = [f"S{i:02d}USDT" for i in range(12)]
    snap = _snap(*(_rec(s) for s in symbols), _rec("ZZZUSDT", venue_status="BREAK"),
                 _rec("QQQUSDT", settlement_asset="BTC"))
    scan = ["S03/USDT:USDT", "S07/USDT:USDT"]
    selectable = {cid(s) for s in symbols} - {cid("S03USDT"), cid("S07USDT")}
    cursor, seen = cid("S05USDT"), []
    for _ in range(len(selectable)):
        result = _sel(snap, scan, cursor)
        # the caller advances regardless of whether the later fetch succeeds
        seen.append(result.selected_id)
        cursor = result.cursor_after
    assert set(seen) == selectable and len(seen) == len(set(seen))
    # the next lap repeats in the same order
    lap = []
    for _ in range(len(selectable)):
        result = _sel(snap, scan, cursor)
        lap.append(result.selected_id)
        cursor = result.cursor_after
    assert lap == seen


# --- purity ------------------------------------------------------------------

SOURCE = Path(rs.__file__)


def _imports():
    names = set()
    for node in ast.walk(ast.parse(SOURCE.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_no_network_imports():
    banned = ("socket", "http", "urllib", "requests", "ccxt", "aiohttp", "httpx",
              "websocket", "subprocess", "trader.data", "trader.observability.supplemental")
    assert not [n for n in _imports() if n.split(".")[0] in banned or n.startswith(banned)]


def test_no_kernel_import():
    assert not [n for n in _imports() if "kernel" in n]


def test_no_journal_or_storage():
    text = SOURCE.read_text()
    assert not [n for n in _imports() if "journal" in n or n in ("sqlite3", "pathlib", "os")]
    assert "open(" not in text and ".execute(" not in text


def test_no_mutation_of_inputs():
    snap = _abc()
    before = (snap.canonical_json(), snap.snapshot_id, copy.deepcopy(snap.records))
    scan = ["BBB/USDT:USDT", "CCC/USDT:USDT"]
    scan_copy = list(scan)
    _sel(snap, scan, cid("BBBUSDT"))
    assert (snap.canonical_json(), snap.snapshot_id, snap.records) == before
    assert scan == scan_copy


def test_scan_iterator_consumed_once():
    result = _sel(_abc(), iter(["AAA/USDT:USDT"]))
    assert result.selected_id == cid("BBBUSDT")
