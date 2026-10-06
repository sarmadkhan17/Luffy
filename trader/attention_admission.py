"""Deterministic discretionary admission; no venue, strategy, or Risk authority.

Replay consumes a frozen observation and prior rotation state. The journal
transaction commits the receipt and next state before any symbol acquisition.
"""
from dataclasses import dataclass, asdict
from bisect import bisect_right
import hashlib
import json
import math

SCHEMA = 'crypto-admission.v1'
POLICY_VERSION = 'hierarchical-crypto.v1'
PEER_VERSION = 'strategy-volume-peer.v2'
STATE_KEY = 'attention_admission_rotation_v1'
RECEIPT_KEY = 'attention_admission_latest_v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class AdmissionPolicy:
    max_deep_slots: int
    event_reserve: int
    exploration_reserve: int
    minimum_slots: int

    def __post_init__(self):
        if any(type(x) is not int or x < 0 for x in asdict(self).values()):
            raise ValueError('admission_policy_invalid')
        if self.minimum_slots != 0:
            raise ValueError('forced_admission_forbidden')

    @classmethod
    def from_config(cls, cfg):
        return cls(**cfg['attention']['admission'])


@dataclass(frozen=True)
class SymbolRoles:
    broad_crypto: tuple[str, ...]
    peers: tuple[str, ...]
    deep: tuple[str, ...]
    anchors: tuple[str, ...]
    exposure: tuple[str, ...]
    event: tuple[str, ...]
    exploration: tuple[str, ...]

    def __post_init__(self):
        if any(not isinstance(v,tuple) or len(v)!=len(set(v)) or any(not isinstance(s,str) for s in v)
               for v in asdict(self).values()):
            raise ValueError('symbol_roles_invalid')
        broad=set(self.broad_crypto)
        if (not set(self.deep).issubset(broad) or not set(self.peers).issubset(broad)
                or not set(self.event).issubset(self.deep) or not set(self.exploration).issubset(self.deep)
                or set(self.event)&set(self.exploration)):
            raise ValueError('symbol_role_boundaries_invalid')


def initial_state():
    body = dict(schema=SCHEMA, exploration_cursor=None, relevance_cursor=None,
                event_signatures={}, generation=0)
    return dict(body, sha256=digest(body))


def verify_state(state):
    if not isinstance(state, dict):
        raise ValueError('exploration_state_corrupt')
    body = {k:v for k,v in state.items() if k != 'sha256'}
    if (set(body) != {'schema','exploration_cursor','relevance_cursor','event_signatures','generation'}
            or state.get('sha256') != digest(body) or body['schema'] != SCHEMA
            or type(body['generation']) is not int or body['generation'] < 0
            or any(body[k] is not None and not isinstance(body[k], str)
                   for k in ('exploration_cursor','relevance_cursor'))
            or not isinstance(body['event_signatures'], dict)
            or any(not isinstance(k,str) or not isinstance(v,str) for k,v in body['event_signatures'].items())):
        raise ValueError('exploration_state_corrupt')
    return body


def rotate(symbols, cursor):
    items = sorted(set(symbols))
    if not items:
        return []
    start = bisect_right(items, cursor) % len(items) if cursor is not None else 0
    return items[start:] + items[:start]


