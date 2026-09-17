"""A stored spec must enter the runtime population only via compilation."""
from types import SimpleNamespace

from trader.kernel import Kernel


def test_specs_are_not_also_loaded_as_evaluatorless_legacy_genomes():
    kernel = Kernel.__new__(Kernel)
    legacy = dict(id="legacy", name="legacy", state="demoted", kind="ema_trend",
                  params="{}", hypothesis="", invalidation="", regime_filter="[]",
                  markets='["futures"]', generation=0)
    kernel.journal = SimpleNamespace(list_strategies=lambda _: [
        legacy, dict(id="compiled", kind="spec"), dict(id="invalid", kind="spec")])
    compiled = (SimpleNamespace(id="compiled"), SimpleNamespace(family="spec:compiled"))
    kernel._load_spec_population = lambda: [compiled]
    population = kernel._load_population()
    assert [st.id for st, _ in population] == ["legacy", "compiled"]
    assert population[-1] is compiled
    assert not population[0][0].is_trade_eligible
