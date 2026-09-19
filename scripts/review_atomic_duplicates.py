#!/usr/bin/env python3
from pathlib import Path
import json, os, urllib.request

SRC = Path("/tmp/luffy-atomic-candidates.json")
OUT = Path("/tmp/luffy-atomic-merge-suggestions.json")

items = json.loads(SRC.read_text())

SYSTEM = """
Review candidate trading-memory concepts for semantic duplicates.

Only suggest MERGE when:
1. type is the same or clearly compatible,
2. family is the same or clearly compatible,
3. the underlying claim is materially the same.

Do NOT merge:
- strategy vs its invalidation rule,
- strategy vs retired/live-status finding,
- similar rules belonging to different strategy families,
- concepts that merely share vocabulary.

Return ONLY JSON:
{
  "groups": [
    {
      "canonical_title": "...",
      "members": ["exact candidate title 1", "exact candidate title 2"],
      "reason": "..."
    }
  ]
}
"""

key = os.environ.get("DEEPSEEK_API_KEY")
if not key:
    raise SystemExit("DEEPSEEK_API_KEY is not set")

# Give the reviewer compact evidence only.
compact = [
    {
        "title": x["title"],
        "type": x.get("type"),
        "family": x.get("family"),
        "claim": x.get("claim"),
    }
    for x in items
]

payload = {
    "model": "deepseek-chat",
    "temperature": 0,
    "response_format": {"type": "json_object"},
    "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
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

with urllib.request.urlopen(req, timeout=180) as r:
    response = json.loads(r.read())

result = json.loads(response["choices"][0]["message"]["content"])
OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))

print("suggested merge groups:", len(result.get("groups", [])))
print("written:", OUT)
