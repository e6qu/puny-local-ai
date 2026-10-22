"""Command-line interface: todo [--file PATH] add TEXT | list [--pending] | done ID"""

import argparse
import sys

from . import store


def main(argv=None):
    parser = argparse.ArgumentParser(prog="todo")
    parser.add_argument("--file", default="todo.json")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("text")
    listing = commands.add_parser("list")
    listing.add_argument("--pending", action="store_true")
    done = commands.add_parser("done")
    done.add_argument("id", type=int)
    args = parser.parse_args(argv)

    items = store.load(args.file)
    if args.command == "add":
        item = {"id": max((i["id"] for i in items), default=0) + 1, "text": args.text, "done": False}
        items.append(item)
        store.save(args.file, items)
        print(f"added {item['id']}")
    elif args.command == "list":
        for item in items:
            if args.pending and item.get("done", False):
                continue
            mark = "x" if item.get("done", False) else " "
            print(f"{item['id']}. [{mark}] {item['text']}")
    elif args.command == "done":
        for item in items:
            if item["id"] == args.id:
                item["done"] = True
                store.save(args.file, items)
                print(f"done {args.id}")
                return 0
        print(f"no item {args.id}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
