"""Disposable, timeout-bounded diagnostics child. No network/trading imports."""
from __future__ import annotations

import json
import sys

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
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
