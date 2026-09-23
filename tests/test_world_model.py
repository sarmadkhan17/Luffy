"""Focused contracts for the point-in-time hierarchy index."""

from dataclasses import FrozenInstanceError

import pytest

from trader.world import (HierarchyNode, Horizon, Observation, Quality, Scope,
                          ScopeLevel, WorldModel, WorldState)


GLOBAL = Scope(ScopeLevel.GLOBAL, "world")
ASSET = Scope(ScopeLevel.ASSET_CLASS, "digital assets")
GROUP = Scope(ScopeLevel.GROUP, "large caps")
BTC = Scope(ScopeLevel.INSTRUMENT, "BTC")


def hierarchy(group=True):
    return [HierarchyNode(GLOBAL), HierarchyNode(ASSET, GLOBAL),
            *([HierarchyNode(GROUP, ASSET)] if group else []),
            HierarchyNode(BTC, GROUP if group else ASSET)]


def observation(horizon="short-term", value="weak"):
    return Observation(instrument="BTC", timestamp_ms=100, observed_at_ms=150,
                       available_at_ms=120, timeframe="1h", horizon=horizon,
                       kind="trend", value=value, source="test", source_ref="input",
                       quality=Quality.VALID)


def state(*observations, cut=200):
    return WorldState("BTC", cut, observations)


def test_immutable_scope_hierarchy_and_order_independent_identity():
    nodes = hierarchy()
    states = [state(observation(), observation("structural", "strong"))]
    horizons = [Horizon.STRUCTURAL, Horizon.SHORT_TERM]
    a = WorldModel(200, nodes, states, horizons)
    b = WorldModel(200, list(reversed(nodes)), list(reversed(states)),
                   list(reversed(horizons)))
    assert a.to_json() == b.to_json()
    assert a.model_id == b.model_id
    for item, field in ((GLOBAL, "identifier"), (nodes[0], "parent"),
                        (a, "as_of_ms")):
        with pytest.raises(FrozenInstanceError):
            setattr(item, field, "changed")
    nodes.clear()
    states.clear()
    horizons.clear()
    assert len(a.nodes) == 4 and len(a.states) == 1 and len(a.horizons) == 2
    record = a.to_dict()
    record["nodes"].clear()
    assert len(a.nodes) == 4


def test_deterministic_parent_children_and_optional_group():
    model = WorldModel(200, hierarchy())
    assert model.children(GLOBAL) == (ASSET,)
    assert model.children(ASSET) == (GROUP,)
    assert model.children(GROUP) == (BTC,)
    assert model.parent(BTC) == GROUP
    assert model.ancestors(BTC) == (GROUP, ASSET, GLOBAL)
    assert model.parent(GLOBAL) is None
    assert model.get_state(GLOBAL) is None
    assert model.get_observations(GLOBAL, Horizon.SWING) == ()
    no_group = WorldModel(200, hierarchy(group=False))
    assert no_group.parent(BTC) == ASSET
    assert no_group.children(ASSET) == (BTC,)


@pytest.mark.parametrize("nodes", [
    [HierarchyNode(GLOBAL), HierarchyNode(GLOBAL)],
    [HierarchyNode(GLOBAL), HierarchyNode(GROUP, ASSET)],
    [HierarchyNode(ASSET, GLOBAL)],
    [HierarchyNode(GLOBAL), HierarchyNode(Scope(ScopeLevel.GLOBAL, "other"))],
])
def test_invalid_or_ambiguous_hierarchy_rejected(nodes):
    with pytest.raises(ValueError):
        WorldModel(200, nodes)


def test_invalid_parent_levels_rejected():
    with pytest.raises(ValueError):
        HierarchyNode(BTC, GLOBAL)
    with pytest.raises(ValueError):
        HierarchyNode(GROUP, GROUP)
    with pytest.raises(ValueError):
        HierarchyNode(ASSET, None)


def test_cut_identity_and_state_node_integrity():
    base = state(observation())
    with pytest.raises(ValueError, match="as_of_ms"):
        WorldModel(201, hierarchy(), [base])
    with pytest.raises(ValueError, match="as_of_ms"):
        WorldModel(199, hierarchy(), [base])
    with pytest.raises(ValueError, match="duplicate instrument"):
        WorldModel(200, hierarchy(), [base, base])
    with pytest.raises(ValueError, match="no hierarchy node"):
        WorldModel(200, hierarchy(group=False)[:-1], [base])
    with pytest.raises(ValueError, match="not declared"):
        WorldModel(200, hierarchy(), [state(observation("structural"))],
                   [Horizon.SHORT_TERM])


def test_horizons_coexist_and_exact_queries_preserve_ambiguity():
    short = observation()
    structural = observation("structural", "strong")
    duplicate = observation(value="mixed")
    underlying = state(short, structural, duplicate)
    before = (underlying.to_json(), short.to_json(), structural.to_json())
    model = WorldModel(200, hierarchy(), [underlying])
    assert model.get_state(BTC) is underlying
    assert model.get_observations(BTC, Horizon.SHORT_TERM, kind="trend") == \
        underlying.get_observations("trend", horizon="short-term")
    assert model.get_one(BTC, Horizon.STRUCTURAL, kind="trend") is structural
    assert model.get_observations(BTC, Horizon.SWING, kind="trend") == ()
    with pytest.raises(LookupError, match="found 2"):
        model.get_one(BTC, Horizon.SHORT_TERM, kind="trend")
    with pytest.raises(LookupError, match="unknown scope"):
        model.get_state(Scope(ScopeLevel.INSTRUMENT, "ETH"))
    assert (underlying.to_json(), short.to_json(), structural.to_json()) == before


def test_material_changes_change_identity():
    base_state = state(observation())
    base = WorldModel(200, hierarchy(), [base_state])
    assert base.model_id != WorldModel(201, hierarchy(), [state(observation(), cut=201)]).model_id
    assert base.model_id != WorldModel(200, hierarchy(group=False), [base_state]).model_id
    assert base.model_id != WorldModel(200, hierarchy(), [base_state],
                                     [Horizon.SHORT_TERM]).model_id
    assert base.model_id != WorldModel(200, hierarchy(),
                                     [state(observation(value="strong"))]).model_id
    assert base.model_id != WorldModel(200, hierarchy(), []).model_id
