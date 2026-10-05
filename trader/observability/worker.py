"""Disposable, timeout-bounded diagnostics child. No network/trading imports."""
from __future__ import annotations

import json
import sys
import sqlite3

from .store import Store
from .collector_health import code_hash


def main():
    store = None
    try:
        job = json.load(sys.stdin)
        store = Store(job["path"], job["settings"])
        proof=store.write(job["event"])
        print(json.dumps({"ok": True,"proof":proof,"code_hash":code_hash()}))
        return 0
    except Exception as exc:
        # Never echo arbitrary exception messages, input or credentials.
        error = {"ok": False, "error_type": type(exc).__name__}
        if isinstance(exc, sqlite3.Error):
            error['sqlite_errorcode'] = getattr(exc, 'sqlite_errorcode', None)
            error['sqlite_errorname'] = getattr(exc, 'sqlite_errorname', None)
        print(json.dumps(error))
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
