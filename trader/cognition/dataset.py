"""Point-in-time dataset assembly from retained typed-outcome receipts.

Deterministic and self-contained: no database, network or wall clock. The only
file it reads is its own source, to stamp the builder version. The caller
freezes a declaration (collection mode, discovery cut, start, universe,
dependence units, coverage expectations, resource limits) and obtains a **freeze
receipt carrying an observed wall clock** BEFORE any capture or search. `build`
is then a function of (declaration, freeze receipt, capture, receipts,
source_meta) and embeds every receipt it considered, so `replay` rebuilds the
dataset without the source ledgers, exactly as `outcomes.replay` rebuilds a
single record.

Boundaries this module keeps:

* The declaration carries no self-asserted freeze time. Only `observed_frozen_ms`
  in the freeze receipt — written by the CLI from the actual clock before
  capture — dates the freeze, and the capture clock must not precede it.
* Knowledge is dated by `known_ms`: the maximum of a receipt's resolved,
  available, original import and LOCAL import clocks. Nothing known at or after
  the discovery cut enters, and nothing known after the observed capture enters.
* `collection_mode='forward'` admits only receipts that were both registered at
  or after the observed freeze and imported locally at or after it, so an old
  outcome restored after the freeze cannot pose as forward collection.
* `collection_mode='retrospective_snapshot'` is an explicitly labelled engineering
  snapshot: every row is marked retrospective unconditionally. Neither mode ever
  reports sampling as complete, and a capture that precedes the discovery cut is
  `partial_not_search_ready` — a captured window is never a finished discovery.
* Features are registration-time state, clocked at the decision's own
  registration rather than at the earlier availability of the bars it read.
  Labels live in a separate structure with their own availability clock. A
  feature may never carry a label key, a source version unavailable at the
  feature clock, or a classification (`missed_opportunity`, `false_signal`)
  derivable only from the outcome.
* A sequence row links a prior case only when that prior was fully KNOWN —
  including its local import — before the current feature clock. A delayed
  import is hindsight, not history.
* Journal rows are first observed at import: their decision fields have no
  registration-time availability receipt, so they are `observation_only`, never
  claimed as point-in-time features, and never used to build sequence rows.
* Journal strategy attribution is an id, never a retained strategy definition.
* Unverified execution P&L stays `unknown`, is counted, and is never imputed as
  zero, taken from a journal default, or replaced by a simulation.
* Simultaneous episodes across symbols are treated as one dependence unit by
  default; declared symbol groups only widen that, never narrow it.

Nothing here searches, ranks, scores, admits or trades.
"""
import json

from trader.cognition import forecast_protocol as L
from trader.cognition import outcomes as O

SCHEMA = 'pit-dataset.v1'
DECLARATION_SCHEMA = 'pit-dataset-declaration.v1'
FREEZE_SCHEMA = 'pit-dataset-freeze.v1'
CAPTURE_SCHEMA = 'pit-dataset-capture.v1'

# Hard ceilings. A declaration may lower these, never raise them.
LIMITS = dict(max_records=512, max_rows=1024, max_universe=256, max_payload_bytes=8*1024*1024)

DECLARED_FIELDS = {'schema_version', 'dataset_id', 'collection_mode', 'start_ms',
                   'discovery_cut_ms', 'universe', 'dependence', 'coverage', 'limits'}
DEPENDENCE_FIELDS = {'symbol_groups', 'window_pad_ms'}
COVERAGE_FIELDS = {'expected_kinds', 'expected_symbols', 'min_rows', 'max_sequence_gap_ms'}
LIMIT_FIELDS = {'max_records', 'max_rows'}
FREEZE_FIELDS = {'schema_version', 'declaration_version', 'declaration', 'observed_frozen_ms',
                 'clock_source', 'code_manifest'}
CAPTURE_FIELDS = {'schema_version', 'observed_capture_ms', 'clock_source', 'code_manifest'}
MAX_MANIFEST_ENTRIES, MAX_MANIFEST_VALUE = 16, 160

PROVENANCE = ('original_after_freeze', 'restored_after_freeze_registered_before',
              'retained_before_freeze')

COLLECTION_MODES = ('forward', 'retrospective_snapshot')

ROW_TYPES = {'selected_forecast': 'state', 'ignored_forecast': 'state',
             'regime_transition': 'transition', 'false_signal': 'failure',
             'skip': 'state', 'missed_opportunity': 'state', 'executed_trade': 'state'}

# Producers whose feature state carries no registration-time availability receipt.
OBSERVATION_ONLY = {'journal': 'journal_features_first_observed_at_import',
                    'journal_unlinked': 'journal_features_first_observed_at_import'}

# Never permitted as a feature key, at any nesting depth.
LABEL_KEYS = frozenset({'label', 'labels', 'outcome', 'winner', 'supports', 'score', 'case_kind',
                        'price_change_bps', 'net_pnl', 'realized_pnl', 'actual_status',
                        'actual_execution', 'simulation', 'decision_kind'})

SAMPLING = ('Rows come from a bounded, evicting store of receipts that happened to be retained. '
            'Retained retrospective rows never establish complete prospective sampling: absence '
            'of a row is not evidence that the underlying event did not occur.')

CASE_SELECTION_BIAS = (
    'Investigation-case rows exist only where the producer emitted a labelled case: a '
    'contradicted same-direction alternative (false_signal) or a registered volatility '
    'transition (regime_transition). Investigations with any other outcome produce no row at '
    'all, so this is a sample conditioned on its own classification. It cannot establish a '
    'rate, a base rate, or how often either label occurs.')

