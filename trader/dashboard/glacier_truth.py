"""Read-only owner projections. No lifecycle, provider or control operations."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import time


def runtime_truth(journal, cfg, now: datetime) -> dict:
    from trader.engine.evidence_capture import SNAPSHOT_KV, load_json, verify_snapshot
    snap = verify_snapshot(load_json(journal.kv_get(SNAPSHOT_KV)))
    at = snap.get('observed_at_ms') if snap else None
    age = (now.timestamp() * 1000 - at) / 1000 if type(at) in (int, float) else None
    fresh = age is not None and 0 <= age <= 60
    brain = (cfg or {}).get('brain')
    # Mirrors LLM-02's configuration boundary; credentials/configuration do
    # not establish network availability. Never instantiate a provider here.
    disabled = isinstance(brain, dict) and brain.get('enabled', True) is not True
    return dict(
        account=dict(venue=snap.get('venue') if fresh else None,
                     environment=snap.get('environment') if fresh else None,
                     observed_at_ms=snap.get('observed_at_ms') if snap else None,
                     snapshot_id=snap.get('snapshot_id') if snap else None,
                     freshness='fresh' if fresh else 'stale' if age is not None and age >= 0 else 'unavailable',
                     source='state_kv.venue_position_snapshot; verified snapshot and observation identities'),
        brain=dict(provider_state='DISABLED' if disabled else 'UNKNOWN',
                   external_llm='UNAVAILABLE' if disabled else 'UNKNOWN',
                   response_mode='EXTERNAL_CHAT_UNAVAILABLE' if disabled else 'NOT_ESTABLISHED',
                   source='config.brain.enabled; configuration only, no provider health call'))


def versioned_strategies(journal, cfg, now_ts) -> dict:
    from trader.strategy import factory_handoff as f
    from trader.owner.approvals import items
    from .owner_reads import _table_exists
    result, errors = [], {}
    if not _table_exists(journal, 'strategy_versions'):
        return dict(versions=None, authority_errors={'versions': 'version_ledger_not_reported'})
    # Reuse the existing exact-version approval validity reader, one exact
    # request at a time so its bounded first page cannot hide a decision.
    for row in journal.query('SELECT * FROM strategy_versions ORDER BY recorded_at_ms DESC'):
        vid = row['version_id']
        entry = dict(version_id=vid, strategy_id=row['strategy_id'], created_at_ms=row['recorded_at_ms'],
                     version=None, validation=None, install=None, probation=None,
                     approval=None, governor=None, lifecycle=None, economics=None, errors={})
        def read(key, fn):
            try:
                entry[key] = fn()
            except Exception as e:
                entry['errors'][key] = str(e) if isinstance(e, ValueError) else type(e).__name__
        read('version', lambda: f.load_version(journal, vid))
        v = entry['version']
        if v is not None:
            read('validation', lambda: f.verify_validation(journal, v))
            read('install', lambda: f.verify_install(journal, v, current=True))
            def probation():
                rows = journal.query('SELECT receipt_id FROM strategy_probation_receipts WHERE version_id=? ORDER BY recorded_at_ms DESC LIMIT 1', (vid,))
                return f._verify_probation(journal, cfg, v, rows[0]['receipt_id']) if rows else None
            read('probation', probation)
            def approval():
                req = f.approval_request(journal, vid)
                if req is None:
                    return None
                feed = items(journal, cfg, now_ms=int(now_ts*1000), identity=req['request_id'])
                return next((x for x in feed['items'] if x['item_id'] == req['request_id']), None)
            read('approval', approval)
            read('governor', lambda: (f.governor_events(journal, vid) or [None])[-1])
            # Factory phase and Governor lifecycle are distinct reads. Never
            # elevate registry or Factory ACTIVE-like labels to order permission.
            read('lifecycle', lambda: f.state_of(journal, vid))
            governor = entry['governor']
            if governor and governor.get('capacity_receipt_id'):
                from trader.strategy import capacity
                def economics():
                    rid = governor['capacity_receipt_id']
                    receipt = capacity.load(journal, rid)
                    if (receipt['version_id'], receipt['spec_hash']) != (vid, v['spec_hash']):
                        raise ValueError('capacity_receipt_wrong_version')
                    return dict(receipt_id=rid, historical_result=receipt['result'],
                                current=capacity.check_current(journal, cfg, v, rid, now_ms=int(now_ts*1000)))
                read('economics', economics)
        result.append(entry)
    return dict(versions=result, authority_errors=errors)


def filesystems(data: Path) -> list[dict]:
    """Actual mount identity for root, data and the recovery mount separately."""
    from trader.core.config import load_config
    recovery = Path(load_config().get('deployment', {}).get(
        'recovery_root', '/mnt/luffy-recovery'))
    mounts = []
    try:
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields = line.split()
            sep = fields.index('-')
            unescape = lambda s: s.replace('\\040', ' ').replace('\\134', '\\')
            mounts.append((Path(unescape(fields[4])), unescape(fields[sep+2])))
    except (OSError, ValueError, IndexError):
        pass
    out = []
    for label, path in (('Root /', Path('/')), ('LUFFY data', data), ('Recovery', recovery)):
        rec = dict(label=label, path=str(path), total=None, used=None, free=None,
                   percent_free=None, mount=None, source=None, status='NOT_REPORTED')
        try:
            if not path.exists():
                out.append(rec)
                continue
            resolved = path.resolve()
            candidates = [(m, s) for m, s in mounts if resolved == m or m in resolved.parents]
            mount, source = max(candidates, key=lambda x: len(str(x[0]))) if candidates else (None, None)
            # A missing recovery mount must not masquerade as root capacity.
            if label == 'Recovery' and (mount is None or mount == Path('/')):
                out.append(rec)
                continue
            usage = shutil.disk_usage(path)
            rec.update(total=usage.total, used=usage.used, free=usage.free,
                       percent_free=usage.free / usage.total * 100 if usage.total else None,
                       mount=str(mount) if mount else None, source=source, status='OBSERVED',
                       observed_at_ms=int(time.time()*1000))
        except OSError:
            pass
        out.append(rec)
    return out
