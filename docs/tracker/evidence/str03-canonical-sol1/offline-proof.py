"""Independent stored-chain restart and approval immutability observation."""
from pathlib import Path
from datetime import datetime, timezone
import json, sqlite3, subprocess, tempfile
import pytest
from trader.core.journal import Journal
from trader.strategy import factory_handoff as F
from tests.authority_factory_fixtures import cfg, _journal, _approved, INPUTS, T0, DAY

EP = Path(__file__).resolve().parent
def observed_processes():
    lines = subprocess.check_output(['ps', '-eo', 'pid,args'], text=True).splitlines()
    return [line for line in lines if any((' -m ' + name) in line for name in ['trader.kernel', 'trader.dashboard.server'])]
assert not observed_processes()
mp = pytest.MonkeyPatch()
try:
    config = cfg.__wrapped__(mp)
    with tempfile.TemporaryDirectory(prefix='str03-independent-', dir='/mnt/luffy-data/test-tmp') as tmp:
        j, h = _journal(Path(tmp))
        v, p, d = _approved(j, config)
        tables = ['strategy_approval_requests', 'strategy_approval_decisions']
        before = {t: j.query('SELECT * FROM ' + t) for t in tables}
        refusals = []
        for table in tables:
            for sql in ['UPDATE ' + table + ' SET canonical_sha256=canonical_sha256', 'DELETE FROM ' + table]:
                try:
                    with j._tx() as c:
                        c.execute(sql)
                except sqlite3.IntegrityError as e:
                    assert 'immutable' in str(e)
                    refusals.append(dict(sql=sql, reason=str(e)))
                else:
                    raise AssertionError('immutable write permitted')
        again = Journal(Path(tmp) / 'j.db')
        assert before == {t: again.query('SELECT * FROM ' + t) for t in tables}
        req = F.approval_request(again, v['version_id'])
        assert F._jsha({k: req[k] for k in F._REQUEST_KEYS}) == req['request_id']
        retry = F.record_owner_decision(again, config, p['request_id'], 'APPROVED', actor='operator', decided_at_ms=T0 + 31 * DAY)
        assert retry['status'] == 'duplicate' and retry['decision_id'] == d['decision_id']
        assert before == {t: again.query('SELECT * FROM ' + t) for t in tables}
        use = F.eligible_for_first_live(again, v['version_id'], cfg=config, available_inputs=INPUTS)
        assert use.reasons == ('capacity_receipt_not_asserted',) and not use.eligible
        result = dict(approval_row_update_delete_refusals=refusals, actual_row_values_equal_after_restart_retry=True,
            request_identity_rederived=True, retry_status='duplicate', exact_decision_identity_equal=True,
            eligibility_retains_capacity_gate=True, no_activation_performed=True,
            temporary_journal_only=True, provider_calls=0)
finally:
    mp.undo()
assert not observed_processes()
result.update(observed_at_utc=datetime.now(timezone.utc).isoformat(), kernel_dashboard_processes=[],
    watchdog_off_present=Path('data/watchdog.off').exists())
assert result['watchdog_off_present']
(EP / 'offline-proof.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