LIMITATIONS = [
    'Offline assembly of retained receipts only; no search, ranking, admission or order path.',
    CASE_SELECTION_BIAS,
    'Observed price or path measurements are not execution P&L and not an edge claim.',
    'Unverified execution accounting stays unknown; it is counted, never imputed.',
    'Journal rows are observation-only: their features were first seen at import.',
    'Journal strategy ids are attribution only; historical strategy definitions are unknown.',
    'Dependence groups are a deterministic conservative over-merge, not a measured correlation.',
    'Coverage is measured against the declaration, not against the true population of events.',
]


def _check_manifest(manifest):
    if (type(manifest) is not dict or not manifest or len(manifest) > MAX_MANIFEST_ENTRIES or
            any(type(k) is not str or not k or type(v) is not str or not v or
                len(v) > MAX_MANIFEST_VALUE for k, v in manifest.items())):
        raise ValueError('code_manifest_invalid')
    return manifest


def _manifest(supplied):
    """Code versions are stamped by the caller that can read the source, not here.

    Hashing local files inside the builder would rehash whatever code happens to
    be installed at replay time; the manifest recorded at freeze and at capture
    travels inside the dataset and is preserved verbatim through replay.
    """
    if supplied is None:
        raise ValueError('code_manifest_required')
    return _check_manifest(json.loads(L.encode(supplied)))


def digest(value):
    return O.digest(value)


def _int(value):
    return type(value) is int and value >= 0


def _strings(value, limit):
    return (type(value) is list and len(value) <= limit and
            all(type(v) is str and v for v in value) and len(set(value)) == len(value))


def validate_declaration(declaration):
    """Return the normalized frozen declaration, or raise a structured reason code."""
    d = declaration
    if type(d) is not dict or set(d) != DECLARED_FIELDS:
        raise ValueError('declaration_unknown_or_missing_field')
    if d['schema_version'] != DECLARATION_SCHEMA or type(d['dataset_id']) is not str or not d['dataset_id']:
        raise ValueError('declaration_schema_or_id')
    if d['collection_mode'] not in COLLECTION_MODES:
        raise ValueError('declaration_invalid_collection_mode')
    if not all(_int(d[k]) for k in ('start_ms', 'discovery_cut_ms')):
        raise ValueError('declaration_invalid_clock')
    if not d['start_ms'] < d['discovery_cut_ms']:
        raise ValueError('declaration_start_not_before_cut')
    if not _strings(d['universe'], LIMITS['max_universe']) or not d['universe']:
        raise ValueError('declaration_invalid_universe')
    dep = d['dependence']
    if type(dep) is not dict or set(dep) != DEPENDENCE_FIELDS or not _int(dep['window_pad_ms']):
        raise ValueError('declaration_invalid_dependence')
    groups = dep['symbol_groups']
    if (type(groups) is not dict or
            any(type(name) is not str or not name or not _strings(members, LIMITS['max_universe']) or
                set(members) - set(d['universe']) for name, members in groups.items())):
        raise ValueError('declaration_invalid_symbol_groups')
    cov = d['coverage']
    if (type(cov) is not dict or set(cov) != COVERAGE_FIELDS or
            not _int(cov['min_rows']) or not _int(cov['max_sequence_gap_ms'])):
        raise ValueError('declaration_invalid_coverage')
    if (type(cov['expected_kinds']) is not dict or
            any(k not in ROW_TYPES or not _int(v) for k, v in cov['expected_kinds'].items())):
        raise ValueError('declaration_invalid_expected_kinds')
    if (not _strings(cov['expected_symbols'], LIMITS['max_universe']) or
            set(cov['expected_symbols']) - set(d['universe'])):
        raise ValueError('declaration_expected_symbols_outside_universe')
    lim = d['limits']
    if (type(lim) is not dict or set(lim) != LIMIT_FIELDS or
            not all(_int(lim[k]) and 0 < lim[k] <= LIMITS[k] for k in LIMIT_FIELDS)):
        raise ValueError('declaration_invalid_limits')
    # JSON detaches caller-owned mutable inputs; key order never changes the version.
    return json.loads(L.encode(d))


def declaration_version(declaration):
    return digest(validate_declaration(declaration))


def freeze(declaration, observed_frozen_ms, clock_source='cli_wall_clock_utc', *, code_manifest):
    """Build the freeze receipt. The caller must read an actual clock, before capture."""
    decl = validate_declaration(declaration)
    if not _int(observed_frozen_ms):
        raise ValueError('freeze_invalid_observed_clock')
    if type(clock_source) is not str or not clock_source:
        raise ValueError('freeze_invalid_clock_source')
    return json.loads(L.encode({'schema_version': FREEZE_SCHEMA, 'declaration_version': digest(decl),
                                'declaration': decl, 'observed_frozen_ms': observed_frozen_ms,
                                'clock_source': clock_source,
                                'code_manifest': _manifest(code_manifest)}))


