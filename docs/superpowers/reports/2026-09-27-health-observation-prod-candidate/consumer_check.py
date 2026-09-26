"""Validate candidate health rows with the TESTED world-branch consumers.

usage: python consumer_check.py <world-tree (git archive of 1eb00e1)> <dump.json>
"""
import json
import sys

world, dump = sys.argv[1], sys.argv[2]
sys.path.insert(0, world)
import trader  # noqa: E402
assert trader.__file__.startswith(world)
from trader.strategy import health_observation as ho  # noqa: E402
from trader.cognition import research_plan as rp  # noqa: E402
from trader.cognition import research_question as rq  # noqa: E402
from trader.cognition import research_unreadable_question as ruq  # noqa: E402

rows, i = [], 0
for r in json.load(open(dump))["results"]:
    for e in r["events"]:
        if e["kind"] in (ho.KIND_SPEC, ho.KIND_SWEEP):
            i += 1
            rows.append({"id": i, "ts": f"2026-09-27T00:{i // 60:02d}:"
                         f"{i % 60:02d}+00:00", "kind": e["kind"],
                         "subject": e["subject"],
                         "detail": json.dumps(e["detail"])})
bad, variants = [], {}
for row in rows:
    rec, why = ho._decode(row)
    if why:
        bad.append((row["id"], "decode", why))
        continue
    if row["kind"] == ho.KIND_SPEC:
        try:
            v = rp._observation_variant(rec, "x")
            variants[v] = variants.get(v, 0) + 1
        except Exception as e:
            bad.append((row["id"], "plan_variant", str(e)))
        o, why = rq._observation(row, rec)
        if o is None:
            bad.append((row["id"], "question_observation", why))
        if rec["verdict"] in rq.UNREADABLE and ruq._reasons(rec) is None:
            bad.append((row["id"], "unreadable_reasons"))
    else:
        if not rp._sweep_ok(rec):
            bad.append((row["id"], "plan_sweep_ok"))
        if rq._sweep(rec) is None:
            bad.append((row["id"], "question_sweep"))
h = ho.history(rows)
rq.derive(rows)
ruq.derive(rows)
print("rows", len(rows), "variants", variants, "invalid", h["invalid_records"],
      "sweeps_without_record", h["sweeps_without_record"], "rejections",
      bad or "none")
