"""notes [--file PATH] add TITLE BODY [--tag TAG]... | list [--tag TAG] | show TITLE | migrate [--dry-run]"""

import argparse
import sys
from datetime import date
from pathlib import Path

from . import store
from .model import Note


def migrate(path, dry_run):
    path = Path(path)
    backup = Path(str(path) + ".v1.bak")
    data = store.read(path)
    if store.version_of(data) == 2:
        print("already version 2")
        return 0
    try:
        notes = store.parse(data)
    except (ValueError, KeyError, AttributeError) as e:
        print(f"cannot migrate: {e}", file=sys.stderr)
        return 1
    if dry_run:
        print(f"would migrate {len(notes)} notes")
        return 0
    if backup.exists():
        print(f"{backup} already exists", file=sys.stderr)
        return 1
    backup.write_bytes(path.read_bytes())
    store.save(path, notes)
    print(f"migrated {len(notes)} notes")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="notes")
    parser.add_argument("--file", default="notes.json")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("title")
    add.add_argument("body")
    add.add_argument("--tag", action="append", default=[])
    listing = commands.add_parser("list")
    listing.add_argument("--tag")
    show = commands.add_parser("show")
    show.add_argument("title")
    migration = commands.add_parser("migrate")
    migration.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "migrate":
        return migrate(args.file, args.dry_run)
    notes = store.load(args.file)
    if args.command == "add":
        notes.append(Note(args.title, args.body, date.today(), store.normal_tags(args.tag)))
        store.save(args.file, notes)
        print(f"added {args.title}")
    elif args.command == "list":
        for note in notes:
            if args.tag is None or args.tag in note.tags:
                print(f"{note.created.isoformat()}  {note.title}  [{', '.join(note.tags)}]")
    elif args.command == "show":
        for note in notes:
            if note.title == args.title:
                print(note.title)
                print(note.body)
                return 0
        print(f"no note {args.title}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