def validate_freeze(receipt, declaration):
    """The receipt, not any caller-declared timestamp, is the evidence of freezing."""
    decl = validate_declaration(declaration)
    if type(receipt) is not dict or set(receipt) != FREEZE_FIELDS or receipt['schema_version'] != FREEZE_SCHEMA:
        raise ValueError('freeze_receipt_unknown_or_missing_field')
    if not _int(receipt['observed_frozen_ms']) or not receipt['clock_source']:
        raise ValueError('freeze_invalid_observed_clock')
    _check_manifest(receipt['code_manifest'])
    if receipt['declaration'] != decl or receipt['declaration_version'] != digest(decl):
        raise ValueError('freeze_receipt_declaration_mismatch')
    if (decl['collection_mode'] == 'forward' and
            receipt['observed_frozen_ms'] >= decl['discovery_cut_ms']):
        # The cut had already passed when the freeze happened: nothing forward remains.
        raise ValueError('declaration_frozen_after_discovery_cut_use_retrospective_snapshot')
    return json.loads(L.encode(receipt))


def capture_receipt(observed_capture_ms, clock_source='cli_wall_clock_utc', *, code_manifest):
    if not _int(observed_capture_ms) or type(clock_source) is not str or not clock_source:
        raise ValueError('capture_invalid_observed_clock')
    return {'schema_version': CAPTURE_SCHEMA, 'observed_capture_ms': observed_capture_ms,
            'clock_source': clock_source, 'code_manifest': _manifest(code_manifest)}


def _validate_capture(capture, frozen_ms):
    if type(capture) is not dict or set(capture) != CAPTURE_FIELDS or capture['schema_version'] != CAPTURE_SCHEMA:
        raise ValueError('capture_unknown_or_missing_field')
    if not _int(capture['observed_capture_ms']) or not capture['clock_source']:
        raise ValueError('capture_invalid_observed_clock')
    _check_manifest(capture['code_manifest'])
    if capture['observed_capture_ms'] < frozen_ms:
        raise ValueError('capture_precedes_declaration_freeze')
    return json.loads(L.encode(capture))


def _leaf_keys(value, out):
    if type(value) is dict:
        for k, v in value.items():
            out.add(k)
            _leaf_keys(v, out)
    elif type(value) is list:
        for v in value:
            _leaf_keys(v, out)
    return out


def _check_features(features, label, point_in_time):
    """Features may not name a label, nor read a source that was not yet available.

    A point-in-time row additionally requires its feature clock to strictly
    precede label availability. An observation-only row makes no such claim and
    instead records the single clock at which everything was first observed.
    """
    if _leaf_keys(features, set()) & LABEL_KEYS:
        raise ValueError('dataset_label_key_in_features')
    as_of = features['as_of_ms']
    if not _int(as_of):
        raise ValueError('dataset_invalid_feature_clock')
    if point_in_time:
        if not as_of < label['available_ms']:
            raise ValueError('dataset_feature_state_not_before_outcome')
    elif as_of != label['available_ms']:
        raise ValueError('dataset_observation_only_clock_mismatch')
    if features.get('source_available_ms') is not None and features['source_available_ms'] > as_of:
        raise ValueError('dataset_feature_uses_unavailable_source')


# --- producers -------------------------------------------------------------

def _producer(record):
    s = record['source']
    if record['kind'] in ('selected_forecast', 'ignored_forecast'):
        return 'forecast'
    if 'investigation_case' in s:
        return 'investigation_case'
    if 'journal_reference' in s:
        return 'journal'
    if 'decision' in s:
        return 'journal_unlinked'
    if 'accounting' in s:
        return 'verified_execution'
    if 'registration' in s and 'target' in s:
        return 'counterfactual'
    return None


def _actual(record):
    a = record['actual_execution']
    if not a:
        return {'actual_status': 'unavailable', 'actual_net_pnl': None,
                'actual_reason': 'observation_only_producer_has_no_execution'}
    return {'actual_status': a['status'], 'actual_net_pnl': a.get('net_pnl'),
            'actual_reason': a.get('reason') or 'verified_venue_accounting',
            'actual_currency': a.get('currency'), 'actual_environment': a.get('environment')}


def _forecast_row(record):
    s = record['source']
    p, o = s['prediction'], s['outcome']
    closes = [b['close'] for b in p['input_window']]
    # The selection and direction are decided at registration, not when the bars
    # became available; the earlier observation clock is recorded, never claimed.
    features = dict(as_of_ms=p['registered_ms'], symbol=s['symbol'], protocol_id=p['protocol_id'],
                    feature_availability='decided_at_registration',
                    state_observed_ms=p['observed_ms'],
                    selected=bool(p['selected']), direction=p['direction'],
                    recent_move_bps=p['recent_move_bps'], baseline_close=p['baseline_close'],
                    bar_returns_bps=[(b/a-1)*10000 for a, b in zip(closes, closes[1:])],
                    window_bars=len(p['input_window']), horizon_open_ms=p['target_open_ms'],
                    source_versions=[b['version_id'] for b in p['input_window']],
                    source_available_ms=max(b['available_ms'] for b in p['input_window']))
    label = dict(available_ms=record['available_ms'], resolved_ms=record['resolved_ms'],
                 case_kind=record['kind'], supports=o['supports'],
                 price_change_bps=o['price_change_bps'], target_version=o['target']['version_id'],
                 measurement='observed_price_change_not_pnl', simulated=False, **_actual(record))
    return s['symbol'], s['id'], (), features, label


