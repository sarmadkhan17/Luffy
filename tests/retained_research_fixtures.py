"""Explicit offline reproduction for tests that tamper with a working ledger."""
from contextlib import closing
from pathlib import Path
from trader.persistence.storage_cleanup import completed_reader


def restore_ledger(bank, experiment_id, destination):
    path=Path(destination)
    path.parent.mkdir(parents=True,exist_ok=True)
    with closing(completed_reader(bank,experiment_id)) as db:
        path.write_bytes(db.serialize())
    return path
