"""Reads and writes the notes file.

Format version 2, which save() writes:

    {"version": 2, "notes": [{"id": "n-...", "title": "...", "body": "...", "tags": ["a"], "created": "2026-10-30"}]}

Format version 1, which load() still reads, is a JSON list of objects:

    [{"title": "...", "body": "...", "tags": "work, Ideas", "created": "30/10/2026"}]

"tags" is one string of comma-separated tags, and "created" is DD/MM/YYYY.
"""

import hashlib
import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

from .model import Note


def normal_tags(tags):
    seen = []
    for tag in tags:
        tag = tag.strip().lower()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def note_id(note):
    digest = hashlib.sha256(f"{note.created.isoformat()}\n{note.title}".encode("utf-8")).hexdigest()
    return "n-" + digest[:10]


def from_v1(item):
    try:
        created = datetime.strptime(item["created"], "%d/%m/%Y").date()
    except ValueError as e:
        raise ValueError(f"{e} in note {item.get('title')!r}") from None
    return Note(item["title"], item["body"], created, normal_tags(item["tags"].split(",")))


def version_of(data):
    return 1 if isinstance(data, list) else data.get("version")


def parse(data):
    version = version_of(data)
    if version == 1:
        return [from_v1(item) for item in data]
    if version == 2:
        return [Note(n["title"], n["body"], date.fromisoformat(n["created"]), normal_tags(n["tags"])) for n in data["notes"]]
    raise ValueError(f"unsupported notes version: {version}")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load(path):
    path = Path(path)
    if not path.exists():
        return []
    return parse(read(path))


def to_v2(notes):
    return {"version": 2, "notes": [
        {"id": note_id(n), "title": n.title, "body": n.body, "tags": normal_tags(n.tags), "created": n.created.isoformat()}
        for n in notes]}


def write_atomically(path, text):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def save(path, notes):
    write_atomically(path, json.dumps(to_v2(notes), indent=2, ensure_ascii=False) + "\n")