def _case_row(record):
    inv = O.unpack_case(record['source']['investigation_case'])['investigation']
    st, m, ob = inv['state'], inv['measurement'], record['observation']
    features = dict(as_of_ms=inv['registered_ms'], symbol=st['symbol'], family=inv['primary_trigger'],
                    feature_availability='decided_at_registration',
                    state_observed_ms=st['observed_ms'], state_available_ms=st['available_ms'],
                    sign=m['sign'], threshold=m['threshold'], baseline_mean=m['baseline_mean'],
                    baseline_scale=m['baseline_scale'], config_id=st['config_id'],
                    catalog_id=m['catalog_id'], deadline_ms=m['deadline_ms'],
                    dimensions=[{'name': x['name'], 'value': x['value'], 'status': x['status']}
                                for x in st['dimensions']],
                    state_transitions=list(st['transitions']),
                    contradictions=list(st['contradictions']), missing=list(st['missing']),
                    cohort=list(st['cohort']), membership_version=digest(st['membership_json']),
                    source_versions=list(st['input_versions']), source_available_ms=st['available_ms'])
    # `false_signal` is derivable only from the resolved assessment: it is a label.
    label = dict(available_ms=record['available_ms'], resolved_ms=record['resolved_ms'],
                 case_kind=record['kind'], winner=ob['winner'], score=ob['score'],
                 measurement='observed_path_not_pnl', simulated=False, meaning=ob['meaning'],
                 **_actual(record))
    return st['symbol'], inv['investigation_id'], tuple(st['cohort']), features, label


def _journal_row(record, linked):
    """First observed at import. The decision timestamp is a claim, not availability."""
    s = record['source']
    ref = s['journal_reference'] if linked else s
    d, t = ref['decision'], ref['trade']
    attribution = [dict(strategy_id=r['strategy_id'], definition_version=None,
                        definition_status='unknown_no_retained_strategy_definition',
                        scope=r['scope'])
                   for r in s.get('strategy_attributions', [])]
    features = dict(as_of_ms=record['imported_ms'], symbol=d['symbol'], action=d['action'],
                    feature_availability='first_observed_at_import',
                    registration_time_availability='unverified_no_registration_receipt',
                    claimed_registration_ms=record['registered_ms'],
                    executed=bool(d['executed']), skip_reason=d.get('skip_reason'),
                    cycle_id=d.get('cycle_id'), scan_id=d.get('scan_id'),
                    strategy_attribution=attribution, attribution_linked=linked,
                    source_versions=[digest(d)], source_available_ms=record['imported_ms'])
    label = dict(available_ms=record['available_ms'], resolved_ms=record['resolved_ms'],
                 case_kind=record['kind'], measurement='unverified_journal_accounting',
                 simulated=False, closed_at=(t or {}).get('closed_at'),
                 journal_realized_pnl='excluded_unverified_default_not_actual', **_actual(record))
    return d['symbol'], d['id'], (), features, label


def _counterfactual_row(record):
    r = record['source']['registration']
    features = dict(as_of_ms=r['registered_ms'], symbol=r['baseline']['symbol'],
                    feature_availability='registered_before_target_open',
                    direction=r['direction'], cost_bps=r['cost_bps'], notional=r['notional'],
                    baseline_close=r['baseline']['close'], horizon_open_ms=r['target_open_ms'],
                    declared=bool(r.get('declaration')),
                    source_versions=[r['baseline']['version_id']],
                    source_available_ms=r['baseline']['available_ms'])
    sim = record['simulation']
    # `missed_opportunity` is a retrospective classification known only at import.
    label = dict(available_ms=record['available_ms'], resolved_ms=record['resolved_ms'],
                 case_kind=record['kind'], price_change_bps=record['observation']['price_change_bps'],
                 simulated=True, simulated_net_pnl=sim['net_pnl'], simulated_model=sim['model'],
                 executable_fill_claim=sim['executable_fill_claim'],
                 measurement='observed_price_change_not_pnl',
                 **dict(_actual(record), actual_status='unknown', actual_net_pnl=None,
                        actual_reason='simulated_close_fill_is_not_execution'))
    return r['baseline']['symbol'], (r.get('decision') or {}).get('id') or digest(r), (), features, label


def _execution_row(record):
    r = record['source']['registration']
    features = dict(as_of_ms=r['registered_ms'], symbol=r['symbol'], venue=r['venue'],
                    feature_availability='frozen_execution_registration_receipt',
                    environment=r['environment'], trade_id=r['trade_id'],
                    source_versions=[digest(r)], source_available_ms=None)
    label = dict(available_ms=record['available_ms'], resolved_ms=record['resolved_ms'],
                 case_kind=record['kind'], measurement='venue_attributed_accounting',
                 simulated=False, **_actual(record))
    return r['symbol'], r['trade_id'], (), features, label


BUILDERS = {'forecast': _forecast_row, 'investigation_case': _case_row,
            'journal': lambda r: _journal_row(r, True),
            'journal_unlinked': lambda r: _journal_row(r, False),
            'counterfactual': _counterfactual_row, 'verified_execution': _execution_row}


# --- build -----------------------------------------------------------------

def _receipt(item):
    if (type(item) is not dict or set(item) != {'source_key', 'local_imported_ms', 'record'} or
            type(item['source_key']) is not str or not item['source_key'] or
            not _int(item['local_imported_ms']) or type(item['record']) is not dict):
        raise ValueError('receipt_envelope_invalid')
    return item