def admit(observation, policy, state):
    """Pure admission over already eligible crypto and existing salience rows.

    No fabricated score for missing history. A valid ticker permits exploration
    and rotating strategy-relevant fallback, explicitly labelled degraded.
    Learned priority has no influence on this version's compute admission.
    """
    old = verify_state(state)
    if observation.get('schema') != 'broad-crypto.v1' or observation.get('source_cut_ms') is None:
        raise ValueError('broad_observation_invalid')
    cut = observation['source_cut_ms']
    if type(cut) is not int or cut < 0:
        raise ValueError('source_cut_invalid')
    rows = observation['rows']
    if rows and 'source_receipts' in observation:
        # Real observations carry raw source receipts. Replay must qualify
        # their inner clocks/bytes, not trust the outer cohort timestamp.
        from .data import market_provenance as mp
        import pandas as pd
        sources = observation['source_receipts']
        r = sources.get('venue_metadata_receipt')
        try:
            markets = sources['venue_metadata']
            if (not r or r['instrument_id'] != 'ref:venue_metadata' or r['kind'] != 'reference'
                    or r['transform_version'] != 'venue_metadata.v1'
                    or json.loads(r['raw_json'])['metadata'] != markets):
                raise ValueError('admission_metadata_receipt_unavailable')
            frame = pd.DataFrame([{**r, 'close':float(len(markets)),
                                   'ts':pd.to_datetime(r['event_time_ms'],unit='ms',utc=True)}])
            mp.receipt_metadata(frame.iloc[0])
            retained = mp.prepare(frame)
            if (not retained or retained[0]['revision_id'] != r['revision_id']
                    or retained[0]['content_hash'] != r['content_hash']):
                raise ValueError('admission_metadata_receipt_corrupt')
            eligible = mp.eligible_frame(frame,None,cut)
            if eligible is None or eligible.empty or not eligible['quality'].eq('VALID').all():
                raise ValueError('admission_metadata_receipt_unavailable')
        except (ValueError,TypeError,KeyError) as exc:
            raise ValueError('admission_metadata_receipt_unavailable') from exc
    members = tuple(sorted(rows))
    if any(not isinstance(r,dict) or r.get('symbol') != sym or r.get('asset_class') != 'CRYPTO'
           for sym,r in rows.items()):
        raise ValueError('crypto_cohort_invalid')
    peer = tuple(sorted(set(observation['peer_cohort'])))
    if not set(peer).issubset(members):
        raise ValueError('peer_cohort_invalid')
    scores = observation['salience_rows']
    threshold = observation['salience_config']['min_salience']
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError('salience_threshold_invalid')
    valid, ranked = [], []
    detail = {}
    for sym in members:
        row = rows[sym]
        available = row.get('available_at_ms')
        usable = (type(available) is int and 0 <= available <= cut and row.get('quality') == 'VALID')
        score = scores.get(sym, {})
        salience = score.get('salience')
        if salience is not None and (isinstance(salience,bool) or not math.isfinite(salience) or salience < 0):
            raise ValueError('salience_invalid')
        eligible_score = score.get('eligible') is True and salience is not None
        if usable: valid.append(sym)
        if usable and eligible_score and salience >= threshold: ranked.append(sym)
        detail[sym] = dict(symbol=sym, features=row, score=salience,
            components=score.get('components',{}), score_status=score.get('status','missing'),
            missing_reasons=row.get('missing_reasons',[]) + ([] if eligible_score else [score.get('reason','no_salience_history')]),
            strategy_relevance=observation['strategy_relevance'].get(sym,[]),
            required_by_strategy=observation.get('required_by_strategy',{}).get(sym,[]),
            context_anchor=sym in observation['context_anchors'], exposure_required=sym in observation['exposure_required'],
            category='deferred', reason='no_qualifying_salience_or_relevance' if usable else 'broad_input_unusable',
            event_reason=None, exploration_reason=None)
    ranked.sort(key=lambda s:(-float(format(detail[s]['score'],'.12g')),s))
    event_signatures = {s:digest([observation['salience_anchor_ms'],detail[s]['components']]) for s in ranked}
    events = [s for s in ranked if old['event_signatures'].get(s) != event_signatures[s]]
    chosen, categories = [], {}
    def add(sym, category, reason):
        chosen.append(sym); categories[sym] = category
        detail[sym].update(category=category,reason=reason)
        if category == 'event': detail[sym]['event_reason'] = 'new_salience_observation_at_or_above_existing_threshold'
        if category == 'exploration': detail[sym]['exploration_reason'] = 'persistent_round_robin_valid_crypto'
    event_capacity = min(policy.event_reserve,policy.max_deep_slots)
    exploration_capacity = min(policy.exploration_reserve,policy.max_deep_slots-event_capacity)
    for sym in events[:event_capacity]: add(sym,'event','new_anomaly')
    # Reserve exploration before ranked filling; event/ranked collisions are
    # skipped and the cursor advances over the actual considered population.
    explored = []
    cursor = old['exploration_cursor']
    for sym in rotate(valid,cursor):
        if len(explored) >= exploration_capacity: break
        cursor = sym
        if sym in chosen: continue
        add(sym,'exploration','rotating_exploration'); explored.append(sym)
    ranked_capacity = max(0,policy.max_deep_slots-event_capacity-exploration_capacity)
    ranked_selected = []
    relevant_cursor = old['relevance_cursor']
    required = [s for s in valid if observation.get('required_by_strategy',{}).get(s)]
    for sym in rotate(required,old['relevance_cursor']):
        if len(ranked_selected) >= ranked_capacity: break
        if sym not in chosen:
            relevant_cursor = sym
            add(sym,'strategy_required','explicit_strategy_evaluation_requirement'); ranked_selected.append(sym)
    for sym in ranked:
        if len(ranked_selected) >= ranked_capacity: break
        if sym not in chosen:
            add(sym,'ranked','salience_at_or_above_existing_threshold'); ranked_selected.append(sym)
    # Includes are relevance, not an unconditional deep obligation. Missing
    # scores with valid tickers use an independent persistent rotation.
    relevant = [s for s in valid if observation['strategy_relevance'].get(s) and s not in chosen]
    for sym in rotate(relevant,relevant_cursor):
        if len(ranked_selected) >= ranked_capacity: break
        relevant_cursor = sym
        category = 'relevant' if detail[sym]['score'] is not None else 'warmup_relevant'
        add(sym,category,'rotating_strategy_relevance'); ranked_selected.append(sym)
    for sym in members:
        if sym not in chosen and sym in valid and (sym in ranked or sym in relevant):
            detail[sym]['reason'] = 'capacity_or_reserved_slot_deferred'
    next_body = dict(schema=SCHEMA, exploration_cursor=cursor,relevance_cursor=relevant_cursor,
                     event_signatures=event_signatures,generation=old['generation']+1)
    next_state = dict(next_body,sha256=digest(next_body))
    roles = SymbolRoles(members,peer,tuple(chosen),tuple(observation['context_anchors']),
                        tuple(observation['exposure_required']),tuple(s for s in chosen if categories[s]=='event'),tuple(explored))
    receipt = dict(schema=SCHEMA,policy_version=POLICY_VERSION,policy=asdict(policy),
        source_cut_ms=cut,source_identity=observation['source_identity'],
        observation=observation,prior_state=state,next_state=next_state,roles=asdict(roles),
        peer_cohort_version=PEER_VERSION,peer_cohort_id=digest([PEER_VERSION,peer]),
        rows=[detail[s] for s in members],tie_break_order=ranked,
        admitted_symbols=chosen,rejected_deferred=[s for s in members if s not in chosen],
        capacity_used=len(chosen),unused_capacity=policy.max_deep_slots-len(chosen),
        reserved_unused=dict(event=event_capacity-len(roles.event),exploration=exploration_capacity-len(explored)),
        degraded_state=observation['status'],fallback='rotating_valid_ticker_relevance_without_fabricated_salience',
        required_deferred=[s for s in observation.get('required_symbols',[]) if s not in chosen],
        learned_priority_influence='NONE: existing governed priorities retained for investigation only')
    receipt['receipt_id'] = digest(receipt)
    return receipt


