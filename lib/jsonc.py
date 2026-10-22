"""Edits JSON and JSONC files in place without touching anything else.

Parses JSON with comments and trailing commas (the format OpenCode and Pi
configs may use), keeping the position of every object member, so a caller
can add or remove one member and leave every other byte as it was. Uses only
the standard library.
"""

import json
import re


class JsoncError(ValueError):
    pass


class Member:
    def __init__(self, key, start, value, end, comma_end):
        self.key = key  # the decoded key
        self.start = start  # offset of the key's opening quote
        self.value = value  # the parsed value: Obj, list, or a plain value
        self.end = end  # offset just past the value
        self.comma_end = comma_end  # offset just past a following comma, or None


class Obj:
    def __init__(self, open_pos, close_pos, members):
        self.open = open_pos  # offset of "{"
        self.close = close_pos  # offset of "}"
        self.members = members

    def get(self, key):
        found = [m for m in self.members if m.key == key]
        if len(found) > 1:
            raise JsoncError(f"the key {key!r} appears {len(found)} times in one object")
        return found[0] if found else None


_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")


class _Parser:
    def __init__(self, text):
        self.text = text
        self.pos = 0

    def fail(self, message):
        line = self.text.count("\n", 0, self.pos) + 1
        raise JsoncError(f"{message} at line {line}")

    def skip(self):
        text = self.text
        while self.pos < len(text):
            c = text[self.pos]
            if c in " \t\r\n":
                self.pos += 1
            elif text.startswith("//", self.pos):
                end = text.find("\n", self.pos)
                self.pos = len(text) if end < 0 else end + 1
            elif text.startswith("/*", self.pos):
                end = text.find("*/", self.pos + 2)
                if end < 0:
                    self.fail("unterminated /* comment")
                self.pos = end + 2
            else:
                break

    def peek(self):
        self.skip()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def string(self):
        start = self.pos
        self.pos += 1
        while True:
            if self.pos >= len(self.text):
                self.fail("unterminated string")
            c = self.text[self.pos]
            if c == "\\":
                self.pos += 2
            elif c == '"':
                self.pos += 1
                try:
                    return json.loads(self.text[start : self.pos])
                except ValueError as e:
                    self.fail(f"invalid string ({e.msg})")
            elif c < " ":
                self.fail("control character inside a string")
            else:
                self.pos += 1

    def value(self):
        c = self.peek()
        if c == "{":
            return self.obj()
        if c == "[":
            return self.array()
        if c == '"':
            return self.string()
        for word, result in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(word, self.pos):
                self.pos += len(word)
                return result
        match = _NUMBER.match(self.text, self.pos)
        if match:
            self.pos = match.end()
            return json.loads(match.group())
        self.fail("expected a value")

    def array(self):
        self.pos += 1
        items = []
        while True:
            if self.peek() == "]":
                self.pos += 1
                return items
            items.append(self.value())
            if self.peek() == ",":
                self.pos += 1
            elif self.peek() != "]":
                self.fail("expected , or ]")

    def obj(self):
        open_pos = self.pos
        self.pos += 1
        members = []
        while True:
            c = self.peek()
            if c == "}":
                close = self.pos
                self.pos += 1
                return Obj(open_pos, close, members)
            if c != '"':
                self.fail("expected a key")
            start = self.pos
            key = self.string()
            if self.peek() != ":":
                self.fail("expected :")
            self.pos += 1
            value = self.value()
            end = self.pos
            comma_end = None
            if self.peek() == ",":
                self.pos += 1
                comma_end = self.pos
            elif self.peek() != "}":
                self.fail("expected , or }")
            members.append(Member(key, start, value, end, comma_end))


def parse(text):
    """Returns the top-level Obj of a JSON or JSONC document.

    A leading byte-order mark is skipped; offsets still count it.
    """
    parser = _Parser(text)
    if text.startswith("\ufeff"):
        parser.pos = 1
    try:
        if parser.peek() != "{":
            parser.fail("the file must hold one JSON object")
        root = parser.obj()
        if parser.peek() != "":
            parser.fail("unexpected text after the object")
    except RecursionError:
        raise JsoncError("the file nests too deeply to read") from None
    return root


def plain(value):
    """Converts a parsed value to plain dicts and lists."""
    if isinstance(value, Obj):
        return {m.key: plain(m.value) for m in value.members}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def _indent_of(text, pos):
    line_start = text.rfind("\n", 0, pos) + 1
    return re.match(r"[ \t]*", text[line_start:].lstrip("\ufeff")).group()


def insert_first(text, obj, key, value):
    """Adds `key: value` as the first member of `obj`.

    Returns the new text and the exact text inserted, which remove_inserted
    takes out again. Multi-line objects get the member on its own lines at
    the members' indentation; single-line objects get it on the same line.
    """
    if "\n" in text[obj.open : obj.close]:
        if obj.members:
            indent = _indent_of(text, obj.members[0].start)
        else:
            indent = _indent_of(text, obj.open) + "  "
        newline = "\r\n" if "\r\n" in text else "\n"
        rendered = json.dumps(value, indent=2).replace("\n", newline + indent)
        inserted = f"{newline}{indent}{json.dumps(key)}: {rendered}" + ("," if obj.members else "")
    else:
        inserted = f"{json.dumps(key)}: {json.dumps(value)}" + (", " if obj.members else "")
    position = obj.open + 1
    return text[:position] + inserted + text[position:], inserted


def remove_inserted(text, inserted):
    """Takes out text that insert_first added, if it is there exactly once."""
    if text.count(inserted) != 1:
        return None
    return text.replace(inserted, "", 1)


def remove(text, obj, key):
    """Removes member `key` from `obj` with its comma, and its line when it had one to itself."""
    member = obj.get(key)
    if member is None:
        raise JsoncError(f"no member {key!r} to remove")
    index = obj.members.index(member)
    if member.comma_end is None and index > 0:
        # The last member: drop only the comma before it, keeping any comments
        # between the two, then remove the member itself as below.
        comma = obj.members[index - 1].comma_end - 1
        text = text[:comma] + text[comma + 1 :]
        member = Member(member.key, member.start - 1, member.value, member.end - 1, None)
    start = member.start
    end = member.comma_end if member.comma_end is not None else member.end
    line_start = text.rfind("\n", 0, start) + 1
    newline = text.find("\n", end)
    line_end = len(text) if newline < 0 else newline + 1
    if text[line_start:start].strip() == "" and text[end:line_end].strip() == "":
        start, end = line_start, line_end
    elif member.comma_end is not None:
        # Sharing a line with the next member: take the spaces after the comma too.
        while end < len(text) and text[end] in " \t":
            end += 1
    return text[:start] + text[end:]