class _Groups:
    """Deterministic union-find over conservatively merged dependence units."""

    def __init__(self):
        self.parent = {}

    def add(self, key):
        self.parent.setdefault(key, key)

    def find(self, key):
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def build(declaration, freeze_receipt, capture, receipts, source_meta=None):
    """Assemble the declared point-in-time dataset. Deterministic and self-contained."""
    decl = validate_declaration(declaration)
    dv = digest(decl)
    frozen = validate_freeze(freeze_receipt, decl)
    frozen_ms = frozen['observed_frozen_ms']
    cap = _validate_capture(capture, frozen_ms)
    forward = decl['collection_mode'] == 'forward'
    max_records, max_rows = decl['limits']['max_records'], decl['limits']['max_rows']
    items = [_receipt(i) for i in receipts]
    if len(items) > max_records:
        raise ValueError('dataset_record_capacity')
    if len({i['source_key'] for i in items}) != len(items):
        raise ValueError('dataset_duplicate_source_key')
    meta = json.loads(L.encode(source_meta if source_meta is not None else {'retention': 'unmeasured'}))
    cut, start = decl['discovery_cut_ms'], decl['start_ms']
    universe = set(decl['universe'])
    member_of = {}
    for members in decl['dependence']['symbol_groups'].values():
        for sym in members:
            member_of.setdefault(sym, set()).update(members)

    ordered = sorted(items, key=lambda i: (i['record'].get('registered_ms') or 0, i['source_key']))
    rows, excluded, seen_case, seen_underlying = [], [], {}, {}
    for item in ordered:
        key, local, record = item['source_key'], item['local_imported_ms'], item['record']

        def drop(reason, detail=None):
            excluded.append({'source_key': key, 'reason': reason, 'detail': detail})
        try:
            O.replay(record)
        except (ValueError, KeyError, TypeError) as exc:
            drop('replay_failed', str(exc) if isinstance(exc, ValueError) else 'malformed_receipt')
            continue
        if local < record['imported_ms']:
            drop('local_import_precedes_source_import')
            continue
        producer = _producer(record)
        if producer is None or record['kind'] not in ROW_TYPES:
            drop('unsupported_record_kind', record['kind'])
            continue
        # The local knowledge clock governs: a restored archive is learned at restore.
        known = max(record['resolved_ms'], record['available_ms'], record['imported_ms'], local)
        if known > cap['observed_capture_ms']:
            # A receipt cannot be known after the clock that captured it.
            drop('known_after_observed_capture', known)
            continue
        if known >= cut:
            drop('not_known_before_discovery_cut', known)
            continue
        provenance = ('retained_before_freeze' if local < frozen_ms else
                      'original_after_freeze' if record['registered_ms'] >= frozen_ms else
                      'restored_after_freeze_registered_before')
        if forward and provenance == 'retained_before_freeze':
            # Already retained when the declaration was frozen: not prospectively sampled.
            drop('retained_before_declaration_freeze', local)
            continue
        if forward and provenance == 'restored_after_freeze_registered_before':
            # An old episode restored after the freeze is not forward collection.
            drop('registered_before_declaration_freeze', record['registered_ms'])
            continue
        if record['registered_ms'] < start:
            drop('before_declared_start', record['registered_ms'])
            continue
        try:
            symbol, underlying, cohort, features, label = BUILDERS[producer](record)
        except (ValueError, KeyError, TypeError) as exc:
            drop('receipt_shape_unsupported', str(exc) if isinstance(exc, ValueError) else 'malformed_source')
            continue
        if symbol not in universe:
            drop('outside_declared_universe', symbol)
            continue
        if record['case_id'] in seen_case:
            drop('duplicate_case_id', seen_case[record['case_id']])
            continue
        pit = producer not in OBSERVATION_ONLY
        try:
            _check_features(features, label, pit)
        except ValueError as exc:
            drop(str(exc))
            continue
        if len(rows) >= max_rows:
            drop('row_capacity')
            continue
        row_id = digest(['row', dv, key, record['case_id']])
        shared = seen_underlying.setdefault((producer, underlying), row_id)
        seen_case[record['case_id']] = key
        rows.append({
            'row_id': row_id, 'row_type': ROW_TYPES[record['kind']], 'producer': producer,
            'source_key': key, 'case_id': record['case_id'], 'symbol': symbol,
            'underlying_id': underlying,
            'shared_underlying_row_id': None if shared == row_id else shared,
            'record_source_version': record['source_version'],
            'point_in_time_features': pit,
            'feature_availability_reason': OBSERVATION_ONLY.get(producer, 'registration_time_receipt'),
            # A retrospective snapshot says so for every row, whatever its clocks.
            'sampling_mode': 'forward_after_declared_freeze' if forward
                             else 'retrospective_retained_snapshot',
            'provenance': provenance,
            'clocks': {'registered_ms': record['registered_ms'], 'resolved_ms': record['resolved_ms'],
                       'available_ms': record['available_ms'], 'imported_ms': record['imported_ms'],
                       'local_imported_ms': local, 'known_ms': known},
            'dependence_symbols': sorted({symbol} | set(cohort) | member_of.get(symbol, set())),
            'features': features, 'labels': label,
        })

    rows.sort(key=lambda r: (r['features']['as_of_ms'], r['row_id']))
    # The cap covers the whole table: sequence rows consume what base rows leave.
    sequences, gaps = _sequences(rows, decl, dv, max_rows - len(rows))
    excluded.extend({'source_key': None, 'reason': 'row_capacity_total', 'detail': g['row_id']}
                    for g in gaps if g['reason'] == 'total_row_capacity')
    groups = _group(rows + sequences, decl['dependence']['window_pad_ms'])
    for row in rows + sequences:
        row['dependence_group'] = groups[row['row_id']]
    all_rows = sorted(rows + sequences, key=lambda r: (r['features']['as_of_ms'], r['row_id']))

    body = {
        'schema_version': SCHEMA, 'declaration_version': dv, 'declaration': decl,
        'freeze': frozen, 'capture': cap, 'source_meta': meta,
        # Preserved verbatim from the receipts; never rehashed from local files.
        'code_manifest': {'freeze': frozen['code_manifest'], 'capture': cap['code_manifest']},
        'status': _status(decl, cap), 'rows': all_rows, 'excluded': excluded,
        'coverage': _coverage(decl, frozen, cap, rows, sequences, gaps, excluded, meta),
        'accounting': _accounting(all_rows, groups),
        'sampling': SAMPLING, 'limitations': LIMITATIONS,
        'receipts': [{'source_key': i['source_key'], 'local_imported_ms': i['local_imported_ms'],
                      'record': i['record']} for i in ordered],
    }
    if len(L.encode(body).encode()) > LIMITS['max_payload_bytes']:
        raise ValueError('dataset_payload_capacity')
    return json.loads(L.encode(dict(body, dataset_version=digest(body))))


