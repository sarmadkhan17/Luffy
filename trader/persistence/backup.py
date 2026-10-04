"""Versioned, integrity checked generations. No scheduler, retention or transport.

SQLite online backup captures one transactionally consistent store at a time.
File capture is checked twice across the capture interval. The manifest explicitly
makes no global atomicity claim. Restore only publishes an absent offline root:
replacing multiple existing live paths is deliberately unsupported.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import time
from uuid import uuid4

SCHEMA = 'luffy-critical-backup.v1'
SECRET_KEYS = frozenset(('api_key', 'apikey', 'api_secret', 'apisecret', 'secret',
    'password', 'session_key', 'session_secret', 'access_token', 'auth_token',
    'bot_token', 'token', 'private_key', 'hmac_key', 'client_secret', 'credentials'))


class Refused(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable(path, raw):
    with path.open('xb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    sync_dir(path.parent)


def safe_path(root, name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or str(p) != name or '\\' in name:
        raise Refused('unsafe_path')
    if any(part.startswith('.env') or part in ('node_modules', 'graphify-out', '__pycache__',
               '.git', 'cache', 'caches', 'tv_profile') or any(x in part.lower() for x in
               ('credential', 'secret', 'session', 'socket', 'api_key', 'token')) for part in p.parts):
        raise Refused('excluded_secret_or_cache_path')
    if p.suffix.lower() in ('.pem', '.key', '.sock'):
        raise Refused('excluded_secret_or_socket_path')
    dest = root / name
    if not dest.resolve().is_relative_to(root.resolve()) or any(
            parent.is_symlink() for parent in (dest, *dest.parents) if parent != root.parent):
        raise Refused('symlink_path')
    return dest


@dataclass(frozen=True)
class Asset:
    path: str
    role: str
    kind: str = 'file'
    store_schema: dict | None = None
    dependency_identity: dict | None = None

    def __post_init__(self):
        if self.kind not in ('file', 'sqlite') or not self.role:
            raise Refused('invalid_asset')


@dataclass(frozen=True)
class Inventory:
    assets: tuple[Asset, ...]
    version: str = 'luffy-critical-inventory.v1'

    def validate(self, root):
        if not self.assets or len({a.path for a in self.assets}) != len(self.assets):
            raise Refused('empty_or_duplicate_inventory')
        for a in self.assets:
            safe_path(root, a.path)


# These are logical stores, not repository globbing. Present optional stores
# become mandatory assets of this generation. Configured external stores and
# non-PIT detached references require explicit Asset registrations. PIT closure
# is mandatory and derived from the configuration plus retained ledger indexes.
STORES = {
    'data/luffy.db': 'control_execution_strategy_world_research_learning_owner_audit',
    'data/candles.db': 'market_reference_provenance_replay',
    'data/derivs.db': 'derivative_provenance_replay',
    'data/attention.db': 'attention_evidence',
    'data/declared-population/attention.db': 'declared_population_evidence',
    'data/attention_learning.db': 'learning_evidence',
    'data/investigation.db': 'research_investigation_evidence',
    'data/runtime-portfolio.db': 'portfolio_authority_evidence',
    'data/execution-evidence.db': 'execution_evidence',
    'data/stage5-public/public.db': 'public_execution_evidence',
    'data/accounting-worker/queue.db': 'accounting_queue_evidence',
}
FILES = {
    'SDD.md': 'architecture_authority', 'STATE.yaml': 'implementation_authority',
    'NEXT.yaml': 'work_authority', 'config.yaml': 'versioned_runtime_config',
    'org.yaml': 'component_configuration', 'data/doctrine.json': 'frozen_doctrine',
    'data/agent_weights.json': 'retained_analyst_weights',
    'data/ewa_state.json': 'retained_analyst_ewa_decision_input',
    'data/stage5-activation.json': 'owner_activation_window',
    'data/safety_health.json': 'safety_outage_audit_state',
    'data/attention_health.json': 'attention_capture_health_evidence',
    'data/attention_learning_health.json': 'learning_capture_health_evidence',
    'data/investigation_health.json': 'research_capture_health_evidence',
}


def critical_inventory(root, *, registered=()):
    root = Path(root)
    if not safe_path(root, 'data/ewa_state.json').is_file():
        raise Refused('retained_decision_input_missing:data/ewa_state.json')
    assets = [Asset(p, r, 'sqlite', metadata(safe_path(root, p))) for p, r in STORES.items()
              if p == 'data/luffy.db' or (root / p).exists()]
    assets += [Asset(p, r) for p, r in FILES.items()
               if p in ('SDD.md', 'STATE.yaml', 'NEXT.yaml', 'config.yaml') or (root / p).exists()]
    import yaml
    cfg = yaml.safe_load(exact_file(root / 'config.yaml')) or {}
    for kind, configured in ((cfg.get('research') or {}).get('paths') or {}).items():
        if kind in ('candles', 'derivs') and configured:
            path = Path(configured)
            if path.is_absolute():
                if not path.resolve().is_relative_to(root.resolve()):
                    raise Refused('configured_store_outside_inventory_root_requires_explicit_migration')
                path = path.relative_to(root)
            name = path.as_posix()
            if name not in {a.path for a in assets}:
                assets.append(Asset(name, kind + '_configured_replay', 'sqlite', metadata(safe_path(root, name))))
    for path in (root / 'data/accounting-worker').glob('*.json'):
        # Frozen accounting receipts referenced by queue.db are replay evidence,
        # never cache/build outputs. Capture all retained receipts in this one
        # narrowly identified store; the queue snapshot is checked below.
        assets.append(Asset(path.relative_to(root).as_posix(), 'accounting_receipt'))
    for path in (root / 'data/safety-incidents').glob('*.json'):
        assets.append(Asset(path.relative_to(root).as_posix(), 'safety_incident_receipt'))
    assets += list(registered)
    from .pit_inventory import dependencies
    closure = dependencies(root, root, [a.path for a in assets if a.kind == 'sqlite'])
    for dependency in closure:
        present = next((a for a in assets if a.path == dependency.path), None)
        if present is None:
            if dependency.kind == 'sqlite':
                dependency = Asset(dependency.path, dependency.role, 'sqlite',
                                   metadata(safe_path(root, dependency.path)), dependency.dependency_identity)
            assets.append(dependency)
    inv = Inventory(tuple(assets))
    inv.validate(root)
    return inv


def metadata(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise Refused('sqlite_integrity_failed')
        catalog = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                             "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        return dict(user_version=db.execute('PRAGMA user_version').fetchone()[0],
                    application_id=db.execute('PRAGMA application_id').fetchone()[0],
                    schema_sha256=digest(canonical(catalog)), sqlite_version=sqlite3.sqlite_version)


def no_store_secrets(path):
    """Refuse recognized credential storage; never redact critical state."""
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables & {'credentials', 'secrets', 'api_keys', 'session_keys', 'session_tokens'}:
            raise Refused('secret_store_not_backuppable')
        if 'state_kv' in tables:
            for key, value in db.execute('SELECT key,value FROM state_kv'):
                if str(key).lower().replace('-', '_') in SECRET_KEYS and value:
                    raise Refused('secret_content_in_operational_store')


def exact_file(path):
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise Refused('file_changed_during_capture')
    return raw


def no_config_secrets(raw, path):
    if path.suffix not in ('.yaml', '.json'):
        return
    import yaml
    value = yaml.safe_load(raw)
    def visit(v):
        if isinstance(v, dict):
            for k, val in v.items():
                name = str(k).lower().replace('-', '_')
                if name in SECRET_KEYS and val:
                    raise Refused('secret_content_in_configuration')
                visit(val)
        elif isinstance(v, list):
            for val in v:
                visit(val)
    visit(value)


def rename_absent(source, target):
    """Linux renameat2(RENAME_NOREPLACE): atomically refuse every existing target.
    No fallback to a non-atomic multi-file or overwrite operation."""
    import ctypes
    import errno
    libc = ctypes.CDLL(None, use_errno=True)
    fn = getattr(libc, 'renameat2', None)
    if fn is None:
        raise Refused('atomic_noreplace_unsupported')
    result = fn(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    if result != 0:
        error = ctypes.get_errno()
        if error in (errno.ENOSYS, errno.EINVAL, errno.EEXIST, errno.ENOTEMPTY):
            raise Refused('atomic_publish_refused')
        raise OSError(error, os.strerror(error))


@dataclass(frozen=True)
class BackupManifest:
    schema: str
    inventory_version: str
    backup_id: str
    created_at: float
    captured_at: float
    repository_version: str
    source_root: str
    capture_boundary: str
    destination_claim: str
    completion: str
    assets: list[dict]

    def __post_init__(self):
        import math
        if (self.schema != SCHEMA or self.completion != 'COMPLETE'
                or not isinstance(self.backup_id, str) or len(self.backup_id) != 32
                or any(ch not in '0123456789abcdef' for ch in self.backup_id)
                or not isinstance(self.repository_version, str) or not self.repository_version
                or type(self.created_at) not in (int, float) or type(self.captured_at) not in (int, float)
                or not math.isfinite(self.created_at) or not math.isfinite(self.captured_at)
                or self.created_at > self.captured_at or not isinstance(self.assets, list)):
            raise Refused('invalid_manifest_contract')
        for e in self.assets:
            if (not isinstance(e, dict) or type(e.get('size')) is not int or e['size'] < 0
                    or not isinstance(e.get('sha256'), str) or len(e['sha256']) != 64
                    or any(ch not in '0123456789abcdef' for ch in e['sha256'])
                    or type(e.get('capture_started_at')) not in (int, float)
                    or type(e.get('capture_finished_at')) not in (int, float)
                    or not self.created_at <= e['capture_started_at'] <= e['capture_finished_at'] <= self.captured_at):
                raise Refused('invalid_asset_contract')


class BackupSets:
    def __init__(self, source_root, destination_root, inventory, *, repository_version,
                 clock=time.time):
        self.source = Path(source_root).resolve()
        self.destination = Path(destination_root).resolve()
        if (self.destination.is_relative_to(self.source) or self.source.is_relative_to(self.destination)):
            raise Refused('destination_must_be_outside_source_runtime_tree')
        self.inventory = inventory
        inventory.validate(self.source)
        required = {'data/luffy.db', 'SDD.md', 'STATE.yaml', 'NEXT.yaml', 'config.yaml',
                    'data/ewa_state.json'}
        if not required.issubset({a.path for a in inventory.assets}):
            raise Refused('critical_inventory_incomplete')
        for a in inventory.assets:
            if a.path in required and (a.role != (STORES.get(a.path) or FILES[a.path])
                    or a.kind != ('sqlite' if a.path in STORES else 'file')):
                raise Refused('critical_inventory_role_or_kind')
        if not repository_version:
            raise Refused('repository_version_required')
        self.compatible_stores = {}
        for a in inventory.assets:
            if a.kind == 'sqlite':
                self.compatible_stores[a.path] = a.store_schema or metadata(safe_path(self.source, a.path))
        if (self.source / 'data/luffy.db').is_file():
            self._verify_pit_dependencies(self.source)
        self.repository_version = repository_version
        self.clock = clock

    def capture(self, *, interrupt=lambda phase: None):
        self.destination.mkdir(parents=True, exist_ok=True)
        backup_id = uuid4().hex
        stage = self.destination / (backup_id + '.incomplete')
        stage.mkdir()
        started = self.clock()
        entries, files = [], {}
        for asset in self.inventory.assets:
            source = safe_path(self.source, asset.path)
            target = safe_path(stage, asset.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            begin = self.clock()
            store = None
            if asset.kind == 'sqlite':
                with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
                    with closing(sqlite3.connect(target)) as out:
                        src.backup(out)
                        out.execute('PRAGMA journal_mode=DELETE')
                store = metadata(target)
                no_store_secrets(target)
                with target.open('rb') as f:
                    os.fsync(f.fileno())
            else:
                raw = exact_file(source)
                no_config_secrets(raw, source)
                files[asset.path] = digest(raw)
                durable(target, raw)
            raw = target.read_bytes()
            entries.append(dict(**asdict(asset), size=len(raw), sha256=digest(raw), store=store,
                                capture_started_at=begin, capture_finished_at=self.clock()))
            interrupt('asset')
        for path, sha in files.items():
            if digest(exact_file(safe_path(self.source, path))) != sha:
                raise Refused('files_changed_across_capture')
        manifest = dict(schema=SCHEMA, inventory_version=self.inventory.version, backup_id=backup_id,
                        created_at=started, captured_at=self.clock(), repository_version=self.repository_version,
                        source_root=str(self.source), capture_boundary='per_store_transaction; files_stable_over_interval; NOT_GLOBAL_ATOMIC',
                        destination_claim='configured_filesystem; physical_offhost_NOT_VERIFIED',
                        completion='COMPLETE', assets=entries)
        manifest = asdict(BackupManifest(**manifest))
        raw = canonical(manifest)
        self._verify_references(stage)
        durable(stage / 'manifest.json', raw)
        durable(stage / 'COMPLETE', digest(raw).encode())
        self.verify(stage, allow_staging=True)
        interrupt('before_publish')
        # All nested directory entries are made durable before publishing.
        for directory, _, _ in os.walk(stage, topdown=False):
            sync_dir(Path(directory))
        published = self.destination / backup_id
        rename_absent(stage, published)
        sync_dir(self.destination)
        return published

    def verify(self, generation, *, allow_staging=False):
        generation = Path(generation)
        if generation.is_symlink() or (not allow_staging and generation.name.endswith('.incomplete')):
            raise Refused('incomplete_or_symlink_generation')
        try:
            raw = safe_path(generation, 'manifest.json').read_bytes()
            if safe_path(generation, 'COMPLETE').read_bytes() != digest(raw).encode():
                raise Refused('manifest_hash_mismatch')
            def unique(pairs):
                out = {}
                for k, v in pairs:
                    if k in out:
                        raise Refused('duplicate_manifest_key')
                    out[k] = v
                return out
            manifest = asdict(BackupManifest(**json.loads(raw, object_pairs_hook=unique)))
            if (manifest['schema'] != SCHEMA or manifest['completion'] != 'COMPLETE'
                    or manifest['inventory_version'] != self.inventory.version
                    or generation.name != manifest['backup_id'] + ('.incomplete' if allow_staging else '')):
                raise Refused('manifest_schema_or_completion')
            if raw != canonical(manifest):
                raise Refused('noncanonical_manifest')
            expected = {a.path: a for a in self.inventory.assets}
            entries = manifest['assets']
            if len(entries) != len(expected) or {e['path'] for e in entries} != set(expected):
                raise Refused('missing_duplicate_or_unexpected_asset')
            for e in entries:
                a = expected[e['path']]
                if e.get('dependency_identity') != a.dependency_identity:
                    raise Refused('dependency_identity_mismatch')
                if (e['role'], e['kind']) != (a.role, a.kind):
                    raise Refused('asset_role_or_kind')
                path = safe_path(generation, e['path'])
                raw_asset = path.read_bytes()
                if len(raw_asset) != e['size'] or digest(raw_asset) != e['sha256']:
                    raise Refused('asset_size_or_hash_mismatch')
                if a.kind == 'sqlite':
                    actual = metadata(path)
                    no_store_secrets(path)
                    if any(actual[k] != e['store'][k] for k in ('user_version', 'application_id', 'schema_sha256')):
                        raise Refused('store_metadata_mismatch')
                    # Compatible with the registered current source schema; restore
                    # never runs implicit migrations against an unknown version.
                    source_meta = self.compatible_stores[a.path]
                    if any(actual[k] != source_meta[k] for k in ('user_version', 'application_id', 'schema_sha256')):
                        raise Refused('incompatible_store_schema')
            permitted = set(expected) | {'manifest.json', 'COMPLETE'}
            for p in generation.rglob('*'):
                if p.is_symlink() or (p.is_file() and p.relative_to(generation).as_posix() not in permitted):
                    raise Refused('unexpected_generation_content')
            self._verify_references(generation)
            return manifest
        except (OSError, sqlite3.Error, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise Refused('backup_unavailable_or_invalid:' + type(exc).__name__) from exc

    def _verify_pit_dependencies(self, root):
        from .pit_inventory import dependencies
        expected = {a.path: a for a in self.inventory.assets}
        stores = {a.path for a in self.inventory.assets if a.kind == 'sqlite'}
        stores.update(path for path in STORES if (Path(root) / path).is_file())
        closure = dependencies(root, self.source, stores)
        for dependency in closure:
            asset = expected.get(dependency.path)
            if (asset is None or asset.kind != dependency.kind
                    or (asset.kind == 'file' and asset.role != dependency.role)):
                raise Refused('pit_critical_inventory_incomplete:' + dependency.path)
            if dependency.dependency_identity != asset.dependency_identity:
                raise Refused('pit_dependency_identity_mismatch')

    def _verify_references(self, root):
        retained = next((a for a in self.inventory.assets if a.path == 'data/ewa_state.json'), None)
        if retained is None or retained.role != FILES['data/ewa_state.json'] or retained.kind != 'file':
            raise Refused('retained_decision_input_inventory_incomplete')
        if not safe_path(root, retained.path).is_file():
            raise Refused('retained_decision_input_missing:' + retained.path)
        self._verify_pit_dependencies(root)
        queue = root / 'data/accounting-worker/queue.db'
        if not queue.exists():
            return
        with closing(sqlite3.connect(queue.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            for (path,) in db.execute('SELECT path FROM attempts'):
                source_path = Path(path)
                if source_path.is_absolute():
                    try:
                        source_path = source_path.relative_to(self.source)
                    except ValueError as exc:
                        raise Refused('accounting_artifact_outside_inventory') from exc
                name = source_path.as_posix()
                if name not in {a.path for a in self.inventory.assets} or not safe_path(root, name).is_file():
                    raise Refused('referenced_accounting_artifact_missing')

    def discover(self):
        return [p for p in self.destination.iterdir() if p.is_dir() and not p.name.endswith('.incomplete')
                and (p / 'COMPLETE').is_file()] if self.destination.exists() else []

    def restore(self, generation, target, *, interrupt=lambda phase: None):
        target = Path(target).absolute()
        receipt = dict(schema='luffy-restore-receipt.v1', backup_id=None, manifest_hash=None,
                       destination=str(target), assets_verified=[], verification='REFUSED',
                       restore='REFUSED', reasons=[], time=self.clock(), identity_status='UNVERIFIED')
        try:
            candidate_raw = (Path(generation) / 'manifest.json').read_bytes()
            receipt['manifest_hash'] = digest(candidate_raw)
            try:
                candidate = json.loads(candidate_raw)
                receipt['backup_id'] = candidate.get('backup_id') if isinstance(candidate, dict) else None
            except (ValueError, TypeError):
                pass
        except OSError:
            pass
        try:
            if (target.exists() or target.is_symlink() or any(p.is_symlink() for p in target.parents)
                    or target.resolve().is_relative_to(self.source)):
                raise Refused('restore_requires_absent_offline_target')
            manifest = self.verify(generation)
            receipt.update(backup_id=manifest['backup_id'], manifest_hash=digest(canonical(manifest)),
                           assets_verified=[e['path'] for e in manifest['assets']], verification='PASS', identity_status='VERIFIED')
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = target.with_name(target.name + '.restore-' + uuid4().hex)
            stage.mkdir()
            for e in manifest['assets']:
                dest = safe_path(stage, e['path'])
                dest.parent.mkdir(parents=True, exist_ok=True)
                raw = safe_path(Path(generation), e['path']).read_bytes()
                if len(raw) != e['size'] or digest(raw) != e['sha256']:
                    raise Refused('asset_changed_during_restore')
                durable(dest, raw)
                if e['kind'] == 'sqlite' and any(metadata(dest)[k] != e['store'][k] for k in ('user_version', 'application_id', 'schema_sha256')):
                    raise Refused('restored_store_invalid')
                interrupt('asset')
            journal_asset = next((a for a in self.inventory.assets if a.role == STORES['data/luffy.db']), None)
            if journal_asset is None:
                raise Refused('operational_journal_required_for_safe_restore')
            # Existing state architecture is authoritative; no restored ACTIVE
            # permission survives. Old Supervisor containment ownership is invalid.
            from trader.core.journal import Journal
            from trader.core.types import ControlState
            from trader.engine.state import ControlStateMachine
            import threading
            # Reuse Journal's connection/transaction methods without invoking
            # constructor migrations or backfills on restored captured state.
            journal = object.__new__(Journal)
            journal.db_path = stage / journal_asset.path
            journal._local = threading.local()
            journal._write_lock = threading.Lock()
            machine = ControlStateMachine(journal)
            if machine.state == ControlState.ACTIVE:
                machine.set(ControlState.FROZEN, 'supervisor', 'restore requires venue recovery')
                machine.set(ControlState.RECOVERY, 'supervisor', 'restore requires venue recovery')
            journal.kv_set('supervisor_status', json.dumps(dict(needs_owner=True, reasons=['restored_state_requires_recovery'],
                           containment_owned=False, safe_to_activate=False, outcome='RECOVERING')))
            journal.log_control_event('restore_verified', 'supervisor', detail=receipt)
            journal._conn().close()
            # Health/control files are generated, never copied from old runtime.
            receipt['restored_control_state'] = machine.state.value
            receipt['restored_assets'] = {a.path: digest((stage / a.path).read_bytes()) for a in self.inventory.assets}
            receipt.update(restore='VERIFIED_STAGED', time=self.clock())
            durable(stage / 'restore_receipt.json', canonical(receipt))
            interrupt('before_publish')
            for directory, _, _ in os.walk(stage, topdown=False):
                sync_dir(Path(directory))
            if target.exists():
                raise Refused('restore_target_appeared')
            # rename cannot partially publish files. Caller must own the offline
            # parent; an existing nonempty directory cannot be replaced.
            rename_absent(stage, target)
            sync_dir(target.parent)
            receipt.update(restore='PUBLISHED', time=self.clock())
        except Exception as exc:
            receipt['reasons'] = [str(exc) if isinstance(exc, Refused) else type(exc).__name__]
            receipt['restore'] = 'REFUSED'
            self._receipt(receipt)
            raise Refused('restore_refused:' + receipt['reasons'][0]) from exc
        self._receipt(receipt)
        return receipt

    def _receipt(self, receipt):
        directory = self.destination / 'restore-receipts'
        directory.mkdir(parents=True, exist_ok=True)
        durable(directory / (uuid4().hex + '.json'), canonical(receipt))
