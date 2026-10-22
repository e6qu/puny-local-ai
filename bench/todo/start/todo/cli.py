"""Command-line interface: todo [--file PATH] add TEXT | list"""

import argparse
import sys

from . import store


def main(argv=None):
    parser = argparse.ArgumentParser(prog="todo")
    parser.add_argument("--file", default="todo.json")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("text")
    commands.add_parser("list")
    args = parser.parse_args(argv)

    items = store.load(args.file)
    if args.command == "add":
        item = {"id": max((i["id"] for i in items), default=0) + 1, "text": args.text}
        items.append(item)
        store.save(args.file, items)
        print(f"added {item['id']}")
    elif args.command == "list":
        for item in items:
            print(f"{item['id']}. {item['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