def _sequences(rows, decl, dv, budget):
    """Within-symbol links to the latest prior case fully KNOWN before this state.

    Eligibility is `known_ms` — the maximum of the prior's resolved, available,
    original import and LOCAL import clocks — not label availability. A record
    resolved long ago but imported late was not known in time, and using it would
    be hindsight. The prior must also be a distinct underlying case, and the wait
    must fit the declared gap. Every refusal is retained as an explicit gap.
    """
    max_gap = decl['coverage']['max_sequence_gap_ms']
    by_symbol, sequences, gaps = {}, [], []
    for row in rows:
        if not row['point_in_time_features']:
            gaps.append({'symbol': row['symbol'], 'row_id': row['row_id'],
                         'reason': 'observation_only_row_excluded_from_sequences'})
            continue
        by_symbol.setdefault(row['symbol'], []).append(row)
    for symbol in sorted(by_symbol):
        chain = sorted(by_symbol[symbol], key=lambda r: (r['features']['as_of_ms'], r['row_id']))
        for index, current in enumerate(chain):
            as_of = current['features']['as_of_ms']
            earlier = [p for p in chain[:index] if p['underlying_id'] != current['underlying_id']]
            if not earlier:
                continue
            eligible = [p for p in earlier if p['clocks']['known_ms'] < as_of]
            if not eligible:
                gaps.append({'symbol': symbol, 'row_id': current['row_id'],
                             'reason': 'prior_outcome_not_known_before_features'})
                continue
            prior = max(eligible, key=lambda p: (p['clocks']['known_ms'], p['row_id']))
            known = prior['clocks']['known_ms']
            if as_of - known > max_gap:
                gaps.append({'symbol': symbol, 'prior_row_id': prior['row_id'],
                             'row_id': current['row_id'], 'gap_ms': as_of - known,
                             'reason': 'sequence_gap_exceeds_declared'})
                continue
            if len(sequences) >= budget:
                gaps.append({'symbol': symbol, 'prior_row_id': prior['row_id'],
                             'row_id': current['row_id'], 'reason': 'total_row_capacity'})
                continue
            features = dict(as_of_ms=as_of, symbol=symbol,
                            feature_availability='prior_case_known_before_current_registration',
                            current_row_id=current['row_id'], prior_row_id=prior['row_id'],
                            prior_row_type=prior['row_type'], prior_producer=prior['producer'],
                            prior_known_ms=known, elapsed_ms=as_of - known,
                            prior_record_source_version=prior['record_source_version'],
                            prior_case_id=prior['case_id'], prior_underlying_id=prior['underlying_id'],
                            prior_observed=_prior_observed(prior),
                            current_features=current['features'],
                            source_versions=sorted(set(prior['features']['source_versions']) |
                                                   set(current['features']['source_versions'])),
                            source_available_ms=current['features'].get('source_available_ms'))
            label = dict(current['labels'])
            _check_features(features, label, True)
            sequences.append({
                'row_id': digest(['seq', dv, prior['row_id'], current['row_id']]),
                'row_type': 'sequence', 'producer': 'sequence_link', 'source_key': None,
                'case_id': current['case_id'], 'symbol': symbol,
                'underlying_id': current['underlying_id'],
                'shared_underlying_row_id': current['row_id'],
                'record_source_version': current['record_source_version'],
                'point_in_time_features': True,
                'feature_availability_reason': 'registration_time_receipt',
                'sampling_mode': current['sampling_mode'], 'provenance': current['provenance'],
                'clocks': dict(current['clocks']),
                'dependence_symbols': sorted(set(prior['dependence_symbols']) |
                                             set(current['dependence_symbols'])),
                'parent_row_ids': [prior['row_id'], current['row_id']],
                'features': features, 'labels': label,
            })
    return sequences, gaps


def _prior_observed(prior):
    """Only what was measurable and available before the current features' clock.

    Renamed away from the label vocabulary: this is a past, already-available
    observation used as state, not this row's label.
    """
    label = prior['labels']
    out = {'available_ms': label['available_ms'], 'known_ms': prior['clocks']['known_ms'],
           'measurement': label['measurement'],
           'prior_case_kind': label['case_kind'], 'prior_actual_status': label['actual_status']}
    for key, name in (('supports', 'prior_supports'), ('winner', 'prior_winner'),
                      ('score', 'prior_score'), ('price_change_bps', 'prior_price_change_bps')):
        if key in label:
            out[name] = label[key]
    return out


