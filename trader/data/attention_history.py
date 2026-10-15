"""Bounded Attention history preparation through the existing DataFeed queue.

No ranking, provider, trading or research authority lives here. Coverage is
always derived from the existing evaluator at the frozen cut. Queue progress
is diagnostic only and cannot promote a symbol into decision authority.
"""
from collections import Counter
import json

from trader.attention_admission import rotate, digest

STATE_KEY = 'attention_history_rotation_v1'
SCHEMA = 'attention-history.v1'


def coverage(result, symbols, threshold):
    windows = {o['symbol']: o for o in result['observations'] if o['kind'] == 'candle_window'}
    scores = {r['symbol']: r for r in result['rows']}
    rows = {}
    for sym in sorted(symbols):
        score, window = scores.get(sym, {}), windows.get(sym, {})
        status = score.get('status', 'missing')
        eligible = score.get('eligible') is True
        salient = eligible and score.get('salience') is not None and score['salience'] >= threshold
        history = window.get('detail', {}).get('history', {})
        rows[sym] = dict(state='SALIENT' if salient else ('WARMING' if not eligible and history.get('eligible_bars', 0) else 'OBSERVED'),
                         history_status=window.get('status', status), ranking_status=status,
                         ranking_eligible=eligible, salient=salient, decision_ready=False,
                         decision_reason='requires_current_strategy_input_evaluation', history=history)
    return dict(schema=SCHEMA, counts=dict(observed=len(rows),
                **{k:sum(bool(r[k]) for r in rows.values()) for k in ('ranking_eligible','salient','decision_ready')},
                warming=sum(r['state']=='WARMING' for r in rows.values())),
                history_status_counts=dict(Counter(r['history_status'] for r in rows.values())), rows=rows)


def prepare(kernel, receipt):
    """Post-admission work only. Demand is reconstructed, never a second queue.

    Half of each pass is reserved for rotating broad exploration. Unused
    priority capacity also goes to exploration. Cursor persistence precedes
    submissions: crash recovery may retry but cannot strand a cohort tail.
    """
    feed = kernel.feed
    if not callable(getattr(feed, 'prepare_history', None)):
        return dict(schema=SCHEMA, status='refused_queue_unavailable', submitted=0)
    obs = receipt['observation']
    tf = obs['attention_event']['input']['timeframe']
    cov = obs.get('history_coverage', {}).get('rows', {})
    # Even ranking-qualified windows require periodic receipt refresh. Full
    # depth and freshness are rechecked by the single queue before acquisition.
    members = set(obs['rows'])
    priority = (set(obs['exposure_required']) | set(obs['context_anchors']) |
                set(obs.get('required_symbols', [])) | set(receipt['admitted_symbols']))
    raw = kernel.journal.kv_get(STATE_KEY)
    state = json.loads(raw) if raw else dict(schema=SCHEMA, priority=None, exploration=None, generation=0)
    if (state.get('schema') != SCHEMA or type(state.get('generation')) is not int or state.get('generation', -1) < 0 or
            any(state.get(k) is not None and not isinstance(state[k], str) for k in ('priority','exploration'))):
        raise ValueError('history_rotation_corrupt')
    batch = feed.HISTORY_BATCH
    selected = rotate(priority, state['priority'])[:batch//2]
    next_state = dict(state, generation=state['generation']+1)
    if selected: next_state['priority'] = selected[-1]
    explored = [s for s in rotate(members, state['exploration']) if s not in selected][:batch-len(selected)]
    if explored: next_state['exploration'] = explored[-1]
    selected += explored
    kernel.journal.kv_set(STATE_KEY, json.dumps(next_state, sort_keys=True))
    outcomes = {s:feed.prepare_history(s, tf) for s in selected}
    return dict(schema=SCHEMA, status='SCHEDULED', receipt_id=receipt['receipt_id'],
                source_cut_ms=obs['source_cut_ms'], submitted=len(selected),
                max_submissions=batch, cursor=next_state, outcomes=outcomes,
                counts=dict(Counter(outcomes.values())),
                deferred_cohort=len(members-set(selected)),
                states={s:('WARMING' if outcome == 'queued_or_coalesced' and not cov.get(s,{}).get('ranking_eligible')
                           else cov.get(s,{}).get('state','OBSERVED')) for s,outcome in outcomes.items()},
                queue=feed.background_acquisition_health())


def decision_state(snapshot, decision):
    """Only a current strategy output can establish evaluated decision inputs.

    No output remains unassessed: legacy evaluators do not distinguish false
    predicates from unavailable inputs. This state grants no Risk permission.
    """
    lineage = getattr(snapshot, 'market_provenance_parts', None)
    ready = bool(lineage and lineage.get('frames') and getattr(decision, 'strategy_signals', None))
    return dict(schema=SCHEMA, state='DECISION_READY' if ready else 'WARMING',
                reason='current_strategy_output_with_retained_inputs' if ready else 'no_validated_strategy_output',
                decision_id=decision.id, source_cut_ms=lineage.get('as_of_ms') if lineage else None,
                authority='data_evaluation_only; Risk_and_Execution_unchanged')
