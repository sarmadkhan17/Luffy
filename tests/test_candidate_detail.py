"""Bounded, indexed, paged, read-only owner detail of one candidate (exact formula/models/thresholds/lineage)."""
import hashlib
import json
import sqlite3
import time

import pytest

from trader.core.journal import Journal
from trader.owner import queries as Q
from trader.research.closure import auto, canonical, digest
from trader.research.closure.store import Store


def build(tmp, n_models=400, versions=2, weird=False):
    st = Store(tmp / 'd.closure')
    cand = dict(candidate_id='c1', question='does context lead', structure='long_short', long={'cmp': 'gt', 'a': {'f': 'x'}}, short=None)
    st.put_once('candidates', 'c1', cand)
    note = 'quote " back\\slash \x01 ctrl é 数据 \U0001F600' * 50 if weird else 'plain'
    big = dict(models=[dict(artifact_id=f'm{i:04d}', weights=[0.1 * i] * 3, note=note) for i in range(n_models)], walk_forward=[], thresholds=None)
    for k in range(versions):
        run = dict(stage='DEVELOPMENT', candidate_id='c1', envelope_id='e', outcome='INCONCLUSIVE_POWER', reason=f'run {k}',
                   combo=dict(long=f'close > {k}', short=None), combo_hash=f'h{k}', threshold_fit={'x': dict(p10=1.0)},
                   graph_fit=big if k == versions - 1 else dict(models=[], walk_forward=[]), result_id=f'r{k:03d}' + 'a' * 20, data_id='d',
                   hedge=None, requires=['ohlcv'], metrics=dict(trades=3))
        st.put_run(f'k{k}', run)
    st.put_once('runs', 'other', dict(stage='DEVELOPMENT', candidate_id='c2', envelope_id='e', result_id='x'))
    st.close()
    return tmp / 'd.closure'


def reassemble(path, section, **kw):
    first = auto.candidate_detail(path, 'c1', section, 0, **kw)
    parts = [first] + [auto.run_detail(path, first['run_id'], section, i) for i in range(1, first['parts'])]
    return ''.join(p['text'] for p in parts), parts


