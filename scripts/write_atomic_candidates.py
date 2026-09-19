#!/usr/bin/env python3
from pathlib import Path
import json
import re
import yaml

SRC = Path("/tmp/luffy-atomic-final-candidates.json")
OUT = Path("knowledge/60 Atomic")

def slug(text):
    text = re.sub(r'[\\/:*?"<>|]', "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]

items = json.loads(SRC.read_text())

written = 0

for item in items:
    title = item["title"].strip()
    typ = item.get("type", "lesson").strip()

    folder = OUT / slug(typ)
    folder.mkdir(parents=True, exist_ok=True)

    path = folder / f"{slug(title)}.md"

    data = {
        "type": typ,
        "family": item.get("family") or "general",
        "status": "candidate",
        "confidence": item.get("confidence") or "unverified",
        "claim": item.get("claim"),
        "relations": item.get("relations") or {},
    }

    if item.get("source_titles"):
        data["source_titles"] = item["source_titles"]
    elif item.get("source_title"):
        data["source_titles"] = [item["source_title"]]

    if item.get("source_paths"):
        data["source_paths"] = item["source_paths"]
    elif item.get("source_path"):
        data["source_paths"] = [item["source_path"]]

    if item.get("merged_from"):
        data["merged_from"] = item["merged_from"]

    content = (
        "---\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000)
        + "---\n\n"
        + f"# {title}\n\n"
        + (item.get("claim") or "")
        + "\n"
    )

    path.write_text(content, encoding="utf-8")
    written += 1

print("written:", written)
print("destination:", OUT)
