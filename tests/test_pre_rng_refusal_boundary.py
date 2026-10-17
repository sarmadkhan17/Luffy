"""Current pre-RNG stop: no generator, world or injection can run."""
import pytest

from trader.cognition import m32_scheme_d_validation as D


@pytest.mark.parametrize('operation', ['seed', 'world', 'injection'])
def test_pre_rng_refuses_before_any_generator_or_world_work(monkeypatch, operation):
    def forbidden(*args, **kwargs):
        pytest.fail('RNG construction reached')
    monkeypatch.setattr(D.np.random, 'default_rng', forbidden)
    monkeypatch.setattr(D.np.random, 'SeedSequence', forbidden)
    monkeypatch.setattr(D.np.random, 'PCG64', forbidden)
    if operation == 'seed':
        registry = D.SeedRegistry()
        for phase in (D.PHASE_BENCHMARK, D.PHASE_BENCHMARK, 10):
            with pytest.raises(D.IntegrityError, match='^pre_rng_rng_construction_refused$'):
                registry.generator(phase, 0, 1, 0, 0)
            assert registry._seen == set()
    elif operation == 'world':
        monkeypatch.setattr(D, 'SeedRegistry', forbidden)
        with pytest.raises(D.IntegrityError, match='^pre_rng_world_generation_refused$'):
            D.generate_world(correlation=0, dgp=1, world_index=0)
    else:
        monkeypatch.setattr(D, 'SeedRegistry', forbidden)
        with pytest.raises(D.IntegrityError, match='^pre_rng_world_generation_refused$'):
            D.apply_injections(None, 104, registry=None)