def _group(rows, pad):
    """Conservative over-merge, never a measured correlation or an independence claim.

    Simultaneous episodes are one unit whatever their symbols: a market-wide move
    repeats across correlated instruments, so overlapping padded windows merge by
    default, with no symbol condition to opt out of. Shared underlying cases and
    sequence parents merge regardless of overlap. Cohort and declared symbol-group
    membership stay recorded on each row as `dependence_symbols`, and widen the
    window through the declared pad rather than joining unrelated eras.
    """
    groups = _Groups()
    for row in rows:
        groups.add(row['row_id'])
    by_underlying = {}
    for row in sorted(rows, key=lambda r: r['row_id']):
        first = by_underlying.setdefault((row['symbol'], row['underlying_id']), row['row_id'])
        groups.union(first, row['row_id'])
        for parent in row.get('parent_row_ids', []):
            groups.union(parent, row['row_id'])
    ordered = sorted(rows, key=lambda r: (r['features']['as_of_ms'], r['row_id']))
    spans = [(r['row_id'], r['features']['as_of_ms'], r['labels']['available_ms']) for r in ordered]
    for i, (a_id, a_lo, a_hi) in enumerate(spans):
        for b_id, b_lo, b_hi in spans[i+1:]:
            # Market-wide: a shared calendar window is dependence on its own.
            if a_lo - pad <= b_hi and b_lo - pad <= a_hi:
                groups.union(a_id, b_id)
    roots = {}
    for row in ordered:
        roots.setdefault(groups.find(row['row_id']), 'g%04d' % len(roots))
    return {row['row_id']: roots[groups.find(row['row_id'])] for row in ordered}


def _status(decl, cap):
    """`cut_reached` is a fact about the capture clock. It is not search readiness.

    A capture clock past the declared cut says only that the cut had elapsed when
    the receipts were read. It does not show the window was fully observed, that
    coverage is adequate, or that a search protocol exists — so `search_ready`
    stays false and is decided elsewhere.
    """
    reached = cap['observed_capture_ms'] >= decl['discovery_cut_ms']
    return {'readiness': 'cut_reached' if reached else 'partial_not_search_ready',
            'cut_reached': reached, 'search_ready': False,
            'search_ready_reason': 'pending_separate_search_protocol_and_coverage_review',
            'observed_capture_ms': cap['observed_capture_ms'],
            'discovery_cut_ms': decl['discovery_cut_ms'],
            'claim': ('Reaching the declared cut is a clock fact only: it does not establish '
                      'full observation of the window, adequate coverage, a completed discovery '
                      'or permission to search.'),
            'reason_codes': (['cut_reached_not_search_ready'] if reached else
                             ['capture_precedes_discovery_cut', 'partial_not_search_ready'])}


def _prospective(decl, frozen_ms, rows):
    """Never reports complete prospective sampling; says plainly when it is absent."""
    counts = _counts(r['provenance'] for r in rows)
    original = counts.get('original_after_freeze', 0)
    forward = decl['collection_mode'] == 'forward'
    status = 'forward_declared_incomplete' if forward and original else 'absent'
    reasons = ['prospective_sampling_absent'] if status == 'absent' else []
    if not forward:
        reasons.append('retrospective_engineering_snapshot')
    if forward and not original:
        reasons.append('no_rows_registered_and_imported_after_declaration_freeze')
    if counts.get('restored_after_freeze_registered_before'):
        reasons.append('restored_rows_registered_before_freeze')
    if decl['start_ms'] < frozen_ms:
        reasons.append('declared_window_starts_before_freeze')
    return {'collection_mode': decl['collection_mode'], 'status': status,
            'observed_frozen_ms': frozen_ms, 'by_provenance': counts,
            'rows_original_after_freeze': original,
            'rows_restored_after_freeze': counts.get('restored_after_freeze_registered_before', 0),
            'rows_retained_before_freeze': counts.get('retained_before_freeze', 0),
            'complete_sampling_claim': False,
            'claim': ('Rows retained before the observed freeze, or registered before it and '
                      'restored later, are a retrospective snapshot. No mode here demonstrates '
                      'complete prospective sampling of the underlying events; only the freeze '
                      'receipt dates the declaration.'),
            'reason_codes': reasons}


