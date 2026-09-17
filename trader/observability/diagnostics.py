"""Bounded evaluation receipts. No exception messages, locals or prompts."""
from pathlib import Path
import traceback


class Receipts:
    def __init__(self, enabled=False, limit=256):
        self.enabled, self.limit = enabled, limit
        self.items = []
        self.omitted = 0

    def add(self, component, component_id, reason, exc=None):
        if not self.enabled:
            return
        if len(self.items) >= self.limit:
            self.omitted += 1
            return
        item = {"component": component, "component_id": str(component_id)[:160],
                "reason": reason}
        if reason == "returned_none":
            item["input_status"] = "not_reported"
            item["meaning"] = "No output; insufficient inputs and false predicates are not distinguished."
        if exc is not None:
            item["error_type"] = type(exc).__name__
            item["frames"] = [{"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
                              for f in traceback.extract_tb(exc.__traceback__)[-8:]]
        self.items.append(item)
