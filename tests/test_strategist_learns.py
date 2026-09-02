"""The Strategist must be told what measurement established.

Two things in the prompt actively taught the failing shape:

  * the worked example ended `"trail": {"kind":"none"}` with a fixed RR
    target and `max_bars: 32`. Every sweep run with that geometry scored at
    or below the no-edge line, and the one mechanism that beat its controls
    needed no target, a 4 ATR trail and a 500-bar hold. The example was
    anchoring on the shape that does not work.

  * `universe` was hardcoded to include/exclude empty with a zero liquidity
    floor, so no generated spec could declare where it wanted to trade — the
    scan planner then had nothing to act on, and MAGMA/USDT (-$199.24, the
    largest single loss on record) stayed eligible.

The measured facts are properties of the search space, not market opinions
that the Theorist should be free to overwrite, so they belong in the prompt
rather than only in doctrine.
"""
from trader.brain.spec_writer import _prompt


def _p():
    return _prompt(None, None, [])


def test_the_example_no_longer_anchors_on_a_dead_trail():
    assert '"trail": {"kind":"none"}' not in _p()


def test_the_prompt_teaches_that_a_rotation_null_is_the_bar():
    p = _p().lower()
    assert "rotation" in p or "null" in p
    assert "1.15" not in p or "null" in p


def test_the_prompt_carries_the_always_long_control_result():
    """PF alone cannot separate edge from drift; the number makes that concrete."""
    assert "always-long" in _p().lower()


def test_the_prompt_states_that_one_direction_alone_failed():
    p = _p().lower()
    assert "both" in p and ("direction" in p or "directions" in p)


def test_the_prompt_still_names_the_cost_asymmetry_between_timeframes():
    assert "18.5%" in _p() and "7.8%" in _p()


def test_the_writer_may_declare_a_universe():
    assert "min_volume_usdt" in _p()


def test_the_prompt_still_asks_for_json_only():
    p = _p()
    assert "JSON ONLY" in p and '"entry_long"' in p and '"invalidation"' in p


def test_an_idea_and_doctrine_still_reach_the_prompt():
    p = _prompt({"source": "src", "title": "t", "text": "body text"},
                {"beliefs": [{"id": "b1", "belief": "something measured"}]},
                ["Existing Strategy"])
    assert "body text" in p and "something measured" in p
    assert "Existing Strategy" in p


# ── a declared universe must survive into the spec ───────────────────────
# _to_spec hardcoded universe={"include": [], "exclude": [],
# "min_volume_usdt": 0}, so whatever the writer declared was discarded and
# the scan planner had nothing to act on.

def test_a_declared_universe_reaches_the_spec():
    from trader.brain.spec_writer import _to_spec
    raw = {"name": "Range Break Continuation",
           "thesis": "A break of a multi-week range persists because risk is "
                     "repriced slowly and participants scale in over days.",
           "invalidation": "Retire below profit factor 1.0 over 30 trades.",
           "timeframe": "4h", "direction": "both",
           "entry_long": "close > donchian_hi(100)",
           "entry_short": "close < donchian_lo(100)", "filters": [],
           "regime_filter": [],
           "universe": {"include": ["BTC/USDT"], "exclude": ["MAGMA/USDT"],
                        "min_volume_usdt": 100000000},
           "exit": {"stop": {"kind": "atr", "mult": 2.5},
                    "target": {"kind": "none"},
                    "trail": {"kind": "atr", "mult": 3.0},
                    "time": {"max_bars": 200}}}
    spec = _to_spec(raw, {"source_kind": "test"})
    assert spec.universe["include"] == ["BTC/USDT"]
    assert spec.universe["exclude"] == ["MAGMA/USDT"]
    assert spec.universe["min_volume_usdt"] == 100000000


def test_an_omitted_universe_still_yields_a_usable_default():
    from trader.brain.spec_writer import _to_spec
    raw = {"name": "Some Mechanism",
           "thesis": "A thesis long enough to satisfy the minimum length rule "
                     "that the spec validator enforces on every strategy.",
           "invalidation": "Retire below profit factor 1.0 over 30 trades.",
           "timeframe": "4h", "direction": "long",
           "entry_long": "close > ema(50)", "entry_short": "", "filters": [],
           "regime_filter": [], "exit": {}}
    u = _to_spec(raw, {}).universe
    assert u["include"] == [] and u["exclude"] == []
    assert isinstance(u["min_volume_usdt"], (int, float))
