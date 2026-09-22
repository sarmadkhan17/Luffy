"""Pure, fail-closed M3.2 evidence validation; no collection or search authority."""
import math

from . import dataset as D, outcomes as O


def pit_regime(row):
    """Validate a retained registration receipt, never reconstruct a past label.

    The classifier must publish this receipt at registration. Inputs are full
    closed-bar versions, not pointers to today's candle store. Its digest binds
    the label, classifier version, input values and original availability clocks.
    This contract does not itself attest classifier accuracy or independence.
    """
    f, clocks = row.get('features', {}), row.get('clocks', {})
    r = f.get('regime_receipt')
    if not row.get('point_in_time_features') or not isinstance(r, dict):
        return 'unknown'
    try:
        registration = clocks['registered_ms']
        if (r['schema_version'] != 'pit-regime-receipt.v1'
                or r['symbol'] != row['symbol'] or r['registered_ms'] != registration
                or f['as_of_ms'] != registration
                or not isinstance(r['classifier_version'], str) or not r['classifier_version'].strip()
                or not isinstance(r['label'], str) or not r['label'].strip()
                or r['label'].lower() == 'unknown'
                or type(r['recorded_ms']) is not int or not 0 <= r['recorded_ms'] <= registration
                or r['sha256'] != O.digest({k: v for k, v in r.items() if k != 'sha256'})
                or not isinstance(r['inputs'], list) or not r['inputs']):
            return 'unknown'
        versions = set()
        for b in r['inputs']:
            if (not isinstance(b['version_id'], str) or not b['version_id']
                    or b['version_id'] in versions or b['symbol'] != row['symbol']
                    or any(type(b[k]) is not int for k in ('open_ms', 'close_ms', 'available_ms'))
                    or not 0 <= b['open_ms'] < b['close_ms'] <= b['available_ms'] <= r['recorded_ms']
                    or any(type(b[k]) not in (int, float) or not math.isfinite(b[k])
                           for k in ('open', 'high', 'low', 'close', 'volume'))
                    or min(b[k] for k in ('open', 'high', 'low', 'close')) <= 0
                    or b['volume'] < 0
                    or not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']):
                return 'unknown'
            versions.add(b['version_id'])
        return r['label']
    except (KeyError, TypeError, ValueError):
        return 'unknown'


def independence_groups(rows, pad=14_400_000):
    """Merge global dependencies; never split pre-existing groups.

    Administrative period names/independent_of assertions grant no independence.
    Missing parents, conflicting duplicate rows or incomplete clocks fail closed.
    Counts are conservative component upper bounds, not measured effective N.
    """
    before = len({r.get('dependence_group', 'unknown') for r in rows})
    result = dict(additional_groups_built=0, effective_groups_before=before,
                  effective_groups_after=0, groups={}, row_groups={},
                  independence_established=False, refusal=None)
    try:
        if type(pad) is not int or pad < 0:
            raise ValueError('invalid_dependence_padding')
        unique = {}
        for row in rows:
            rid = row['row_id']
            if not isinstance(rid, str) or not rid:
                raise ValueError('missing_row_identity')
            if rid in unique and unique[rid] != row:
                raise ValueError('conflicting_duplicate_row')
            unique[rid] = row
        uf = D._Groups()
        for rid in unique:
            uf.add(rid)
        identities, spans, periods = {}, [], []
        for rid, r in sorted(unique.items()):
            f, c, label = r['features'], r['clocks'], r['labels']
            lo = f['as_of_ms']
            times = [lo, label['available_ms'], c['known_ms'], c['registered_ms']]
            if any(type(t) is not int or t < 0 for t in times) or max(times[1:3]) < lo:
                raise ValueError('invalid_dependence_clocks')
            hi = max(times)
            if f.get('deadline_ms') is not None:
                if type(f['deadline_ms']) is not int:
                    raise ValueError('invalid_dependence_clocks')
                hi = max(hi, f['deadline_ms'])
            spans.append((lo, hi, rid))
            if not r.get('dependence_group') or r['dependence_group'] == 'unknown':
                raise ValueError('missing_original_group')
            keys = [('group', r['dependence_group'])]
            if r.get('case_id'): keys.append(('case', r['case_id']))
            if r.get('underlying_id'): keys.append(('underlying', r['symbol'], r['underlying_id']))
            if not r.get('case_id') or not r.get('underlying_id'):
                raise ValueError('missing_case_identity')
            # Shared exact input versions are dependencies even across calendar cuts.
            keys.extend(('input', x) for x in f.get('source_versions', []))
            for key in keys:
                uf.union(rid, identities.setdefault(key, rid))
            parents = set(r.get('parent_row_ids', []))
            parents.update(f[k] for k in ('prior_row_id', 'current_row_id') if f.get(k))
            if r.get('shared_underlying_row_id'): parents.add(r['shared_underlying_row_id'])
            for parent in parents:
                if parent not in unique:
                    raise ValueError('missing_cross_window_parent')
                uf.union(rid, parent)
            p = r.get('independence_provenance') or {}
            # Optional actual period bounds can only widen dependence.
            if 'start_ms' in p or 'end_ms' in p:
                a, b = p['start_ms'], p['end_ms']
                if type(a) is not int or type(b) is not int or not 0 <= a <= lo <= hi <= b:
                    raise ValueError('invalid_collection_period_bounds')
                periods.append((a, b, rid))
        intervals = sorted(spans + periods)
        end, anchor = -1, None
        for lo, hi, rid in intervals:
            if anchor is not None and lo <= end + pad:
                uf.union(rid, anchor)
            else:
                anchor = rid
            end = max(end, hi)
        roots = {}
        mapping = {rid: roots.setdefault(uf.find(rid), 'g%04d' % len(roots)) for rid in sorted(unique)}
        counts = {}
        for group in mapping.values(): counts[group] = counts.get(group, 0) + 1
        result.update(effective_groups_after=len(counts), groups=counts, row_groups=mapping)
    except (KeyError, TypeError, ValueError) as exc:
        result['refusal'] = str(exc) if isinstance(exc, ValueError) else 'incomplete_dependence_evidence'
    return result


def complete_actual(record):
    """Only the canonical replay contract can certify an execution outcome."""
    try:
        O.replay(record)
        a = record.get('actual_execution') or {}
        return (record['kind'] == 'executed_trade' and a.get('status') == 'verified_actual'
                and type(a.get('net_pnl')) in (int, float) and math.isfinite(a['net_pnl']))
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError):
        return False


