from pathlib import Path
import sys
import json
import yaml

vault = Path("knowledge")

def load_notes():
    notes = []

    for note in vault.rglob("*.md"):
        text = note.read_text(errors="ignore")
        if not text.startswith("---"):
            continue

        parts = text.split("---", 2)
        if len(parts) < 3:
            continue

        data = yaml.safe_load(parts[1]) or {}

        if data.get("status") == "candidate":
            continue

        body = parts[2]

        title = next(
            (line[2:].strip() for line in body.splitlines() if line.startswith("# ")),
            None,
        )

        if title:
            notes.append((title, data, note))

    return notes


notes = load_notes()

# Reverse lookup:
# memory_lookup.py --reverse works_in "Ranging Regime"
if len(sys.argv) == 4 and sys.argv[1] == "--reverse":
    relation = sys.argv[2]
    target = sys.argv[3].strip().lower()

    matches = []

    for title, data, note in notes:
        values = data.get("relations", {}).get(relation, [])

        if any(str(v).strip().lower() == target for v in values):
            matches.append(title)

    print(json.dumps({
        "relation": relation,
        "target": sys.argv[3],
        "concepts": matches,
    }))
    raise SystemExit(0)


if len(sys.argv) not in (2, 3):
    print('usage: memory_lookup.py "<concept>" [relation]')
    print('       memory_lookup.py --reverse <relation> "<target>"')
    raise SystemExit(1)

concept = sys.argv[1].strip().lower()
relation = sys.argv[2].strip() if len(sys.argv) == 3 else None

matches = [
    (title, data, note)
    for title, data, note in notes
    if title.lower() == concept
]

if not matches:
    print(f"Concept not found: {sys.argv[1]}")
    raise SystemExit(2)

if len(matches) > 1:
    print("Ambiguous concept:")
    for _, _, note in matches:
        print(note)
    raise SystemExit(3)

title, data, note = matches[0]
relations = data.get("relations", {})

if relation:
    values = relations.get(relation, [])
    if not values:
        print(f"No '{relation}' relation recorded for {title}")
        raise SystemExit(4)

    print(json.dumps({
        "concept": title,
        "relation": relation,
        "values": values,
    }))
else:
    print(json.dumps({
        "concept": title,
        "claim": data.get("claim"),
        "relations": relations,
    }))
