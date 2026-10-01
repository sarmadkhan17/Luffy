"""Link historical booking receipts to trade_legs / trade_fills — deterministic
evidence only.

Each trade_accounting_bookings receipt was written in the same transaction as
its booking and names its trade, and (where an order existed) the venue order
id and fills observed at the time. Re-deriving legs and fills from a receipt
that still replays is exact. Nothing else is inferred:

- strategy entry identity is never backfilled (the journal trigger refuses it);
  historical trades stay UNKNOWN;
- a receipt that fails integrity replay is skipped and counted;
- exit attribution of backfilled legs is UNKNOWN_HISTORICAL, because whether a
  sibling position shared the symbol at booking time was not recorded;
- fills whose order id matches no recorded Luffy order stay UNATTRIBUTED.

- the market type of every leg and fill is the receipt's own trade snapshot;
  a receipt without a known market type records no fill and links no order.

Dry run by default, and the dry run never touches the supplied database: SQLite
never opens the source (even ``mode=ro`` may create or write ``-shm``). The
database file and its ``-wal`` / ``-journal`` siblings are copied into a
temporary directory with plain file reads, and the copy is used only if every
source file's content hash, mtime, size and the directory's membership of
those names are identical before and after copying — otherwise the run is
refused. SQLite then opens only the copy (recovering committed WAL frames or
rolling back a hot journal there), checks its integrity, and Journal (with its
additive migration) is initialized on it. The copy is discarded, also when the
run fails or is interrupted. ``--apply`` opens the supplied database with
Journal, migrates it additively and commits:

    ./venv/bin/python scripts/backfill_trade_provenance.py --db data/luffy.db
    ./venv/bin/python scripts/backfill_trade_provenance.py --db data/luffy.db --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal  # noqa: E402
from trader.engine import booking, trade_provenance  # noqa: E402


class _DryRun(Exception):
    pass


def backfill(journal: Journal, apply: bool) -> dict:
    report = Counter()
    fills_before = {}
    try:
        with journal._tx() as db:
            fills_before = dict(Counter(r[0] for r in db.execute(
                "SELECT attribution FROM trade_fills")))
            rows = db.execute("SELECT id, trade_id, payload FROM trade_accounting_bookings "
                              "ORDER BY id").fetchall()
            for rid, tid, payload in rows:
                report["receipts"] += 1
                if db.execute("SELECT 1 FROM trade_legs WHERE booking_id=?", (rid,)).fetchone():
                    report["already_linked"] += 1
                    continue
                try:
                    rec = json.loads(payload)
                    booking.replay(rec)
                    if rec.get("trade_id") != tid:
                        raise ValueError("booking_trade_mismatch")
                except Exception as e:                    # noqa: BLE001
                    report["skipped_integrity:" + (str(e) if isinstance(e, ValueError)
                                                   else type(e).__name__)] += 1
                    continue
                leg = trade_provenance.record_booking(
                    db, tid, rec["kind"], rec.get("before"), rec.get("after"),
                    rec.get("evidence") or {}, rid, source="backfill_receipt",
                    recorded_ms=rec.get("observed_ms"))
                if leg:
                    purpose = db.execute("SELECT purpose, order_identity, market_type, "
                                         "terminal_close FROM trade_legs WHERE id=?",
                                         (leg,)).fetchone()
                    report[f"leg:{purpose[0]}:{purpose[1]}"] += 1
                    report[f"leg_market:{purpose[2] or 'unknown'}"] += 1
                    report["terminal_close_legs"] += purpose[3] or 0
            after = Counter(r[0] for r in db.execute("SELECT attribution FROM trade_fills"))
            report.update({f"fills_total:{k}": v for k, v in after.items()})
            report["trades_without_entry_identity"] = db.execute(
                "SELECT COUNT(*) FROM trades WHERE entry_identity_json IS NULL").fetchone()[0]
            if not apply:
                raise _DryRun
    except _DryRun:
        report["dry_run_rolled_back"] = 1
    out = dict(sorted(report.items()))
    out["fills_before"] = fills_before
    return out


class SnapshotRefused(RuntimeError):
    """The source could not be read as one stable set of files."""


_SIBLINGS = ("", "-wal", "-journal", "-shm")


def _state(source: Path) -> dict:
    """What the dry run may not change: membership of the database's sibling
    names in its directory, and hash, mtime and size of each present file
    (``-shm`` included, though never copied)."""
    names = {source.name + s for s in _SIBLINGS}
    present = sorted(p.name for p in source.parent.iterdir() if p.name in names)
    out = {"members": present}
    for name in present:
        f = source.parent / name
        st = f.stat()
        digest = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        out[name] = (digest.hexdigest(), st.st_mtime_ns, st.st_size)
    return out


def _snapshot(source: Path, tmp: Path) -> Path:
    """Copy the database and its WAL / rollback journal into `tmp` by file
    reads only; refuse unless the source was stable across the copy."""
    try:
        before = _state(source)
        if source.name not in before["members"]:
            raise SnapshotRefused("source_database_missing")
        for suffix in _SIBLINGS[:3]:
            name = source.name + suffix
            if name not in before["members"]:
                continue
            digest = hashlib.sha256()
            with open(source.parent / name, "rb") as src, open(tmp / name, "wb") as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    digest.update(chunk)
                    dst.write(chunk)
            if digest.hexdigest() != before[name][0]:
                raise SnapshotRefused(f"source_changed_during_copy:{name}")
        after = _state(source)
    except OSError as e:
        raise SnapshotRefused(f"source_unreadable:{type(e).__name__}") from e
    if after != before:
        raise SnapshotRefused("source_changed_during_copy")
    return tmp / source.name


def _check_copy(copy: Path) -> None:
    con = sqlite3.connect(copy)
    try:
        result = [r[0] for r in con.execute("PRAGMA integrity_check").fetchall()]
    except sqlite3.DatabaseError as e:
        raise SnapshotRefused(f"snapshot_unreadable:{e}") from e
    finally:
        con.close()
    if result != ["ok"]:
        raise SnapshotRefused("snapshot_integrity_check_failed:" + "; ".join(result[:5]))


def dry_run(db_path: str | Path) -> dict:
    """The backfill against a temporary file snapshot of `db_path`; SQLite
    never opens the source."""
    source = Path(db_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))
    tmp = Path(tempfile.mkdtemp(prefix="luffy-provenance-dry-run-"))
    try:
        copy = _snapshot(source, tmp)
        copied = sorted(p.name for p in tmp.iterdir())
        _check_copy(copy)
        report = backfill(Journal(copy), apply=False)
        report["source"] = {"path": str(source),
                            "opened": "file snapshot copy; sqlite opened only the copy",
                            "files_copied": copied,
                            "journal_initialized_on": "temporary copy, discarded"}
        return report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(db_path: str | Path, apply: bool) -> dict:
    if apply:
        return backfill(Journal(db_path), apply=True)
    return dry_run(db_path)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    print(json.dumps(run(args.db, args.apply), indent=2))


if __name__ == "__main__":
    main()
