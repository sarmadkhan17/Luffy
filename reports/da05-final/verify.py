"""Verify the frozen matched matrices, preserved scope and readiness evidence."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

P = Path(__file__).parent


def cases(name):
    result = {}
    for c in ET.parse(P/name).iter('testcase'):
        key = c.attrib['classname']+'::'+c.attrib['name']
        state = next((s for s in ('failure','error','skipped') if c.find(s) is not None),'passed')
        assert key not in result
        result[key] = state
    assert result
    return result


def read(name): return json.loads((P/name).read_text())


def verify():
    baseline = cases('baseline.xml') | cases('regression-before.xml') | cases('readonly-before.xml')
    final = cases('final.xml') | cases('readonly-final.xml')
    new = cases('matched-controls-before.xml')
    assert len(baseline)==325 and len(final)==345 and len(new)==20
    assert {k:final[k] for k in baseline} == baseline
    assert set(new)==set(final)-set(baseline)
    assert sum(s=='failure' for s in new.values())==13
    assert sum(s=='passed' for s in new.values())==7
    assert all(final[k]=='passed' for k in new)
    failures=[k for k,v in final.items() if v!='passed']
    assert failures==['tests.test_forming_bar_never_stored::test_incremental_fetch_re_reads_the_stored_tail']
    targeted=cases('final-targeted.xml')
    assert len(targeted)==61 and all(v=='passed' for v in targeted.values())
    before,after=read('measure-before.json'),read('measure-final.json')
    assert [r['returned'] for r in before['expansions']]==[246,300,399,846,246]
    assert [r['returned'] for r in after['expansions']]==[400,400,400,1000,250]
    for collection in (before,after):
        assert all(r['requests']==2 and r['max_request_limit']==1000 and r['restart_additional_requests']==0 for r in collection['expansions'])
        assert collection['short_fallback']['retained_candle_rows']==20
    assert not before['short_fallback']['failed_refresh_refused']
    assert after['short_fallback']['failed_refresh_refused']
    for name in ('cohort-metrics.json','cohort-final.json'):
        cohort=read(name)
        assert cohort['cohort']==439
        assert cohort['ranking_eligible_before']==20 and cohort['ranking_eligible_after']==439
        assert cohort['source_requests']==419 and cohort['max_requested_bars']<=1000
        assert cohort['queue_max_pending']<=16 and cohort['cache_series']<=128
        assert cohort['historical_attention_exact'] and cohort['historical_receipt_exact']
    retained=read('retained-readiness.json')
    assert retained['event_2338']['status_counts']=={'ok':5,'missing':407,'stale':27}
    assert retained['event_2338']['rows_exact'] and retained['event_2338']['receipt_exact']
    assert retained['da06_retained_scan']['hash_verified_source_blobs']==77
    assert retained['da06_retained_scan']['sufficient_eligible_snapshots']==0
    old=read('baseline-scope.json')
    changed=[]
    for path,expected in old.items():
        actual=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if actual!=expected:changed.append(path)
    assert changed==['trader/data/feed.py']
    for path,expected in read('raw-evidence-hashes.json').items():
        assert hashlib.sha256((P/path).read_bytes()).hexdigest()==expected,path
    result=dict(status='PASS',baseline_old_passed=324,baseline_old_failed=1,
        final_unique_passed=344,final_inherited_failed=1,matched_old_case_statuses_exact=True,
        new_controls_baseline={'passed':7,'failed':13},new_controls_final_passed=20,
        final_targeted_passed=61,read_only_feed_tests_baseline_and_final=6,
        inherited_failures=failures,runtime_changed_files=changed,
        original_439_cohort_progress_and_acquisition_budget_exact=True,
        retained_event_and_receipt_exact=True,retained_source_hashes_verified=77,
        operational_status='OPERATIONAL_VERIFICATION_REQUIRED')
    return result


if __name__=='__main__':
    print(json.dumps(verify(),indent=2))
