"""ATT-01: attention picks where to investigate from real typed triggers; offline."""
import json
import re
from pathlib import Path

import pytest

from trader import attention_admission as A
from trader.core.journal import Journal
from tests.test_hierarchical_admission import CUT, observation, policy, cfg, offline  # noqa: F401


def test_event_creates_ranked_typed_candidates_subset_of_eligible(policy):
    o = observation(30)
    r = A.admit(o, policy, A.initial_state())
    c = A.candidates(r)
    assert [x['rank'] for x in c] == list(range(1, 13))
    assert [x['symbol'] for x in c] == r['admitted_symbols']
    assert len(r['roles']['broad_crypto']) == 30 and len(c) == 12          # eligible universe != subset
    for x in c:
        assert x['symbol'] in o['rows'] and x['reasons'] and x['category']
        assert x['source_identity'] == 'bulk-v1' and x['source_cut_ms'] == CUT and x['receipt_id'] == r['receipt_id']
        assert x['cost']['deep_slots'] == 1 and x['cost']['wall_time']['status'] == 'NOT_MEASURED'
        assert x['urgency']['kind']
    ev = [x for x in c if x['category'] == 'event']
    assert len(ev) == 2 and all(x['urgency']['kind'] == 'new_event' and x['urgency']['signal_age_ms'] == 0 for x in ev)
    assert 'new_salience_observation_at_or_above_existing_threshold' in ev[0]['reasons']


def test_warmup_is_not_negative_salience(policy):
    r = A.admit(observation(10, scores=False), policy, A.initial_state())
    assert all(x['score'] is None and x['score_status'] == 'warmup' for x in r['rows'])
    assert not r['roles']['event'] and r['reserved_unused']['event'] == 2     # no event invented, none zeroed
    c = A.candidates(r)
    assert c and all(x['score'] is None and x['urgency']['signal_age_ms'] is None for x in c)
    assert all('warmup' in x['reasons'] for x in c)
    assert {x['category'] for x in c} <= {'exploration', 'warmup_relevant'}


def test_zero_candidates_is_valid_current_and_distinct_from_refused(policy, tmp_path):
    o = observation(8, scores=False, relevant=False)
    for row in o['rows'].values():
        row['quality'] = 'STALE'
    j = Journal(tmp_path / 'z.db')
    r = A.persist(j, o, policy)
    assert A.candidates(r) == []
    v = A.owner_view(j, now_ms=CUT, stale_seconds=300)
    assert v['status'] == 'current' and v['deep_admitted'] == 0 and v['broad_observed'] == 8
    j.log_brain_event('attention_admission_refused', 'attention', {'reason': 'capture_incomplete'})
    v = A.owner_view(j, now_ms=CUT, stale_seconds=300)
    assert v['status'] == 'refused' and v['reason'] != 'no_admission_receipt'


def test_no_receipt_is_unavailable_not_zero(tmp_path):
    v = A.owner_view(Journal(tmp_path / 'n.db'), now_ms=CUT, stale_seconds=300)
    assert v['status'] == 'unavailable'


def test_incomplete_observation_refuses_instead_of_empty(policy):
    o = observation(5)
    del o['source_cut_ms']
    with pytest.raises(ValueError):
        A.admit(o, policy, A.initial_state())


def test_candidates_grant_no_trade_or_size_authority(policy):
    c = A.candidates(A.admit(observation(), policy, A.initial_state()))
    assert {x['authority'] for x in c} == {'INVESTIGATE_ONLY'}
    banned = re.compile(r'size|qty|quantity|leverage|notional|order|side|direction|action', re.I)
    assert not [k for x in c for k in x if banned.search(k)]
    src = Path(A.__file__).read_text()
    assert not re.search(r'^\s*(from|import)\s+.*\b(executor|risk|exits|orchestrator)\b', src, re.M)


def test_deterministic_replay_reproduces_selection_and_reasons(policy, tmp_path):
    r1 = A.admit(observation(), policy, A.initial_state())
    r2 = A.admit(json.loads(A.canonical(observation())), policy, A.initial_state())
    assert A.canonical(A.candidates(r1)) == A.canonical(A.candidates(r2))
    j = Journal(tmp_path / 'r.db')
    r3 = A.persist(j, observation(), policy)
    assert A.canonical(A.candidates(A.latest(j))) == A.canonical(A.candidates(r3)) == A.canonical(A.candidates(r1))


def test_tampered_receipt_cannot_yield_candidates(policy):
    r = A.admit(observation(), policy, A.initial_state())
    r['admitted_symbols'] = list(reversed(r['admitted_symbols']))
    with pytest.raises(ValueError):
        A.candidates(r)
