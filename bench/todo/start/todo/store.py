"""Reads and writes the todo list as a JSON array of items."""

import json
from pathlib import Path


def load(path):
    path = Path(path)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save(path, items):
    Path(path).write_text(json.dumps(items, indent=2) + "\n")
