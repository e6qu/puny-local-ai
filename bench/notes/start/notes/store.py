"""Reads and writes the notes file.

Format version 1 is a JSON list of objects:

    [{"title": "...", "body": "...", "tags": "work, Ideas", "created": "30/10/2026"}]

"tags" is one string of comma-separated tags, and "created" is DD/MM/YYYY.
"""

import json
from datetime import datetime
from pathlib import Path

from .model import Note


def load(path):
    path = Path(path)
    if not path.exists():
        return []
    notes = []
    for item in json.loads(path.read_text(encoding="utf-8")):
        tags = [t.strip() for t in item["tags"].split(",") if t.strip()]
        created = datetime.strptime(item["created"], "%d/%m/%Y").date()
        notes.append(Note(item["title"], item["body"], created, tags))
    return notes


def save(path, notes):
    items = [{"title": n.title, "body": n.body, "tags": ", ".join(n.tags), "created": n.created.strftime("%d/%m/%Y")}
             for n in notes]
    Path(path).write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