def verify_receipt(receipt):
    if not isinstance(receipt,dict) or receipt.get('receipt_id') != digest({k:v for k,v in receipt.items() if k!='receipt_id'}):
        raise ValueError('admission_receipt_corrupt')
    replay = admit(receipt['observation'],AdmissionPolicy(**receipt['policy']),receipt['prior_state'])
    if canonical(replay) != canonical(receipt):
        raise ValueError('admission_replay_mismatch')
    return receipt


def persist(journal, observation, policy):
    with journal._tx() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT value FROM state_kv WHERE key=?',(STATE_KEY,)).fetchone()
        from trader.core import journal_evidence as E
        previous=db.execute('SELECT value FROM state_kv WHERE key=?',(RECEIPT_KEY,)).fetchone()
        if row is None or previous is None:
            if row is not None or previous is not None or db.execute(
                    "SELECT 1 FROM brain_events WHERE kind='attention_admission' LIMIT 1").fetchone():
                raise ValueError('admission_rotation_or_receipt_missing')
            state=initial_state()
        else:
            state=json.loads(row[0])
            prior=verify_receipt(json.loads(E.resolve(db,previous[0])))
            if canonical(state)!=canonical(prior['next_state']):
                raise ValueError('admission_rotation_receipt_mismatch')
        receipt = admit(observation,policy,state)
        verify_receipt(receipt)
        from trader.core import journal_evidence as E
        # One cheap bounded receipt for the whole observed cohort, not deep
        # per-symbol Journal/learning capture. Raw tickers remain exact here.
        detail = E.store(db,receipt)
        db.execute('INSERT INTO brain_events(ts,kind,subject,detail) VALUES(?,?,?,?)',
            (__import__('datetime').datetime.fromtimestamp(observation['source_cut_ms']/1000,__import__('datetime').timezone.utc).isoformat(),
             'attention_admission',receipt['receipt_id'],detail))
        for key,value in ((STATE_KEY,receipt['next_state']),(RECEIPT_KEY,receipt)):
            # Latest receipt uses the same lossless blob manifest as history.
            text = detail if key == RECEIPT_KEY else canonical(value)
            db.execute('INSERT OR REPLACE INTO state_kv(key,value) VALUES(?,?)',(key,text))
    return receipt


