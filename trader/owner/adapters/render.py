"""Human-readable rendering of an OwnerResult. Wording is not the safety contract."""
from __future__ import annotations

from ..contract import OwnerResult, Status


def render(result: OwnerResult) -> str:
    op, st = result.operation or "request", result.status
    why = ", ".join(result.reasons) or "unproven"
    if st == Status.UNAVAILABLE:
        return f"⚠️ {op} not delivered — Luffy kernel unavailable ({why}). Nothing changed."
    if st == Status.ERROR:
        return (f"⚠️ {op}: outcome UNKNOWN — {why}. It may have run or may still run; "
                "retry only the same request ID to resolve its outcome.")
    if st == Status.PENDING:
        return f"⏳ {op} still running — {why}."
    if op in ("status", "health") and st == Status.ACCEPTED:
        d = result.data or {}
        hb = d.get("heartbeat_age_s")
        text = (f"state={d.get('control_state')} open={d.get('open_trades')} "
                f"heartbeat={hb and f'{hb:.0f}s'}")
        sup = d.get("supervisor")
        if op == "health" and sup:
            text += (f"\nsupervisor={sup.get('outcome')} needs_owner={sup.get('needs_owner')} "
                     f"reasons={', '.join(sup.get('reasons') or []) or '—'}")
        return text
    if st in (Status.ACCEPTED, Status.ALREADY_SET):
        if op == "freeze":
            return "🥶 FROZEN — no new entries; managing existing to close."
        if op == "halt":
            return "😴 HALTED."
        if op == "panic":
            return (f"🚨 PANIC accepted — {result.control_state_after} now; "
                    "flattening on the next cycle.")
        if op == "close_trade":
            return "Close queued — next cycle."
        if op == "set_market_type":
            return "Market type recorded — applies at the next kernel start."
    if st == Status.REFUSED and result.control_state_after is None:
        return f"🔒 {op} refused — {why}. Nothing changed."
    if op in ("resume", "unhalt"):
        if st == Status.ACTIVATED:
            return "🙂 ACTIVE — fresh recovery check proved safe."
        if st == Status.ALREADY_SET:
            return "ℹ️ already ACTIVE — nothing changed."
        if "halted_requires_explicit_unhalt" in result.reasons:
            return ("😴 still HALTED — /resume does not release a halt; "
                    "send /unhalt to request a contained recheck.")
        return (f"🔒 still {result.control_state_after}: {st} "
                f"{result.supervisor_outcome or ''} — {why}")
    return f"🔒 {op}: {st} — {why}"
