"""Legacy labels cannot write learning authority, even after repeated calls."""
from types import SimpleNamespace
import pytest
from trader.agents import calibration, weights_online, validate
from trader.brain import meta_label
from trader.core.journal import Journal
from trader.kernel import Kernel
from trader.learning import foundation as L
from trader.strategy import promotion
from tests.test_promotion_rules import _mk, _losses
from tests.test_learning_foundation import chain  # noqa: F401


@pytest.mark.parametrize('module,entry', [
    (calibration, 'refit'), (weights_online, 'update'), (meta_label, 'refit')])
def test_legacy_model_writers_preserve_existing_bytes(tmp_path, monkeypatch, module, entry):
    path = tmp_path / 'state.json'
    original = b'{"updated":0,"agents":{},"legacy_marker":true}'
    path.write_bytes(original)
    monkeypatch.setattr(module, 'PATH', path)
    journal = SimpleNamespace(query=lambda *a: pytest.fail('legacy labels were read before refusal'))
    for _ in range(2):
        with pytest.raises(ValueError, match='replay_complete_learning_authority_required'):
            getattr(module, entry)(journal)
        assert path.read_bytes() == original


@pytest.mark.parametrize('module', [weights_online, meta_label])
def test_direct_legacy_save_refuses(tmp_path, monkeypatch, module):
    path = tmp_path / 'state.json'
    monkeypatch.setattr(module, 'PATH', path)
    with pytest.raises(ValueError, match='replay_complete_learning_authority_required'):
        module._save({'quality': 'VERIFIED_REPLAY'})
    assert not path.exists()


def test_static_validation_cannot_publish_weights(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='replay_complete_learning_authority_required'):
        validate.run({}, [], None)
    assert not (tmp_path / 'data' / 'agent_weights.json').exists()


@pytest.mark.parametrize('state', ['paper', 'active', 'demoted'])
def test_legacy_losses_cannot_mutate_lifecycle(tmp_path, state):
    j = Journal(tmp_path / 'j.db')
    _mk(j, 'legacy', state)
    _losses(j, 'legacy', 8)
    before = j.query('SELECT state,state_changed_at,retire_reason FROM strategies')[0]
    for _ in range(2):
        assert promotion.evaluate_population(j) == []
        assert j.query('SELECT state,state_changed_at,retire_reason FROM strategies')[0] == before
    assert not j.query("SELECT * FROM brain_events WHERE kind='statistical_transition'")
    assert j.query('SELECT stats_json FROM strategies')[0]['stats_json'] != '{}'


def test_kernel_legacy_decay_cannot_retire(tmp_path, monkeypatch):
    from trader.brain import analyst, ideas
    j = Journal(tmp_path / 'j.db')
    _mk(j, 'legacy', 'paper')
    spec = SimpleNamespace(id='legacy', name='legacy')
    monkeypatch.setattr(j, 'list_specs', lambda *a: [(None, spec)])
    monkeypatch.setattr(analyst.Analyst, 'review_deployed', lambda *a: [
        {'spec': 'legacy', 'name': 'legacy', 'evidence': {'verdict': 'decayed'}}])
    monkeypatch.setattr(analyst.Analyst, 'flush_health_observations', lambda *a: True)
    monkeypatch.setattr(ideas, 'pending', lambda *a: [])
    k = Kernel.__new__(Kernel)
    k.journal, k.cfg, k.feed, k.notifier = j, {}, None, None
    before = j.query('SELECT state,retire_reason FROM strategies')[0]
    for _ in range(2):
        assert k._mechanism_once(max_book=1)['retired'] == []
        assert j.query('SELECT state,retire_reason FROM strategies')[0] == before


def test_registered_decay_rejects_incomplete_before_evaluator(chain, monkeypatch):
    from tests.test_learning_foundation import get_evidence
    outcome, sources, current, _ = chain
    sources = dict(sources)
    sources.pop(('data', 'old-v1'))
    ev = get_evidence(outcome, sources)
    assert ev.quality == 'NON_AUTHORITATIVE'
    monkeypatch.setattr(L, 'evaluate_decay', lambda *a: pytest.fail('incomplete evidence evaluated'))
    proposal = L.propose(ev, L.Target.LIFECYCLE, current, rule=L.DECAY_RULE)
    assert proposal.status == L.Status.INCOMPLETE
    assert proposal.proposed_json is None
