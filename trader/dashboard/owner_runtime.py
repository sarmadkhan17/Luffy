"""Read-only process evidence for owner displays; never creates a lock/record."""
import fcntl
from pathlib import Path

from trader import runtime_identity as identity


def kernel_process(root: Path) -> dict:
    source = "data/kernel.lock, data/kernel_instance.json and /proc start ticks"
    result = {"state": "UNKNOWN", "heartbeat_instance_id": None, "source": source}
    try:
        # The Kernel holds this lock for its lifetime. Probe only an existing
        # inode; the owner reader must not manufacture an identity artifact.
        with (root / "data/kernel.lock").open("r") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                record = identity._read_record(root / "data/kernel_instance.json")
                if (record and record["pid"] > 0 and record["start_ticks"] > 0
                        and identity.proc_start_ticks(record["pid"]) == record["start_ticks"]):
                    result.update(state="RUNNING",
                                  heartbeat_instance_id=record.get("heartbeat_instance_id"))
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)
                # Pre-lock legacy processes cannot be attributed to this root.
                if not identity.legacy_kernel_pids():
                    result["state"] = "STOPPED"
    except OSError:
        pass
    return result