def latest(journal):
    from trader.core import journal_evidence as E
    with journal._tx() as db:
        row = db.execute('SELECT value FROM state_kv WHERE key=?',(RECEIPT_KEY,)).fetchone()
        return verify_receipt(json.loads(E.resolve(db,row[0]))) if row else None


def owner_view(journal, *, now_ms, stale_seconds):
    """Bounded semantic summary, distinct from legacy investigation telemetry."""
    try:
        attempts=journal.query("SELECT kind FROM brain_events WHERE kind IN ('attention_admission','attention_admission_refused') ORDER BY id DESC LIMIT 1")
        if attempts and attempts[0]['kind']=='attention_admission_refused':
            return dict(status='refused',reason='latest_discretionary_admission_refused',deep_admitted=0)
        r = latest(journal)
        if r is None: return dict(status='unavailable',reason='no_admission_receipt')
        age = now_ms-r['source_cut_ms']
        status = 'current' if 0 <= age <= stale_seconds*1000 else 'stale'
        return dict(status=status,receipt_id=r['receipt_id'],source_cut_ms=r['source_cut_ms'],
            policy=r['policy'],policy_version=r['policy_version'],broad_observed=len(r['roles']['broad_crypto']),
            peer_cohort=len(r['roles']['peers']),deep_admitted=r['capacity_used'],
            event_admitted=len(r['roles']['event']),exploration_admitted=len(r['roles']['exploration']),
            exposure_required=len(r['roles']['exposure']),context_anchors=r['roles']['anchors'],
            unused_capacity=r['unused_capacity'],degraded_state=r['degraded_state'],
            admitted_symbols=r['admitted_symbols'],rejected_deferred_count=len(r['rejected_deferred']),
            categories=[dict(symbol=x['symbol'],category=x['category'],reason=x['reason'],
                score_status=x['score_status']) for x in r['rows']])
    except (ValueError,KeyError,TypeError, __import__('sqlite3').Error):
        return dict(status='refused',reason='admission_provenance_unavailable_or_corrupt')