def _coverage(decl, frozen, cap, rows, sequences, gaps, excluded, meta):
    cov, frozen_ms = decl['coverage'], frozen['observed_frozen_ms']
    observed_kinds = _counts(r['labels']['case_kind'] for r in rows)
    observed_symbols = sorted({r['symbol'] for r in rows})
    prospective = _prospective(decl, frozen_ms, rows)
    reasons, kind_status = list(prospective['reason_codes']), {}
    for kind, expected in sorted(cov['expected_kinds'].items()):
        got = observed_kinds.get(kind, 0)
        status = 'met' if got >= expected else ('absent' if not got else 'below_expected')
        kind_status[kind] = {'expected': expected, 'observed': got, 'status': status}
        if status != 'met':
            reasons.append('kind_%s:%s' % (status, kind))
    missing_symbols = [s for s in cov['expected_symbols'] if s not in set(observed_symbols)]
    if missing_symbols:
        reasons.append('declared_symbol_absent')
    if len(rows) < cov['min_rows']:
        reasons.append('rows_below_declared_minimum')
    if any(g['reason'] != 'observation_only_row_excluded_from_sequences' for g in gaps):
        reasons.append('missing_sequence')
    if any(g['reason'] == 'observation_only_row_excluded_from_sequences' for g in gaps):
        reasons.append('journal_features_first_observed_at_import')
    if any(e['reason'] in ('row_capacity', 'row_capacity_total') for e in excluded):
        reasons.append('row_capacity_truncated')
    if any(e['reason'] == 'known_after_observed_capture' for e in excluded):
        reasons.append('receipts_known_after_capture_refused')
    if any(r['producer'] == 'investigation_case' for r in rows):
        reasons.append('investigation_case_labels_are_classification_conditioned')
    if frozen['code_manifest'] != cap['code_manifest']:
        reasons.append('code_version_changed_between_freeze_and_capture')
    reasons += _status(decl, cap)['reason_codes']
    retention = meta.get('retention')
    if not isinstance(retention, dict):
        reasons.append('retention_unmeasured')
    else:
        reasons.append('retention_evicted_records' if retention.get('evicted_total')
                       else 'retention_no_evictions_observed')
    reasons += ['retention_bias_bounded_retained_store', 'no_complete_sampling_claim']
    return {'declared': cov, 'kind_status': kind_status, 'observed_kinds': observed_kinds,
            'observed_symbols': observed_symbols, 'missing_symbols': missing_symbols,
            'rows': len(rows), 'sequence_rows': len(sequences), 'excluded': len(excluded),
            'exclusion_reasons': _counts(e['reason'] for e in excluded), 'sequence_gaps': gaps,
            'prospective_sampling': prospective,
            'selection_bias': (CASE_SELECTION_BIAS
                               if any(r['producer'] == 'investigation_case' for r in rows) else None),
            'reason_codes': sorted(set(reasons))}


def _counts(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _accounting(rows, groups):
    """Honest skip/trade and unknown-P&L accounting. Nothing unknown becomes zero."""
    base = [r for r in rows if r['row_type'] != 'sequence']
    return {
        'rows': len(rows), 'base_rows': len(base), 'sequence_rows': len(rows) - len(base),
        'dependence_groups': len(set(groups.values())),
        'independent_units_upper_bound': len(set(groups.values())),
        'point_in_time_rows': sum(1 for r in base if r['point_in_time_features']),
        'observation_only_rows': sum(1 for r in base if not r['point_in_time_features']),
        'by_row_type': _counts(r['row_type'] for r in base),
        'by_case_kind': _counts(r['labels']['case_kind'] for r in base),
        'by_actual_status': _counts(r['labels']['actual_status'] for r in base),
        'by_sampling_mode': _counts(r['sampling_mode'] for r in base),
        'by_provenance': _counts(r['provenance'] for r in base),
        'unknown_actual_reasons': _counts(r['labels']['actual_reason'] for r in base
                                          if r['labels']['actual_status'] != 'verified_actual'),
        'skip_rows': sum(1 for r in base if r['labels']['case_kind'] == 'skip'),
        'trade_rows': sum(1 for r in base if r['labels']['case_kind'] == 'executed_trade'),
        'simulated_rows': sum(1 for r in base if r['labels'].get('simulated')),
        'verified_actual_rows': sum(1 for r in base if r['labels']['actual_status'] == 'verified_actual'),
        'unknown_actual_rows': sum(1 for r in base if r['labels']['actual_status'] != 'verified_actual'),
        'note': ('Unknown actual P&L is counted, never imputed as zero, and never taken from a '
                 'journal default or a simulation. Row counts are not independent observations; '
                 'use dependence_groups.'),
    }


def replay(dataset):
    """Rebuild from the dataset's own embedded declaration, receipts and clocks."""
    body = {k: v for k, v in dataset.items() if k != 'dataset_version'}
    if body.get('schema_version') != SCHEMA or digest(body) != dataset.get('dataset_version'):
        raise ValueError('dataset_integrity_mismatch')
    if body.get('code_manifest') != {'freeze': body['freeze']['code_manifest'],
                                     'capture': body['capture']['code_manifest']}:
        raise ValueError('dataset_code_manifest_mismatch')
    if build(body['declaration'], body['freeze'], body['capture'],
             body['receipts'], body['source_meta']) != dataset:
        raise ValueError('dataset_replay_mismatch')
    return dataset


def summary(dataset):
    c, a = dataset['coverage'], dataset['accounting']
    return {'dataset_id': dataset['declaration']['dataset_id'],
            'collection_mode': dataset['declaration']['collection_mode'],
            'declaration_version': dataset['declaration_version'],
            'dataset_version': dataset['dataset_version'],
            'observed_frozen_ms': dataset['freeze']['observed_frozen_ms'],
            'discovery_cut_ms': dataset['declaration']['discovery_cut_ms'],
            'rows': a['rows'], 'base_rows': a['base_rows'], 'sequence_rows': a['sequence_rows'],
            'point_in_time_rows': a['point_in_time_rows'],
            'observation_only_rows': a['observation_only_rows'],
            'dependence_groups': a['dependence_groups'], 'excluded': c['excluded'],
            'unknown_actual_rows': a['unknown_actual_rows'],
            'verified_actual_rows': a['verified_actual_rows'],
            'prospective_sampling': c['prospective_sampling']['status'],
            'readiness': dataset['status']['readiness'],
            'cut_reached': dataset['status']['cut_reached'],
            'search_ready': dataset['status']['search_ready'],
            'code_manifest': dataset['code_manifest']['capture'],
            'reason_codes': c['reason_codes']}
