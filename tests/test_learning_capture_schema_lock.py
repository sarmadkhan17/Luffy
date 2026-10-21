import sqlite3

import pytest

from trader.learning import capture as C


def opened(tmp_path):
    path = tmp_path / 'capture.db'
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    C.ensure(db)
    db.commit()
    return path, db


def test_existing_capture_schema_does_not_reserve_sqlite_writer(tmp_path):
    path, db = opened(tmp_path)
    other = sqlite3.connect(path, timeout=.05)
    try:
        db.execute('SAVEPOINT capture')
        C.ensure(db)
        other.execute('BEGIN IMMEDIATE')
        other.rollback()
    finally:
        other.close()
        db.rollback()
        db.close()


def test_missing_guard_is_recreated_and_still_enforces_immutability(tmp_path):
    _, db = opened(tmp_path)
    try:
        db.execute("INSERT INTO learning_source_blobs VALUES ('id','{}')")
        db.execute('DROP TRIGGER learning_source_blobs_delete')
        C.ensure(db)
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute("DELETE FROM learning_source_blobs WHERE sha256='id'")
    finally:
        db.rollback()
        db.close()


def test_rolled_back_initialization_is_rechecked(tmp_path):
    db = sqlite3.connect(tmp_path / 'rollback.db')
    try:
        db.execute('BEGIN')
        C.ensure(db)
        db.rollback()
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_actions'").fetchone() is None
        C.ensure(db)
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_actions'").fetchone()
    finally:
        db.close()


def test_legacy_update_guard_migration_is_not_skipped(tmp_path):
    _, db = opened(tmp_path)
    try:
        db.execute('DROP TRIGGER learning_source_blobs_update')
        db.execute("CREATE TRIGGER learning_source_blobs_update BEFORE UPDATE ON learning_source_blobs BEGIN SELECT RAISE(ABORT,'learning capture immutable'); END")
        C.ensure(db)
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='learning_source_blobs_update'").fetchone()[0]
        assert 'journal_detail_v1(OLD.payload)' in sql
    finally:
        db.close()