def test_manifest_then_exact_paged_sections_reassemble_without_loss(tmp_path):
    path = build(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    man = auto.candidate_detail(path, 'c1')
    assert man['status'] == 'AVAILABLE' and man['results']['total'] == 2 and man['results_may_be_incomplete'] is False
    assert man['sections']['models']['parts'] > 1 and man['sections']['evaluated']['parts'] == 1
    text, parts = reassemble(path, 'models')
    models = json.loads(text)
    assert len(models['models']) == 400 and models['models'][399]['artifact_id'] == 'm0399'      # the whole block, not a hash
    assert digest(models) == parts[0]['sha256']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before                              # read-only
    ev = json.loads(auto.candidate_detail(path, 'c1', 'evaluated', 0)['text'])
    assert ev['combo']['long'] == 'close > 1' and ev['threshold_fit'] == {'x': dict(p10=1.0)}      # newest result by default


def test_non_ascii_quotes_backslashes_and_controls_reassemble_exactly_and_every_chunk_fits_the_wire_budget(tmp_path):
    path = build(tmp_path, n_models=25, weird=True)
    text, parts = reassemble(path, 'models')
    assert len(parts) > 2                                                                         # more than two parts
    assert all('\ufffd' not in p['text'] for p in parts)                                          # never a replacement character
    models = json.loads(text)                                                                     # exact reconstruction of valid JSON
    assert models['models'][24]['note'].startswith('quote " back')
    assert digest(models) == parts[0]['sha256']
    for p in parts:
        envelope = json.dumps(dict(records=[dict(value=p)]))                                       # what the owner envelope actually carries
        assert len(json.dumps(p['text'])) - 2 <= auto.WIRE_CHUNK_BYTES and len(envelope.encode()) < 16000
    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def test_an_older_result_is_selectable_and_more_than_a_page_of_versions_is_paged_with_a_true_total(tmp_path):
    path = build(tmp_path, n_models=10, versions=45)
    man = auto.candidate_detail(path, 'c1')
    assert man['results']['total'] == 45 and len(man['results']['items']) == auto.RESULT_PAGE and man['results']['more_results'] is True
    page2 = json.loads(auto.candidate_detail(path, 'c1', 'results', 1)['text'])
    assert page2['offset'] == 20 and len(page2['items']) == 20 and page2['more_results'] is True
    page3 = json.loads(auto.candidate_detail(path, 'c1', 'results', 2)['text'])
    assert len(page3['items']) == 5 and page3['more_results'] is False
    oldest = page2['items'][-1]['run_id']
    older = auto.run_detail(path, oldest, 'evaluated', 0)
    assert older['status'] == 'AVAILABLE' and older['run_id'] == oldest
    assert json.loads(older['text'])['combo']['long'] == 'close > 5'
    assert auto.candidate_detail(path, 'c2', 'evaluated', 0, result=oldest)['status'] == 'ABSENT'


def test_absent_store_unknown_candidate_and_bad_arguments_are_refused_without_writes(tmp_path):
    with pytest.raises(ValueError, match='closure_store_not_found'):
        auto.candidate_detail(tmp_path / 'nope.closure', 'c1')
    assert not (tmp_path / 'nope.closure').exists()                                              # never creates a store
    path = build(tmp_path)
    assert auto.candidate_detail(path, 'zzz')['status'] == 'ABSENT'
    with pytest.raises(ValueError, match='section_unknown'):
        auto.candidate_detail(path, 'c1', 'nonsense')
    with pytest.raises(ValueError, match='part_out_of_range'):
        auto.candidate_detail(path, 'c1', 'evaluated', 9)
    with pytest.raises(ValueError, match='candidate_id_invalid'):
        auto.candidate_detail(path, 'x' * 200)


def test_oversize_records_and_slow_statements_are_reported_truthfully_not_decoded(tmp_path, monkeypatch):
    path = build(tmp_path, n_models=50)
    monkeypatch.setattr(auto, 'MAX_RECORD_BYTES', 60)
    out = auto.candidate_detail(path, 'c1')
    assert out['status'] == 'TOO_LARGE' and out['what'] == 'candidate' and out['bytes'] > 60
    monkeypatch.setattr(auto, 'MAX_RECORD_BYTES', 8 * 1024 * 1024)
    monkeypatch.setattr(auto, 'DETAIL_SECONDS', -1.0)                                            # the statement budget is already spent
    assert auto.candidate_detail(path, 'c1')['status'] == 'TIME_LIMIT'
    assert auto.run_detail(path, 'k1', 'evaluated')['status'] == 'TIME_LIMIT'


def test_limits_are_applied_in_sql_not_after_decoding_every_row(tmp_path):
    path = build(tmp_path, n_models=5, versions=60)
    seen = []
    orig = json.loads
    import trader.research.closure.auto as A
    class Counter:
        def loads(self, s, *a, **k):
            seen.append(len(s))
            return orig(s, *a, **k)
    monkey = A.json
    A.json = Counter()                                                                           # count decoded records
    try:
        A.json.dumps = json.dumps
        auto.candidate_detail(path, 'c1')
    finally:
        A.json = monkey
    assert len(seen) <= 1 + auto.RESULT_PAGE + 12 + 1                                            # candidate + one page + <=12 lineage, not 60+


def test_lookup_uses_the_index_and_a_legacy_store_is_scanned_only_over_a_bounded_window(tmp_path):
    path = build(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM run_index')                                                     # a store written before the index
    legacy = auto.candidate_detail(path, 'c1')
    assert legacy['results_may_be_incomplete'] is True and legacy['results']['total'] == 2
    auto.LEGACY_SCAN_RUNS, keep = 1, auto.LEGACY_SCAN_RUNS
    try:
        assert auto.candidate_detail(path, 'c1')['results']['total'] <= 1                       # bounded, and says so
    finally:
        auto.LEGACY_SCAN_RUNS = keep


def test_owner_query_route_returns_the_exact_section_and_reports_unconfigured_store(tmp_path):
    path = build(tmp_path)
    j = Journal(tmp_path / 'j.db')
    cfg = dict(research=dict(closure=dict(store_path=str(path))))
    out = Q.query(j, 'research', 'closure-candidate:c1:evaluated:0', cfg=cfg, root=tmp_path)
    [rec] = [r for r in out['records'] if r['kind'] == 'closure_candidate']
    assert json.loads(rec['value']['text'])['combo']['long'] == 'close > 1'
    none = Q.query(j, 'research', 'closure-candidate:c1:evaluated:0', cfg={}, root=tmp_path)
    assert not none['records'] and any('closure_store_not_configured' in str(m) for m in (none.get('unavailable') or []))
    absent = Q.query(j, 'research', 'closure-candidate:c1', cfg=dict(research=dict(closure=dict(store_path=str(tmp_path / 'x')))), root=tmp_path)
    assert not absent['records']


def test_exact_run_query_accepts_full_ids_and_pins_old_content_after_a_new_run(tmp_path):
    candidate, old, new = 'a' * 64, 'b' * 64, 'c' * 64
    path = tmp_path / 'full.closure'
    st = Store(path)
    st.put_once('candidates', candidate, dict(candidate_id=candidate, question='q'))
    def result(value):
        return dict(candidate_id=candidate, result_id=digest(value), stage='DEVELOPMENT', outcome='NEGATIVE_ECONOMICS',
                    combo=dict(long=f'close > {value}'), graph_fit=dict(models=[]), data_id='d')
    st.put_run(old, result(1))
    st.put_run(new, result(2))
    st.close()
    identity = f'closure-run:{old}:evaluated:0'
    assert len(identity) <= 128
    j = Journal(tmp_path / 'j.db')
    cfg = dict(research=dict(closure=dict(store_path=str(path))))
    answer = Q.query(j, 'research', identity, cfg=cfg, root=tmp_path)
    [item] = [r for r in answer['records'] if r['kind'] == 'closure_candidate']
    assert item['value']['run_id'] == old
    assert json.loads(item['value']['text'])['combo']['long'] == 'close > 1'
    assert auto.run_detail(path, 'd' * 64, 'evaluated')['status'] == 'ABSENT'


def test_catalogue_never_decodes_result_payloads_and_exact_read_decodes_only_one(tmp_path, monkeypatch):
    path = build(tmp_path, n_models=30, versions=45)
    original = json.loads
    decoded = []
    def loads(raw, *args, **kw):
        obj = original(raw, *args, **kw)
        if isinstance(obj, dict) and obj.get('stage') == 'DEVELOPMENT':
            decoded.append(obj['result_id'])
        return obj
    monkeypatch.setattr(json, 'loads', loads)
    auto.candidate_detail(path, 'c1', 'results', 1)
    assert decoded == []
    detail = auto.run_detail(path, 'k0', 'evaluated')
    assert detail['status'] == 'AVAILABLE' and len(decoded) == 1
