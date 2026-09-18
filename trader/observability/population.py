"""Transactional prospective receipts with bounded, append-only export.

Only producer hooks can attest registration-time capture. Existing records are
reported as gaps, never reconstructed. Exhaustion fails the consumer transaction;
unexported receipts are never evicted. This module grants no search authority.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sqlite3
from contextlib import closing

from trader.cognition import dataset as D

MAX_EVENTS = 32768
MAX_PENDING = 256
MAX_PAYLOAD = 2 * 1024**2
MAX_EXPORT_BYTES = 256 * 1024**2


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def schema(db):
    # execute, not executescript: do not commit the caller's transaction.
    db.execute('CREATE TABLE IF NOT EXISTS population_events (id TEXT PRIMARY KEY, hash TEXT NOT NULL, local_imported_ms INTEGER NOT NULL, payload TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS population_meta (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')


def configured(directory):
    path = Path(directory) / 'pit_population.json'
    if not path.exists():
        return None
    if path.stat().st_size > 16384:
        raise ValueError('population_config_capacity')
    return json.loads(path.read_text())


class Producer:
    def __init__(self, db, config, stream, now):
        self.db, self.stream, self.now = db, stream, now
        self.declaration = json.loads(Path(config['declaration']).read_text())
        self.freeze = json.loads(Path(config['receipt']).read_text())
        D.validate_freeze(self.freeze, self.declaration)
        if self.declaration['collection_mode'] != 'forward' or now < self.freeze['observed_frozen_ms']:
            raise ValueError('population_invalid_freeze_clock')
        self.version = D.declaration_version(self.declaration)
        self.directory = Path(config['export_directory']) / stream
        schema(db)
        old = db.execute("SELECT payload FROM population_meta WHERE key='binding'").fetchone()
        binding = encode({'declaration_version': self.version, 'directory': str(self.directory.resolve())})
        if old and old[0] != binding:
            raise ValueError('population_binding_conflict')
        db.execute("INSERT OR IGNORE INTO population_meta VALUES ('binding',?)", (binding,))
        old = db.execute("SELECT payload FROM population_meta WHERE key='activation'").fetchone()
        if not old:
            db.execute("INSERT INTO population_meta VALUES ('activation',?)", (str(now),))
            self.emit('activation', 'activation', {'activated_ms': now,
                'missed_registration_interval': [self.declaration['start_ms'], min(now, self.declaration['discovery_cut_ms'])]
                if now > self.declaration['start_ms'] else None,
                'reason': 'not_collected_before_activation', 'complete_sampling_claim': False})
        self.activated = int(old[0]) if old else now
        previous = db.execute("SELECT payload FROM population_meta WHERE key='last_invocation'").fetchone()
        if previous and now < int(previous[0]):
            raise ValueError('population_clock_regression')
        if previous and now-int(previous[0]) > 600_000:
            start = max(int(previous[0]), self.declaration['start_ms'])
            end = min(now, self.declaration['discovery_cut_ms'])
            if start < end:
                self.emit('gap:cadence:'+str(now), 'gap', {'reason': 'collection_cadence_gap',
                                                        'interval_ms': [start, end]})
        db.execute("INSERT OR REPLACE INTO population_meta VALUES ('last_invocation',?)", (str(now),))

    def eligible(self, symbol, registered):
        return (symbol in self.declaration['universe'] and
                self.declaration['start_ms'] <= registered < self.declaration['discovery_cut_ms'])

    def emit(self, key, kind, source):
        identity = self.version + ':' + self.stream + ':' + key
        body = {'schema_version': 'pit-population-event.v1', 'id': identity,
                'kind': kind, 'declaration_version': self.version, 'stream': self.stream,
                'source': source, 'source_version': D.digest(source)}
        # Stable contents compared independently of the local export/import clock.
        h = D.digest(body)
        old = self.db.execute('SELECT hash FROM population_events WHERE id=?', (identity,)).fetchone()
        if old:
            if old[0] != h:
                raise ValueError('population_immutable_conflict')
            return
        if self.db.execute('SELECT COUNT(*) FROM population_events').fetchone()[0] >= MAX_EVENTS:
            raise ValueError('population_index_capacity')
        if self.db.execute('SELECT COUNT(*) FROM population_events WHERE payload IS NOT NULL').fetchone()[0] >= MAX_PENDING:
            raise ValueError('population_export_required')
        body.update(local_imported_ms=self.now, available_ms=self.now,
                    producer_code_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        body['sha256'] = D.digest(body)
        payload = encode(body)
        if len(payload.encode()) > MAX_PAYLOAD:
            raise ValueError('population_payload_capacity')
        self.db.execute('INSERT INTO population_events VALUES (?,?,?,?)', (identity, h, self.now, payload))

    def registration(self, key, symbol, registered, source):
        if not self.eligible(symbol, registered):
            return
        if registered != self.now or registered < self.activated:
            self.emit('gap:'+key, 'gap', {'episode_id': key, 'reason': 'missed_registration',
                                        'registered_ms': registered})
            return
        self.emit('registration:'+key, 'registration', source)

    def update(self, key, symbol, registered, version, source, terminal=False):
        if not self.eligible(symbol, registered):
            return
        identity = self.version+':'+self.stream+':registration:'+key
        if not self.db.execute('SELECT 1 FROM population_events WHERE id=?', (identity,)).fetchone():
            self.emit('gap:'+key, 'gap', {'episode_id': key, 'reason': 'missed_registration',
                                        'registered_ms': registered})
        self.emit(('terminal:'+key) if terminal else ('update:'+key+':'+version),
                  'terminal' if terminal else 'update', source)

    def scan(self, scan, decisions):
        if not self.declaration['start_ms'] <= self.now < self.declaration['discovery_cut_ms']:
            return
        # Invocation receipts retain repeats and skipped scans without inventing episodes.
        self.emit('scan:'+str(self.now), 'population', {
            'scan_id': scan['scan_id'], 'scan_as_of_ms': scan['as_of_ms'],
            'observed_ms': self.now, 'universe': self.declaration['universe'],
            'rows': [dict(row, declared_eligible=bool(row.get('eligible')) and
                          row.get('status') == 'ok' and row['symbol'] in self.declaration['universe'])
                     for row in decisions],
            'missing_symbols': sorted(set(self.declaration['universe'])-{r['symbol'] for r in decisions}),
            'membership': scan.get('membership'),
            'source_code_manifest': scan.get('code_manifest'),
            'reason': 'consumer_observed_scan_only', 'complete_sampling_claim': False})


def flush(db, config, stream):
    """Export committed outbox before any source pruning. Crash retries are exact.

    One exclusive immutable file per event; file fsync precedes transactional ack.
    Existing differing files and archive capacity refuse further source eviction.
    """
    schema(db)
    directory = Path(config['export_directory']) / stream
    directory.mkdir(parents=True, exist_ok=True)
    files = list(directory.iterdir())
    if len(files) > MAX_EVENTS:
        raise ValueError('population_export_capacity')
    size = sum(p.stat().st_size for p in files)
    for key, payload in db.execute('SELECT id,payload FROM population_events WHERE payload IS NOT NULL ORDER BY rowid').fetchall():
        path = directory / (hashlib.sha256(key.encode()).hexdigest()+'.json')
        raw = payload.encode()
        if path.exists():
            if path.stat().st_size > MAX_PAYLOAD or path.read_bytes() != raw:
                raise ValueError('population_export_conflict')
        else:
            if size + len(raw) > MAX_EXPORT_BYTES:
                raise ValueError('population_export_capacity')
            # Link a fully synced staging file into the immutable namespace. A
            # crash cannot publish a torn receipt; retries compare exact bytes.
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.pending-', delete=False) as out:
                staged = Path(out.name)
                try:
                    out.write(raw); out.flush(); os.fsync(out.fileno())
                    os.link(staged, path)
                finally:
                    staged.unlink(missing_ok=True)
            size += len(raw)
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        db.execute('UPDATE population_events SET payload=NULL WHERE id=?', (key,))
    db.commit()


def capture(config, now):
    """Self-contained coverage artifact; imported copies become known at this read.

    Producer clocks remain embedded. Reading copied files cannot backdate local
    restoration. The typed dataset continues to use the existing M2 producers.
    """
    from trader.cognition import outcomes as O
    from scripts.build_pit_dataset import cli_manifest
    declaration = json.loads(Path(config['declaration']).read_text())
    freeze = json.loads(Path(config['receipt']).read_text())
    D.validate_freeze(freeze, declaration)
    events, receipts, refusals = [], [], []
    local_clocks, local_indices = {}, {}
    total = 0
    for stream in ('forecast', 'investigation'):
        directory = Path(config['export_directory']) / stream
        ledger_path = config.get('local_ledgers', {}).get(stream)
        if ledger_path:
            with closing(sqlite3.connect(Path(ledger_path).resolve().as_uri()+'?mode=ro', uri=True, timeout=.1)) as db:
                db.execute('BEGIN')
                binding = db.execute("SELECT payload FROM population_meta WHERE key='binding'").fetchone()
                expected = encode({'declaration_version': D.declaration_version(declaration),
                                   'directory': str(directory.resolve())})
                if not binding or binding[0] != expected:
                    raise ValueError('population_local_receipt_mismatch')
                if db.execute('SELECT COUNT(*) FROM population_events').fetchone()[0] > MAX_EVENTS:
                    raise ValueError('population_index_capacity')
                local_indices[stream] = [dict(id=r[0], hash=r[1], local_imported_ms=r[2], pending=bool(r[3]))
                    for r in db.execute('SELECT id,hash,local_imported_ms,payload IS NOT NULL FROM population_events ORDER BY id')]
        indexed = {r['id']: r for r in local_indices.get(stream, [])}
        paths = sorted(directory.glob('*.json'))
        if len(paths) > MAX_EVENTS:
            raise ValueError('population_export_capacity')
        for path in paths:
            size = path.stat().st_size
            total += size
            if size > MAX_PAYLOAD or total > 2 * MAX_EXPORT_BYTES:
                raise ValueError('population_export_capacity')
            event = json.loads(path.read_text())
            if event.get('sha256') != D.digest({k: v for k, v in event.items() if k != 'sha256'}):
                raise ValueError('population_integrity_mismatch')
            if (event['declaration_version'] != D.declaration_version(declaration) or
                    event['source_version'] != D.digest(event['source']) or
                    event['stream'] != stream or event['available_ms'] > now or
                    event['local_imported_ms'] > now):
                raise ValueError('population_receipt_mismatch')
            ledger_path = config.get('local_ledgers', {}).get(stream)
            if ledger_path:
                # A live local index retains the import clock independently of
                # exported payloads. Without it, copied archives are known now.
                row = indexed.get(event['id'])
                stable = {k: v for k, v in event.items() if k not in
                          ('local_imported_ms', 'available_ms', 'producer_code_hash', 'sha256')}
                if not row or row['hash'] != D.digest(stable) or row['local_imported_ms'] != event['local_imported_ms']:
                    raise ValueError('population_local_receipt_mismatch')
                local_clocks[event['id']] = row['local_imported_ms']
            events.append(event)
            if event['kind'] != 'terminal':
                continue
            source = event['source']
            try:
                records = []
                if stream == 'forecast' and source['status'] == 'resolved':
                    source = dict(source)
                    raw = source.pop('source_receipts')
                    source['source_receipt'] = raw['registration'] + raw['outcome']
                    records = [O.forecast(source, event['local_imported_ms'])]
                elif stream == 'investigation' and source['updates'][-1]['evidence']['status'] == 'measured':
                    inv, update = source['investigation'], source['updates'][-1]
                    archive = dict(investigation=inv, update=update, inputs=source['inputs'])
                    kinds = []
                    if dict(update['assessment']).get('same_direction') == 'contradicted':
                        kinds.append('false_signal')
                    if inv['primary_trigger'] == 'volatility_transition':
                        kinds.append('regime_transition')
                    records = [O.investigation_case(archive, kind, event['local_imported_ms']) for kind in kinds]
                for record in records:
                    receipts.append({'source_key': event['id']+':'+record['kind'],
                                     'local_imported_ms': local_clocks.get(event['id'], now), 'record': record})
            except (ValueError, KeyError, TypeError) as exc:
                refusals.append({'event_id': event['id'], 'reason': str(exc)[:120]})
    registrations_by_id = {e['id']: e for e in events if e['kind'] == 'registration'}
    # A terminal imported after activation cannot substitute for a missed
    # registration-time receipt, even if its embedded registration is older.
    receipts = [r for r in receipts if r['source_key'].rsplit(':', 1)[0].replace(
        ':terminal:', ':registration:') in registrations_by_id]
    verify_events(events)
    manifest = cli_manifest()
    manifest['population'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest['cohort'] = hashlib.sha256(Path(__file__).with_name('cohort.py').read_bytes()).hexdigest()
    dataset = D.build(declaration, freeze, D.capture_receipt(now, code_manifest=manifest), receipts,
        {'source': 'population_append_only_exports', 'local_clock': 'verified_local_index_or_archive_read',
         'trustworthy_local_receipt': bool(events) and len(local_clocks) == len(events), 'observed_source_read_ms': now})
    D.replay(dataset)
    registrations = [e for e in events if e['kind'] == 'registration']
    terminals = [e for e in events if e['kind'] == 'terminal']
    from .cohort import review
    body = {'schema_version': 'pit-population-capture.v2',
            'local_indices': local_indices,
            'cohort': review(events, declaration, now, local_indices), 'observed_capture_ms': now,
            'freeze': freeze, 'events': events, 'dataset': dataset,
            'coverage': {'registrations': len(registrations), 'terminal_results': len(terminals),
                'population_receipts': sum(e['kind'] == 'population' for e in events),
                'explicit_gaps': [e for e in events if e['kind'] == 'gap'],
                'typed_outcome_refusals': refusals, 'complete_sampling_claim': False,
                'search_ready': False, 'reason': 'requires_window_close_coverage_review_and_separate_search_protocol',
                'denominator': 'registered_episodes_and_observed_scan_rows_only_not_all_market_events',
                'selection_warning': 'classification_selected_typed_rows_cannot_establish_population_rates',
                'accounting': 'observed_movement_is_not_execution_pnl; missing_accounting_unknown',
                'local_restoration_ms': None if events and len(local_clocks) == len(events) else now,
                'verified_local_receipts': len(local_clocks)}}
    result = dict(body, sha256=D.digest(body))
    replay_capture(result)
    return result


def verify_events(events):
    from . import memory
    seen, registrations = set(), {}
    for e in events:
        if e['id'] in seen or e.get('sha256') != D.digest({k: v for k, v in e.items() if k != 'sha256'}):
            raise ValueError('population_integrity_mismatch')
        seen.add(e['id'])
        if e['source_version'] != D.digest(e['source']):
            raise ValueError('population_source_mismatch')
        if e['kind'] == 'registration':
            source = e['source']
            registered = (source['registered_ms'] if e['stream'] == 'forecast'
                          else source['investigation']['registered_ms'])
            if registered != e['local_imported_ms'] or registered != e['available_ms']:
                raise ValueError('population_registration_clock_mismatch')
            registrations[e['id']] = e
    for e in events:
        if e['stream'] == 'investigation' and e['kind'] in ('registration', 'update', 'terminal'):
            source = dict(e['source'])
            context = source.pop('memory', {'context': None, 'reasoning': []})
            archive = {'schema_version': 'investigation-archive.v1', 'source': source, 'memory': context}
            memory.replay_export(dict(archive, sha256=D.digest(archive)))
        if e['kind'] != 'terminal':
            continue
        reg = registrations.get(e['id'].replace(':terminal:', ':registration:'))
        if reg:
            field = 'prediction' if e['stream'] == 'forecast' else 'investigation'
            if reg['source'][field] != e['source'][field]:
                raise ValueError('population_registration_terminal_mismatch')


def replay_capture(artifact):
    if artifact.get('sha256') != D.digest({k: v for k, v in artifact.items() if k != 'sha256'}):
        raise ValueError('population_capture_integrity')
    D.validate_freeze(artifact['freeze'], artifact['dataset']['declaration'])
    verify_events(artifact['events'])
    D.replay(artifact['dataset'])
    if artifact['schema_version'] == 'pit-population-capture.v2':
        from .cohort import review
        expected = review(artifact['events'], artifact['dataset']['declaration'],
                          artifact['observed_capture_ms'], artifact['local_indices'])
        if expected != artifact['cohort']:
            raise ValueError('population_cohort_replay_mismatch')
    elif artifact['schema_version'] != 'pit-population-capture.v1':
        raise ValueError('population_capture_unknown_schema')
    return artifact['coverage']
