"""Typed, replayable outcomes. Observed prices are never execution profits."""
import base64
import zlib
import hashlib
import json
import math
from datetime import datetime

from trader.cognition import forecast_protocol as L

SCHEMA = 'typed-outcome.v1'
LINK_TYPES = {'failure', 'regime', 'pattern', 'cross_market', 'strategy', 'decision',
              'investigation', 'owner_preference', 'doctrine_proposal'}


def digest(value):
    return hashlib.sha256(L.encode(value).encode()).hexdigest()


def timestamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('unknown_source_timezone')
    return int(dt.timestamp()*1000)


def _record(kind, source, registered, resolved, available, imported, observation=None,
            simulation=None, actual=None, links=()):
    if not all(type(t) is int and t >= 0 for t in (registered, resolved, available, imported)):
        raise ValueError('invalid_outcome_clock')
    if not registered <= resolved <= available <= imported:
        raise ValueError('invalid_outcome_chronology')
    for link in links:
        if (set(link) not in ({'kind', 'id', 'version', 'available_ms'},
                              {'kind', 'id', 'version', 'available_ms', 'phase'}) or
                link.get('phase', 'registration') not in ('registration', 'outcome') or
                link['kind'] not in LINK_TYPES or not link['id'] or not link['version'] or
                type(link['available_ms']) is not int or link['available_ms'] < 0 or
                link['available_ms'] > (imported if link.get('phase') == 'outcome' else registered)):
            raise ValueError('invalid_or_future_memory_link')
    fields = dict(schema_version=SCHEMA, kind=kind, source=source,
                  source_version=digest(source), registered_ms=registered, resolved_ms=resolved,
                  available_ms=available, imported_ms=imported, observation=observation,
                  simulation=simulation, actual_execution=actual, links=list(links))
    # JSON detaches all caller-owned mutable inputs.
    return json.loads(L.encode(dict(case_id=digest(fields), **fields)))


