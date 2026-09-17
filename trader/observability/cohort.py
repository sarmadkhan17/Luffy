"""Classification-neutral receipt review. No rates, search or admission authority."""
from collections import Counter, defaultdict

from trader.cognition import dataset as D


def counts(values):
    return dict(sorted(Counter(values).items()))


def review(events, declaration, now, local_indices):
    """Pure replay over retained receipts and read-only local index snapshots.

    Rows are registration-anchored; orphan updates/results remain explicit gaps.
    Classification and missing outcomes never determine cohort inclusion.
    """
    version = D.declaration_version(declaration)
    start, cut = declaration['start_ms'], declaration['discovery_cut_ms']
    grouped, scans, gaps = defaultdict(list), [], []
    reconciliation, verified = {}, {}
    for stream in ('forecast', 'investigation'):
        exports = {e['id']: e for e in events if e['stream'] == stream}
        index = local_indices.get(stream)
        if index is None:
            reconciliation[stream] = {'status': 'local_index_unavailable',
                                       'exported': len(exports)}
            continue
        indexed = {r['id']: r for r in index}
        if len(indexed) != len(index):
            raise ValueError('cohort_duplicate_index')
        for key in exports.keys() & indexed.keys():
            e, r = exports[key], indexed[key]
            stable = {k: v for k, v in e.items() if k not in
                      ('local_imported_ms', 'available_ms', 'producer_code_hash', 'sha256')}
            if r['hash'] != D.digest(stable) or r['local_imported_ms'] != e['local_imported_ms']:
                raise ValueError('population_local_receipt_mismatch')
            verified[key] = r['local_imported_ms']
        missing = sorted(indexed.keys() - exports.keys())
        reconciliation[stream] = {
            'status': 'matched' if set(indexed) == set(exports) else 'receipt_gap',
            'indexed': len(index), 'exported': len(exports),
            'missing_exports': [k for k in missing if not indexed[k]['pending']],
            'pending_exports': [k for k in missing if indexed[k]['pending']],
            'exports_absent_from_index': sorted(exports.keys() - indexed.keys())}
    for e in events:
        if (e['declaration_version'] != version or e['stream'] not in ('forecast', 'investigation')
                or max(e['available_ms'], e['local_imported_ms']) > now):
            raise ValueError('cohort_receipt_clock_or_binding')
        kind, source = e['kind'], e['source']
        if kind in ('registration', 'update', 'terminal'):
            if e['stream'] == 'forecast':
                key = source.get('episode_id', source.get('id'))
            else:
                key = source['investigation']['investigation_id']
            grouped[e['stream'], key].append(e)
        elif kind == 'population':
            scans.append(e)
        elif kind in ('gap', 'activation'):
            if kind == 'gap' or source.get('missed_registration_interval'):
                gaps.append({'event_id': e['id'], 'stream': e['stream'], 'source': source})
    rows, orphans = [], []
    for (stream, key), history in sorted(grouped.items()):
        regs = [e for e in history if e['kind'] == 'registration']
        terminals = [e for e in history if e['kind'] == 'terminal']
        if not regs:
            orphans.append({'stream': stream, 'episode_id': key,
                            'event_ids': sorted(e['id'] for e in history),
                            'reason': 'missing_registration_receipt'})
            continue
        if len(regs) != 1 or len(terminals) > 1:
            raise ValueError('cohort_duplicate_episode')
        reg = regs[0]
        source = reg['source']
        if stream == 'forecast':
            frozen = source['prediction']
            symbol, registered = source['symbol'], source['registered_ms']
            deadline = frozen['target_open_ms'] + 4*60*60*1000
            selected, reason = frozen['selected'], frozen['attention_reason']
            underlying = key
        else:
            frozen = source['investigation']
            symbol, registered = frozen['state']['symbol'], frozen['registered_ms']
            deadline = frozen['measurement']['deadline_ms']
            # Investigation selection is distinct from the scanner's selection.
            selected, reason = None, frozen['primary_trigger']
            underlying = frozen['episode_id']
        if not start <= registered < cut or symbol not in declaration['universe']:
            raise ValueError('cohort_registration_outside_declaration')
        latest = max(history, key=lambda e: (e['available_ms'], e['id']))
        terminal = terminals[0] if terminals else None
        latest = terminal or latest
        if latest['source'].get('updates'):
            evidence = latest['source']['updates'][-1]['evidence']
        else:
            evidence = latest['source'].get('outcome')
        status = ('resolved' if stream == 'forecast' and terminal and terminal['source']['status'] == 'resolved'
                  else 'unavailable' if stream == 'forecast' and terminal
                  else evidence['status'] if stream == 'investigation' and evidence else 'unresolved')
        known = max(latest['available_ms'], verified.get(latest['id'], now))
        rows.append({'stream': stream, 'episode_id': key, 'underlying_id': underlying,
                     'symbol': symbol, 'registered_ms': registered, 'deadline_ms': deadline,
                     'selected': selected, 'selection_reason': reason,
                     'status': status, 'terminal': bool(terminal),
                     'mature_at_capture': now >= deadline,
                     'registration_event_id': reg['id'], 'latest_event_id': latest['id'],
                     'event_ids': sorted(e['id'] for e in history),
                     'registration_known_ms': max(reg['available_ms'], verified.get(reg['id'], now)),
                     'latest_known_ms': known, 'terminal_known_by_cut': bool(terminal and known <= cut and
                         max(reg['available_ms'], verified.get(reg['id'], now)) <= cut),
                     'evidence': evidence,
                     'assessment': latest['source']['updates'][-1]['assessment']
                         if stream == 'investigation' and latest['source']['updates'] else None})
    registered_keys = {(r['stream'], r['episode_id']) for r in rows}
    scan_summary = {}
    for stream in ('forecast', 'investigation'):
        receipts = sorted((e for e in scans if e['stream'] == stream), key=lambda e: e['id'])
        decisions = [r for e in receipts for r in e['source']['rows']]
        unmatched = [{'event_id': e['id'], 'symbol': r['symbol'], 'episode_id': r.get('episode_id')}
                     for e in receipts for r in e['source']['rows']
                     if r.get('declared_eligible') and r.get('registration_reason') == 'registered'
                     and (stream, r.get('episode_id')) not in registered_keys]
        observed = sorted({e['source']['observed_ms'] for e in receipts})
        boundary = [start, *observed, min(now, cut)] if now >= start else []
        scan_summary[stream] = {
            'observation_gap_intervals_ms': [[a, b] for a, b in zip(boundary, boundary[1:]) if b-a > 600_000],
            'activation_receipts': sorted(e['id'] for e in events if e['stream'] == stream and e['kind'] == 'activation'),
            'observations': len(receipts), 'unique_scan_ids': len({e['source']['scan_id'] for e in receipts}),
            'observed_rows': len(decisions),
            'declared_eligible_rows': sum(bool(r.get('declared_eligible')) for r in decisions),
            'selection_counts': counts('selected' if r.get('selected') is True else
                                      'ignored' if r.get('selected') is False else 'unknown' for r in decisions),
            'registration_reasons': counts(r.get('registration_reason', 'unknown') for r in decisions),
            'attention_reasons': counts(r.get('reason', 'unknown') for r in decisions),
            'missing_symbols': [{'event_id': e['id'], 'symbols': e['source']['missing_symbols']}
                                for e in receipts if e['source']['missing_symbols']],
            'unmatched_registration_claims': unmatched}
    # A sweep conservatively merges overlapping padded episode windows across
    # both streams/symbols; it does not assert statistical independence.
    group, end = -1, -1
    pad = declaration['dependence']['window_pad_ms']
    for row in sorted(rows, key=lambda r: (r['registered_ms'], r['stream'], r['episode_id'])):
        if group < 0 or row['registered_ms'] > end + pad:
            group += 1
        end = max(end, row['deadline_ms'], row['latest_known_ms'])
        row['dependence_group'] = 'g%04d' % group
    return {'schema_version': 'pit-population-cohort.v1', 'rows': rows,
            'counts_by_stream': {s: counts(r['status'] for r in rows if r['stream'] == s)
                                 for s in ('forecast', 'investigation')},
            'selection_counts': counts('selected' if r['selected'] is True else
                                      'ignored' if r['selected'] is False else 'investigation' for r in rows),
            'scan_coverage': scan_summary, 'receipt_reconciliation': reconciliation,
            'orphan_results': orphans, 'explicit_gaps': sorted(gaps, key=lambda g: g['event_id']),
            'missing_target_receipts': sorted((e['id'] for e in events if e['kind'] == 'missing')),
            'window_status': 'not_started' if now < start else 'open' if now < cut else 'cut_reached_review_required',
            'dependence_groups': group+1, 'complete_sampling_claim': False, 'search_ready': False,
            'denominator': 'registration_receipts_only; repeated_scan_rows_are_not_episodes',
            'accounting': 'observed_evidence_not_execution_pnl; missing_accounting_unknown'}
