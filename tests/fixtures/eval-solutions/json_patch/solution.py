"""Reference solution: RFC 6902 on a deep copy, with RFC 6901 pointers."""

import copy
import re

INDEX = re.compile(r"0|[1-9][0-9]*")


class PatchError(ValueError):
    def __init__(self, index, message):
        super().__init__(f"operation {index}: {message}")
        self.index = index


def tokens(pointer):
    if pointer == "":
        return []
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError(f"bad pointer {pointer!r}")
    out = []
    for raw in pointer[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            raise ValueError(f"bad escape in {pointer!r}")
        out.append(raw.replace("~1", "/").replace("~0", "~"))
    return out


def child(container, token, adding=False):
    """The key or index `token` names in `container`."""
    if isinstance(container, dict):
        return token
    if isinstance(container, list):
        if adding and token == "-":
            return len(container)
        if not INDEX.fullmatch(token):
            raise ValueError(f"bad array index {token!r}")
        index = int(token)
        if index > len(container) or (index == len(container) and not adding):
            raise ValueError(f"index {index} is out of range")
        return index
    raise ValueError("a scalar has no children")


def parent_of(doc, path):
    parts = tokens(path)
    target = doc
    for token in parts[:-1]:
        target = get(target, token)
    return target, parts[-1] if parts else None


def get(container, token):
    key = child(container, token)
    if isinstance(container, dict):
        if key not in container:
            raise ValueError(f"no member {key!r}")
    return container[key]


def lookup(doc, path):
    target = doc
    for token in tokens(path):
        target = get(target, token)
    return target


def add(doc, path, value):
    if path == "":
        return value
    parent, token = parent_of(doc, path)
    key = child(parent, token, adding=True)
    if isinstance(parent, list):
        parent.insert(key, value)
    else:
        parent[key] = value
    return doc


def remove(doc, path):
    if path == "":
        raise ValueError("cannot remove the whole document")
    parent, token = parent_of(doc, path)
    key = child(parent, token)
    if isinstance(parent, dict) and key not in parent:
        raise ValueError(f"no member {key!r}")
    return parent.pop(key)


def equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def apply_patch(document, operations):
    doc = copy.deepcopy(document)
    for index, operation in enumerate(operations):
        try:
            op = operation["op"]
            path = operation["path"]
            if op == "add":
                doc = add(doc, path, copy.deepcopy(operation["value"]))
            elif op == "remove":
                remove(doc, path)
            elif op == "replace":
                lookup(doc, path)
                if path == "":
                    doc = copy.deepcopy(operation["value"])
                else:
                    remove(doc, path)
                    doc = add(doc, path, copy.deepcopy(operation["value"]))
            elif op == "move":
                source = operation["from"]
                if path != source and path.startswith(source + "/"):
                    raise ValueError("cannot move a location into its own child")
                if path != source:
                    value = remove(doc, source) if source else doc
                    doc = add(doc, path, value)
                else:
                    lookup(doc, source)
            elif op == "copy":
                doc = add(doc, path, copy.deepcopy(lookup(doc, operation["from"])))
            elif op == "test":
                if not equal(lookup(doc, path), operation["value"]):
                    raise ValueError("test failed")
            else:
                raise ValueError(f"unknown op {op!r}")
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise PatchError(index, str(e)) from None
    return doc