def forecast(episode, imported_ms, links=()):
    """Replay the frozen embedded baseline and target; no nearby-bar lookup."""
    p, o = episode['prediction'], episode['outcome']
    if (episode['status'] != 'resolved' or episode['protocol_id'] != L.PROTOCOL_ID or
            p['protocol_id'] != L.PROTOCOL_ID or not o):
        raise ValueError('forecast_not_resolved_or_protocol_mismatch')
    if 'source_receipt' in episode:
        expected = ([{'scan_id':p['scan_id'],'bar':b} for b in p['input_window']] +
                    [{'scan_id':o['scan_id'],'bar':o['target']}])
        if episode['source_receipt'] != expected:
            raise ValueError('forecast_source_receipt_mismatch')
    registered, observed = p['registered_ms'], p['observed_ms']
    window = p['input_window']
    target_open = (registered//L.TF+1)*L.TF
    expected_id = digest([L.PROTOCOL_ID, episode['symbol'], target_open])
    if (episode['id'] != expected_id or episode['created_ms'] != registered or
            episode['deadline_ms'] != target_open+L.TF or p['target_open_ms'] != target_open or
            not observed <= registered < target_open or len(window) != 6 or
            p['neutral_bps'] != L.PROTOCOL['neutral_bps'] or type(p['selected']) is not bool):
        raise ValueError('forecast_registration_mismatch')
    if (any(not L.usable(b, observed) or b['symbol'] != episode['symbol'] or not b['version_id'] for b in window)
            or len({b['version_id'] for b in window}) != 6 or
            any(b['open_ms']-a['open_ms'] != L.TF for a,b in zip(window,window[1:])) or
            window[-1]['open_ms'] != observed//L.TF*L.TF-L.TF):
        raise ValueError('forecast_baseline_versions_invalid')
    recent = (window[-1]['close']/window[0]['close']-1)*10000
    if (not recent or p['baseline_close'] != window[-1]['close'] or
            p['recent_move_bps'] != recent or p['direction'] != (1 if recent > 0 else -1)):
        raise ValueError('forecast_frozen_baseline_mismatch')
    t = o['target']
    if (t['symbol'] != episode['symbol'] or t['open_ms'] != target_open or not t['version_id'] or
            not L.usable(t, o['observed_ms']) or
            not registered < o['observed_ms'] <= o['recorded_ms'] <= imported_ms):
        raise ValueError('forecast_exact_target_or_clock_mismatch')
    move = (t['close']/p['baseline_close']-1)*10000
    signed = move*p['direction']
    supports = 'persistence' if signed > p['neutral_bps'] else 'reversal' if signed < -p['neutral_bps'] else 'unresolved'
    if (o['price_change_bps'] != move or o['supports'] != supports or
            o['measurement'] != 'observed_price_change_not_pnl'):
        raise ValueError('forecast_terminal_measurement_mismatch')
    return _record('selected_forecast' if p['selected'] else 'ignored_forecast', episode,
                   registered, o['recorded_ms'], max(t['available_ms'],o['recorded_ms']), imported_ms,
                   observation={'price_change_bps': move, 'supports': supports,
                                'measurement': 'observed_price_change_not_pnl'}, links=links)


def journal_record(decision, trade, imported_ms, links=()):
    """Legacy journal accounting has no complete fill/fee/funding attribution.

    Its first import is the knowledge clock. Never infer availability from an
    old decision date, or promote its realized_pnl default/estimate to actual.
    """
    if trade:
        if (trade['decision_id'] != decision['id'] or trade['symbol'] != decision['symbol'] or
                trade['status'] != 'closed' or not trade['closed_at']):
            raise ValueError('journal_trade_join_or_status')
        resolved = timestamp(trade['closed_at'])
        kind = 'executed_trade'
    else:
        if decision['executed']:
            raise ValueError('executed_decision_requires_trade')
        resolved = timestamp(decision['ts'])
        kind = 'skip'
    registered = timestamp(decision['ts'])
    return _record(kind, {'decision': decision, 'trade': trade}, registered, resolved,
                   imported_ms, imported_ms,
                   actual={'status': 'unknown', 'net_pnl': None,
                           'reason': 'missing_verified_fill_fee_funding_attribution'}, links=links)


def counterfactual(registration, target, imported_ms, links=()):
    """One frozen close-to-close simulation, explicitly not achievable execution.

    Missed-opportunity classification is retrospective and becomes known only
    at import. Costs must be specified before the registered target opens.
    """
    r = registration
    if (r['schema_version'] != 'close-counterfactual.v1' or r['decision_kind'] not in ('skip','missed_opportunity') or
            (type(r['direction']) is not int or r['direction'] not in (-1,1)) or not r['registered_ms'] < r['target_open_ms'] or
            not L.usable(r['baseline'], r['registered_ms']) or
            target['symbol'] != r['baseline']['symbol'] or target['open_ms'] != r['target_open_ms'] or
            not r['baseline']['version_id'] or not target['version_id'] or
            not L.usable(target, imported_ms) or
            not all(type(r[k]) in (int,float) and math.isfinite(r[k]) and r[k]>=0
                    for k in ('cost_bps','notional'))):
        raise ValueError('counterfactual_contract_or_target_mismatch')
    if 'declaration' in r:
        d = r['declaration']
        if (r['declaration_version'] != digest(d) or
                any(r[k] != d[k] for k in ('direction','cost_bps','notional')) or
                d['decision_id'] != r['decision']['id'] or r['decision']['executed'] or
                r['decision']['symbol'] != r['baseline']['symbol'] or
                timestamp(r['decision']['ts']) > r['registered_ms']):
            raise ValueError('nontrade_declaration_receipt_mismatch')
        derived = [dict(kind='decision',id=r['decision']['id'],version=digest(r['decision']),
                        available_ms=r['registered_ms'])]
        if links and list(links) != derived:
            raise ValueError('nontrade_reference_mismatch')
        links = derived
    move = (target['close']/r['baseline']['close']-1)*10000
    net = (move*r['direction']-r['cost_bps'])/10000*r['notional']
    return _record(r['decision_kind'], {'registration': r, 'target': target}, r['registered_ms'],
                   imported_ms, imported_ms, imported_ms,
                   observation={'price_change_bps': move, 'measurement': 'observed_price_change_not_pnl'},
                   simulation={'status': 'simulated', 'net_pnl': net, 'cost_bps': r['cost_bps'],
                               'model': 'close-counterfactual.v1', 'executable_fill_claim': False}, links=links)


def verified_execution(registration, accounting, imported_ms, links=()):
    """Accept only complete attributed venue accounting; absence stays unknown.

    Upstream reconciliation owns venue truth. This adapter verifies the joins,
    clocks and arithmetic of its frozen receipt, not live venue availability.
    """
    r, a = registration, accounting
    actual = {'status': 'unknown', 'net_pnl': None, 'reason': 'incomplete_accounting'}
    complete = (a.get('schema_version') == 'execution-accounting.v1' and a.get('complete') is True
                and a.get('trade_id') == r['trade_id'] and a.get('symbol') == r['symbol']
                and a.get('venue') == r['venue'] and a.get('environment') == r['environment']
                and a.get('reconciliation_version') and a.get('fills') and a.get('funding_complete') is True)
    if complete:
        fills = a['fills']
        if (len({f['id'] for f in fills}) != len(fills) or
                any(not f['version'] or f['trade_id'] != r['trade_id'] or
                    f['currency'] != a['currency'] or
                    not r['registered_ms'] <= f['event_ms'] <= f['available_ms'] <= a['resolved_ms']
                    for f in fills)):
            raise ValueError('execution_fill_attribution_invalid')
        values = [a['funding_net'], *[v for f in fills for v in (f['realized_pnl'],f['commission'])]]
        if not all(type(v) in (int,float) and math.isfinite(v) for v in values):
            raise ValueError('execution_nonfinite_accounting')
        actual = {'status': 'verified_actual', 'net_pnl': sum(f['realized_pnl']-f['commission'] for f in fills)+a['funding_net'],
                  'currency': a['currency'], 'environment': r['environment'],
                  'reconciliation_version': a['reconciliation_version']}
    return _record('executed_trade', {'registration':r,'accounting':a}, r['registered_ms'],
                   a['resolved_ms'], a['available_ms'], imported_ms, actual=actual, links=links)


def replay(record):
    """Rebuild without external mutable data, including after source retention."""
    s, now, links = record['source'], record['imported_ms'], record['links']
    if record['kind'] in ('selected_forecast','ignored_forecast'):
        rebuilt = forecast(s,now,links)
    elif 'whole_trade_capture' in s:
        from trader.engine.trade_accounting import verified_outcome
        rebuilt = verified_outcome(s['whole_trade_capture'], now)
    elif 'investigation_case' in s:
        rebuilt = investigation_case(s['investigation_case'], record['kind'], now)
    elif 'journal_reference' in s:
        rebuilt = linked_journal(s['journal_reference']['decision'], s['journal_reference']['trade'], now)
    elif 'decision' in s:
        rebuilt = journal_record(s['decision'],s['trade'],now,links)
    elif 'accounting' in s:
        rebuilt = verified_execution(s['registration'],s['accounting'],now,links)
    else:
        rebuilt = counterfactual(s['registration'],s['target'],now,links)
    if rebuilt != record:
        raise ValueError('typed_outcome_replay_mismatch')
    return rebuilt


CASE_PROTOCOL = 'observable-path-cases.v1'


def investigation_case(archive, kind, imported_ms):
    """Derived labels on registered alternatives, not a claim a trade was signalled.

    Reconstruct the exact baseline and terminal measurement from embedded inputs.
    A false signal means only that the same-direction alternative was contradicted.
    Regime means the registered volatility dimension, not a learned regime model.
    """
    from trader.cognition import investigation as I, memory as M
    archive = unpack_case(archive)
    inv = I.investigation_from_dict(archive['investigation'])
    update = I.update_from_dict(archive['update'])
    bars = [I.InputBar(b['version_id'], I.Candle(**b['candle'])) for b in archive['inputs']]
    case = M.verified_case(inv, update, bars, imported_ms)
    if kind == 'false_signal':
        if case.winner == 'same_direction':
            raise ValueError('persistence_not_contradicted')
    elif kind == 'regime_transition':
        if case.family != 'volatility_transition':
            raise ValueError('not_volatility_transition')
    else:
        raise ValueError('unsupported_investigation_case')
    links = [dict(kind='investigation', id=inv.investigation_id,
                  version=digest(archive['investigation']), available_ms=inv.registered_ms)]
    links.append(dict(kind='failure' if kind == 'false_signal' else 'regime',
                      id=update.event_id, version=digest(archive['update']),
                      available_ms=case.available_ms, phase='outcome'))
    # Cohort membership and exact baseline versions were frozen at registration.
    if len(inv.state.cohort) > 1:
        cohort = dict(membership=inv.state.membership_json, cohort=list(inv.state.cohort),
                      input_versions=list(inv.state.input_versions), config_id=inv.state.config_id)
        links.append(dict(kind='cross_market', id=inv.state.state_id,
                          version=digest(cohort), available_ms=inv.state.available_ms))
    return _record(kind, {'protocol': CASE_PROTOCOL, 'investigation_case': pack_case(archive)},
                   case.registered_ms, case.resolved_ms, case.available_ms, imported_ms,
                   observation=dict(measurement='observed_path_not_pnl', symbol=case.symbol,
                                    family=case.family, sign=case.sign, winner=case.winner,
                                    score=case.evidence['score'], protocol=CASE_PROTOCOL,
                                    meaning='registered_alternative_not_trade_or_causal_claim'), links=links)


def linked_journal(decision, trade, imported_ms):
    """Populate observed decision/strategy attribution references at first import.

    A journal strategy ID is not a retained strategy definition. Version the actual
    attribution receipt and explicitly leave the absent definition version unknown.
    """
    base = journal_record(decision, trade, imported_ms)
    ids = decision.get('strategy_ids') or []
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except ValueError:
            ids = [v for v in ids.split(',') if v]
    if not isinstance(ids, list) or any(not isinstance(v, str) for v in ids):
        raise ValueError('invalid_strategy_attribution')
    refs = [dict(kind='decision', id=decision['id'], version=digest(decision),
                 available_ms=imported_ms, phase='outcome')]
    receipts = []
    for sid in sorted(set(ids + ([trade['strategy_id']] if trade and trade.get('strategy_id') else []))):
        receipt = dict(strategy_id=sid, decision_id=decision['id'],
                       definition_version=None, scope='journal_attribution_only',
                       decision_version=digest(decision))
        receipts.append(receipt)
        refs.append(dict(kind='strategy', id=sid, version=digest(receipt),
                         available_ms=imported_ms, phase='outcome'))
    return _record(base['kind'], {'journal_reference':dict(decision=decision, trade=trade),
                                  'strategy_attributions':receipts},
                   base['registered_ms'], base['resolved_ms'], imported_ms, imported_ms,
                   actual=base['actual_execution'], links=refs)


MAX_CASE_RAW = 2*1024*1024


def pack_case(archive):
    raw = L.encode(archive).encode()
    if len(raw) > MAX_CASE_RAW:
        raise ValueError('case_raw_capacity')
    return dict(encoding='zlib-base64.v1',sha256=hashlib.sha256(raw).hexdigest(),
                data=base64.b64encode(zlib.compress(raw)).decode('ascii'))


def unpack_case(archive):
    if 'encoding' not in archive:
        if len(L.encode(archive).encode()) > MAX_CASE_RAW:
            raise ValueError('case_raw_capacity')
        return archive
    if archive['encoding'] != 'zlib-base64.v1' or len(archive['data']) > 65536:
        raise ValueError('case_encoding_or_capacity')
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(base64.b64decode(archive['data'],validate=True),MAX_CASE_RAW+1)
        if (len(raw)>MAX_CASE_RAW or not decoder.eof or decoder.unused_data or
                hashlib.sha256(raw).hexdigest()!=archive['sha256']):
            raise ValueError('case_archive_integrity_or_capacity')
        return json.loads(raw)
    except (zlib.error, UnicodeError) as exc:
        raise ValueError('case_archive_encoding') from exc
