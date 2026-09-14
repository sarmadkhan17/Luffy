"""LORD++ and the forward brake."""
import numpy as np
import pytest

from trader.research import fdr


def test_gamma_sums_to_at_most_one():
    assert sum(fdr.gamma(j) for j in range(1, 200_000)) < 1.0


def test_alpha_by_hand():
    g = fdr.gamma
    assert fdr.alpha_at(1, []) == pytest.approx(g(1) * 0.05)
    # first rejection at t=2: t=3 earns (alpha - w0) * gamma(1)
    assert fdr.alpha_at(3, [2]) == pytest.approx(g(3) * 0.05 + 0.05 * g(1))
    # second rejection at t=5: t=6 earns alpha * gamma(1) on top
    assert fdr.alpha_at(6, [2, 5]) == pytest.approx(
        g(6) * 0.05 + 0.05 * g(4) + 0.10 * g(1))
    # a rejection at or after t is not yet known at t
    assert fdr.alpha_at(2, [2]) == pytest.approx(g(2) * 0.05)


def test_alpha_never_exceeds_the_target():
    for t in range(1, 60):
        assert fdr.alpha_at(t, range(1, t)) <= 0.10 + 1e-12


def test_w0_above_alpha_is_refused():
    with pytest.raises(ValueError):
        fdr.alpha_at(1, [], alpha=0.1, w0=0.2)


def test_pure_noise_rarely_discovers():
    """All nulls: every rejection is false, so FDR = P(any rejection)."""
    rng = np.random.default_rng(0)
    any_rej = sum(any(fdr.run(rng.uniform(size=300))) for _ in range(200))
    assert any_rej / 200 <= 0.10


def test_a_mix_controls_fdr_and_keeps_power():
    rng = np.random.default_rng(1)
    fdps, found = [], 0
    for _ in range(100):
        real = rng.uniform(size=300) < 0.10
        p = np.where(real, rng.beta(0.1, 20.0, 300), rng.uniform(size=300))
        rej = np.array(fdr.run(p))
        found += int((rej & real).sum())
        fdps.append((rej & ~real).sum() / max(1, rej.sum()))
    assert np.mean(fdps) <= 0.10
    assert found > 0


def test_brake():
    assert fdr.brake_factor([True] * 4) == 1.0              # too few
    assert fdr.brake_factor([True] + [False] * 4) == 0.5    # 20% > 10%
    assert fdr.brake_factor([True] + [False] * 9) == 1.0    # 10%, not above


def test_the_sequence_survives_a_restart(tmp_path):
    from trader.core.journal import Journal
    from trader.research.ledger import Ledger
    db = str(tmp_path / "j.db")
    led = Ledger(Journal(db))
    led.ensure()
    t, a = led.next_alpha(0.10, 0.05)
    assert t == 1
    assert led.record_test("h1", "4h", "trail", "gate1", a / 2, a, False, {})
    led.record_test("h2", "4h", "trail", "gate1", 0.9, 0.01, False, {})
    again = Ledger(Journal(db))
    t3, a3 = again.next_alpha(0.10, 0.05)
    assert t3 == 3
    assert a3 == pytest.approx(fdr.alpha_at(3, [1]))
    assert again.next_alpha(0.10, 0.05, brake=0.5)[1] == pytest.approx(a3 / 2)
