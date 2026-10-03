"""Exact PIT dependencies, derived from configuration and retained ledger indexes.

Export acknowledgement removes payloads from SQLite. Its id/hash/import clock
remain authoritative; directory contents cannot substitute for those identities.
Paths outside the source tree require migration, never an implicit directory copy.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def dependencies(root, source_root, sqlite_paths):
    from .backup import Asset, Refused, safe_path, exact_file
    from trader.cognition import dataset as D
    root, source_root = Path(root), Path(source_root).resolve()
    result = {}

    def name(reference):
        path = Path(reference)
        if path.is_absolute():
            try:
                path = path.relative_to(source_root)
            except ValueError as exc:
                raise Refused('pit_dependency_outside_source_root') from exc
        value = path.as_posix()
        safe_path(root, value)
        return value

    def require(reference, role, identity=None, kind='file'):
        value = name(reference)
        path = safe_path(root, value)
        if not path.is_file():
            raise Refused('pit_required_dependency_missing:' + value)
        asset = Asset(value, role, kind, dependency_identity=identity)
        old = result.get(value)
        if old is not None and old != asset:
            raise Refused('pit_dependency_identity_conflict')
        result[value] = asset
        return path

    def read(reference, role):
        return json.loads(exact_file(require(reference, role)))

    try:
        config_path = root / 'data/pit_population.json'
        ledger_paths = set(sqlite_paths)
        configs = None
        if config_path.exists():
            cfg = read('data/pit_population.json', 'pit_population_configuration')
            declaration = read(cfg['declaration'], 'pit_population_declaration')
            freeze = read(cfg['receipt'], 'pit_population_freeze')
            D.validate_freeze(freeze, declaration)
            version = D.declaration_version(declaration)
            configs = cfg
            declarations = {version: declaration}
            for reference in cfg.get('prior_declarations', []):
                prior = read(reference, 'pit_population_declaration')
                declarations[D.declaration_version(prior)] = prior
            for reference in [cfg['declaration'], *cfg.get('prior_declarations', [])]:
                path = name(reference)
                d = json.loads(exact_file(safe_path(root, path)))
                result[path] = Asset(path, 'pit_population_declaration', dependency_identity={
                    'declaration_version': D.declaration_version(d)})
            path = name(cfg['receipt'])
            result[path] = Asset(path, 'pit_population_freeze', dependency_identity={
                'schema_version': freeze['schema_version'], 'declaration_version': version,
                'freeze_sha256': D.digest(freeze)})
            for stream, reference in cfg.get('local_ledgers', {}).items():
                path = require(reference, 'pit_population_ledger', kind='sqlite')
                ledger_paths.add(path.relative_to(root).as_posix())
        else:
            declarations = {}

        for ledger in sorted(ledger_paths):
            path = safe_path(root, ledger)
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                db.execute('BEGIN')
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables & {'population_meta', 'population_events'}:
                    continue
                if configs is None or not {'population_meta', 'population_events'} <= tables:
                    raise Refused('pit_population_configuration_or_ledger_missing')
                require(ledger, 'pit_population_ledger', kind='sqlite')
                bindings = {}
                for (raw,) in db.execute("SELECT payload FROM population_meta WHERE key='binding' OR key LIKE 'binding:%'"):
                    bound = json.loads(raw)
                    v = bound['declaration_version']
                    if v not in declarations:
                        raise Refused('pit_bound_declaration_unresolved')
                    if bound.get('freeze_sha256') and bound['freeze_sha256'] != D.digest(freeze):
                        # The producer contract has no archived freeze path. Refuse
                        # unresolved evidence instead of guessing a sibling filename.
                        raise Refused('pit_bound_freeze_unresolved')
                    directory = name(bound['directory'])
                    stream = Path(directory).name
                    if stream not in ('forecast', 'investigation') or v in bindings:
                        raise Refused('pit_invalid_binding')
                    bindings[v] = (directory, stream)
                for event_id, stable_hash, imported, payload in db.execute(
                        'SELECT id,hash,local_imported_ms,payload FROM population_events'):
                    v = event_id.split(':', 1)[0]
                    if v not in bindings:
                        raise Refused('pit_event_binding_unresolved')
                    directory, stream = bindings[v]
                    reference = directory + '/' + hashlib.sha256(event_id.encode()).hexdigest() + '.json'
                    exported = safe_path(root, reference)
                    if payload is None or exported.exists():
                        raw = exact_file(require(reference, 'pit_population_exported_receipt'))
                        event = json.loads(raw)
                        if payload is not None and raw != payload.encode():
                            raise Refused('pit_pending_export_mismatch')
                        result[reference] = Asset(reference, 'pit_population_exported_receipt', dependency_identity={
                            'schema_version': event['schema_version'], 'id': event_id,
                            'declaration_version': v, 'stable_sha256': stable_hash,
                            'receipt_sha256': event['sha256'], 'local_imported_ms': imported})
                    else:
                        event = json.loads(payload)
                    stable = {k: val for k, val in event.items() if k not in
                              ('local_imported_ms', 'available_ms', 'producer_code_hash', 'sha256')}
                    if (event['schema_version'] != 'pit-population-event.v1' or event['id'] != event_id
                            or event['stream'] != stream or event['declaration_version'] != v
                            or event['local_imported_ms'] != imported or D.digest(stable) != stable_hash
                            or event['source_version'] != D.digest(event['source'])
                            or event['sha256'] != D.digest({k: val for k, val in event.items() if k != 'sha256'})):
                        raise Refused('pit_receipt_identity_mismatch')
        if configs is not None:
            # capture() also accepts archives without local indexes. Only the two
            # explicit receipt streams are inspected; never other directory files.
            directory = name(configs['export_directory'])
            for stream in ('forecast', 'investigation'):
                for path in sorted(safe_path(root, directory + '/' + stream).glob('*.json')):
                    reference = path.relative_to(root).as_posix()
                    if reference in result:
                        continue
                    event = json.loads(exact_file(path))
                    if (event['declaration_version'] != version or event['stream'] != stream
                            or event['sha256'] != D.digest({k: val for k, val in event.items() if k != 'sha256'})
                            or event['source_version'] != D.digest(event['source'])
                            or path.name != hashlib.sha256(event['id'].encode()).hexdigest() + '.json'):
                        raise Refused('pit_unindexed_receipt_identity_mismatch')
                    require(reference, 'pit_population_exported_receipt', {
                        'schema_version': event['schema_version'], 'id': event['id'],
                        'declaration_version': version, 'receipt_sha256': event['sha256']})
        return tuple(result.values())
    except (OSError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        if isinstance(exc, Refused):
            raise
        raise Refused('pit_dependencies_unresolved:' + type(exc).__name__) from exc
