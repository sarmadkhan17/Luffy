#!/usr/bin/env python3

from pathlib import Path
import json

CANDIDATES = Path("/tmp/luffy-atomic-candidates.json")
SUGGESTIONS = Path("/tmp/luffy-atomic-merge-suggestions.json")
PLAN = Path("/tmp/luffy-approved-merges.json")
OUT = Path("/tmp/luffy-atomic-final-candidates.json")

items = json.loads(CANDIDATES.read_text())
groups = json.loads(SUGGESTIONS.read_text()).get("groups", [])
plan = json.loads(PLAN.read_text())

approved = {
    i - 1 for i in plan["approved_groups"]
}

# Exact title -> candidate index
by_title = {}
for i, item in enumerate(items):
    by_title.setdefault(item["title"], []).append(i)

# Union-find so overlapping approved groups are handled safely.
parent = list(range(len(items)))

def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x

def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[rb] = ra

canonical_for_root = {}

for group_index in approved:
    g = groups[group_index]

    member_indices = []
    for title in g["members"]:
        matches = by_title.get(title, [])
        if len(matches) != 1:
            print(
                f"[WARN] group {group_index + 1}: "
                f"title {title!r} matched {len(matches)} candidates"
            )
            continue
        member_indices.append(matches[0])

    if len(member_indices) < 2:
        continue

    first = member_indices[0]
    for idx in member_indices[1:]:
        union(first, idx)

    canonical_for_root[first] = g["canonical_title"]

# Normalize canonical mapping after all unions.
normalized_canonical = {}
for old_root, title in canonical_for_root.items():
    normalized_canonical[find(old_root)] = title

clusters = {}
for i in range(len(items)):
    clusters.setdefault(find(i), []).append(i)

result = []

for root, indices in clusters.items():
    if len(indices) == 1:
        result.append(items[indices[0]])
        continue

    members = [items[i] for i in indices]

    # Preserve first candidate as the base, then combine provenance/relations.
    merged = dict(members[0])
    merged["title"] = normalized_canonical.get(root, merged["title"])

    # Keep all provenance.
    source_titles = []
    source_paths = []

    combined_relations = {}

    for m in members:
        st = m.get("source_title")
        sp = m.get("source_path")

        if st and st not in source_titles:
            source_titles.append(st)
        if sp and sp not in source_paths:
            source_paths.append(sp)

        for relation, values in (m.get("relations") or {}).items():
            bucket = combined_relations.setdefault(relation, [])
            for value in values or []:
                if value not in bucket:
                    bucket.append(value)

    merged["relations"] = combined_relations
    merged["source_titles"] = source_titles
    merged["source_paths"] = source_paths

    # Retain compatibility for later tooling.
    if source_titles:
        merged["source_title"] = source_titles[0]
    if source_paths:
        merged["source_path"] = source_paths[0]

    merged["merged_from"] = [m["title"] for m in members]

    result.append(merged)

OUT.write_text(
    json.dumps(result, indent=2, ensure_ascii=False),
    encoding="utf-8",
)

print("before:", len(items))
print("after approved merges:", len(result))
print("concepts removed by merges:", len(items) - len(result))
print("written:", OUT)