def execution_identity(record):
    s = record['source']
    if 'whole_trade_capture' in s:
        a = s['whole_trade_capture']
        return (a['venue'], a['environment'], a['bookings']['trade']['symbol'],
                a['bookings']['trade']['id'])
    r = s['registration']
    return r['venue'], r['environment'], r['symbol'], r['trade_id']


def eligible_actuals(rows, envelopes, declaration):
    """Join only PIT execution episodes, deduplicate trade identity, enforce cut.

    No new row or new outcome is spliced into the frozen artifact. This returns
    an audit overlay; unknown labels in the original artifact remain unchanged.
    """
    accepted, refused, versions = {}, {}, {}
    def reject(reason): refused[reason] = refused.get(reason, 0) + 1
    for e in envelopes:
        record = e.get('record')
        if not complete_actual(record):
            reject('incomplete_authoritative_outcome'); continue
        try:
            identity = execution_identity(record)
            local = e['local_imported_ms']
            if type(local) is not int or local < record['imported_ms']:
                raise ValueError('local_import_clock_invalid')
            known = max(local, *(record[k] for k in ('resolved_ms', 'available_ms', 'imported_ms')))
            if known >= declaration['discovery_cut_ms']:
                raise ValueError('accounting_not_known_before_cut')
            if identity[2] not in declaration['universe'] or record['registered_ms'] < declaration['start_ms']:
                raise ValueError('accounting_outside_declaration')
            capture = record['source'].get('whole_trade_capture')
            if capture and str(capture['bookings']['trade'].get('strategy_id', '')).startswith('diagnostic:'):
                raise ValueError('diagnostic_execution_excluded')
            matches = [r for r in rows if r.get('row_type') != 'sequence'
                       and r.get('point_in_time_features') is True
                       and r.get('producer') == 'verified_execution'
                       and (r.get('features', {}).get('venue'), r.get('features', {}).get('environment'),
                            r.get('symbol'), r.get('underlying_id')) == identity
                       and r.get('clocks', {}).get('registered_ms') == record['registered_ms']
                       and r.get('clocks', {}).get('known_ms', declaration['discovery_cut_ms']) < declaration['discovery_cut_ms']]
            if not matches:
                raise ValueError('no_eligible_pit_execution_episode')
            # Conflicting receipt versions refuse the entire trade, not first-wins.
            version = O.digest(record['source'])
            versions.setdefault(identity, set()).add(version)
            accepted[identity] = record
        except (KeyError, TypeError, ValueError) as exc:
            reject(str(exc) if isinstance(exc, ValueError) else 'accounting_join_invalid')
    for identity, seen in versions.items():
        if len(seen) > 1:
            accepted.pop(identity, None); reject('conflicting_accounting_receipts')
    return accepted, refused
