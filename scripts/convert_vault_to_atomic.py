#!/usr/bin/env python3

from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import urllib.request
import yaml

ROOT = Path("knowledge")
OUT = ROOT / "60 Atomic"

ALLOWED_RELATIONS = {
    "supports",
    "contradicts",
    "works_in",
    "fails_in",
    "causes",
    "precedes",
    "derived_from",
}

SYSTEM = """
You convert trading knowledge into small atomic memory concepts.

RULES:
- One concept = one specific reusable claim/mechanism/lesson.
- Do not invent facts not present in the source.
- Preserve uncertainty.
- Prefer 0-5 useful concepts rather than creating noise.
- A strategy may itself be one atomic concept.
- A postmortem should yield reusable lessons/failure mechanisms, not trade-by-trade narration.
- Agent/company notes should yield responsibilities or invariants only when useful.
- Daily notes should yield only durable lessons, never ordinary events.
- Relations may ONLY use:
  supports, contradicts, works_in, fails_in, causes, precedes, derived_from
- Relation targets must be short concept names.
- status must be "candidate".
- confidence must be one of: unverified, observed, measured.
- derived_from should normally include the source note title.

Return ONLY valid JSON:
{
  "concepts": [
    {
      "title": "...",
      "type": "market-mechanism|strategy|regime|lesson|risk-rule|agent-responsibility|research-finding",
      "family": "...",
      "status": "candidate",
      "confidence": "unverified|observed|measured",
      "claim": "...",
      "relations": {
        "supports": [],
        "contradicts": [],
        "works_in": [],
        "fails_in": [],
        "causes": [],
        "precedes": [],
        "derived_from": []
      }
    }
  ]
}
"""


def slug(text):
    text = re.sub(r'[\\/:*?"<>|]', "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


def title_from_note(text, path):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def load_env():
    env = Path(".env")
    if not env.exists():
        return
    for raw in env.read_text(errors="ignore").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def ask_deepseek(source_title, source_path, content):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    prompt = f"""
SOURCE TITLE: {source_title}
SOURCE PATH: {source_path}

SOURCE CONTENT:
{content}
"""

    payload = {
        "model": "deepseek-chat",
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }

    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=120) as r:
        response = json.loads(r.read())

    text = response["choices"][0]["message"]["content"]
    return json.loads(text)


def clean_relations(relations, source_title):
    result = {}
    relations = relations or {}

    for rel in ALLOWED_RELATIONS:
        vals = relations.get(rel, [])
        if isinstance(vals, str):
            vals = [vals]
        vals = [str(v).strip() for v in vals if str(v).strip()]
        result[rel] = list(dict.fromkeys(vals))

    if source_title not in result["derived_from"]:
        result["derived_from"].append(source_title)

    return result


def render_note(concept, source_title, source_path):
    relations = clean_relations(concept.get("relations"), source_title)

    frontmatter = {
        "type": concept.get("type", "lesson"),
        "family": concept.get("family") or "general",
        "status": "candidate",
        "confidence": concept.get("confidence", "unverified"),
        "claim": concept["claim"].strip(),
        "source_note": source_title,
        "source_path": str(source_path),
        "relations": relations,
    }

    return (
        "---\n"
        + yaml.safe_dump(
            frontmatter,
            sort_keys=False,
            allow_unicode=True,
            width=1000,
        )
        + "---\n\n"
        + f"# {concept['title'].strip()}\n\n"
        + concept["claim"].strip()
        + "\n"
    )


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write atomic notes; default is dry-run")
    ap.add_argument("--include-daily", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    load_env()

    notes = sorted(ROOT.rglob("*.md"))
    notes = [
        p for p in notes
        if "60 Atomic" not in p.parts
        and "00 Company" not in p.parts
        and "Mechanisms" not in p.parts
        and "States" not in p.parts
        and not (p.parent == ROOT / "10 Theories")
        and p.name != "MOC.md"
        and p.name != "Agent Ledger.md"
        and p.name != "Regime Playbook.md"
        and (args.include_daily or "50 Daily" not in p.parts)
    ]

    if args.limit:
        notes = notes[:args.limit]

    print(f"Source notes: {len(notes)}")
    print("Mode:", "WRITE" if args.apply else "DRY RUN")

    produced = 0
    failures = 0
    seen_titles = set()
    candidate_records = []
    candidate_records = []
    seen_concepts = set()

    for i, path in enumerate(notes, 1):
        try:
            text = path.read_text(errors="ignore")
            source_title = title_from_note(text, path)

            result = ask_deepseek(source_title, path, text)
            concepts = result.get("concepts", [])

            if "30 Postmortems" in path.parts:
                concepts = concepts[:2]

            print(f"[{i}/{len(notes)}] {path}: {len(concepts)} concept(s)")

            for concept in concepts:
                title = str(concept.get("title", "")).strip()
                claim = str(concept.get("claim", "")).strip()

                if not title or not claim:
                    continue

                key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
                key = re.sub(r"\b(minimum|requires|required|rule|bar|threshold)\b", "", key)
                key = re.sub(r"\s+", " ", key).strip()

                if key in seen_concepts:
                    continue

                seen_concepts.add(key)
                produced += 1

                candidate_records.append({
                    "title": title,
                    "claim": claim,
                    "type": concept.get("type"),
                    "family": concept.get("family"),
                    "confidence": concept.get("confidence"),
                    "relations": concept.get("relations", {}),
                    "source_title": source_title,
                    "source_path": str(path),
                })

                if not args.apply:
                    print(f"    -> {title}")
                    continue

                category = str(concept.get("type", "lesson")).strip()
                out_dir = OUT / slug(category)
                out_dir.mkdir(parents=True, exist_ok=True)

                out = out_dir / f"{slug(title)}.md"

                # Do not silently overwrite a concept from another source.
                if out.exists():
                    out = out_dir / f"{slug(title)} [{fingerprint(path)}].md"

                out.write_text(
                    render_note(concept, source_title, path),
                    encoding="utf-8",
                )

        except Exception as e:
            failures += 1
            print(f"[ERROR] {path}: {e}")

    Path("/tmp/luffy-atomic-candidates.json").write_text(
        json.dumps(candidate_records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print()
    print(f"Candidates saved: /tmp/luffy-atomic-candidates.json")
    print(f"Concepts produced: {produced}")
    print(f"Failures: {failures}")

    if not args.apply:
        print("Nothing written. Re-run with --apply after reviewing.")


if __name__ == "__main__":
    main()
