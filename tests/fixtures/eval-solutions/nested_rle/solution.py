"""Reference solution: one parse into a tree with lengths, without recursion."""

import re

TOKEN = re.compile(r"[a-z]+|[1-9][0-9]{0,2}\[|\]")


def parse(text):
    """A list of items: strings, or [count, items, length]; plus the total length."""
    root = []
    stack = [(None, root)]
    pos = 0
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"cannot read {text!r} at {pos}")
        token = m.group()
        pos = m.end()
        if token == "]":
            if len(stack) == 1:
                raise ValueError(f"unmatched ] at {pos - 1}")
            count, items = stack.pop()
            stack[-1][1].append([count, items, count * total(items)])
        elif token.endswith("["):
            stack.append((int(token[:-1]), []))
        else:
            stack[-1][1].append(token)
    if len(stack) != 1:
        raise ValueError("unclosed [")
    return root


def total(items):
    return sum(len(i) if isinstance(i, str) else i[2] for i in items)


def length(text):
    return total(parse(text))


def expand(text, limit):
    items = parse(text)
    if total(items) > limit:
        raise OverflowError(f"the expansion is longer than {limit} characters")
    # Builds bottom-up with an explicit stack, so deep nesting is fine.
    out = []
    stack = [(items, 0, out, 1)]
    while stack:
        current, index, parts, count = stack.pop()
        if index == len(current):
            if stack:
                stack[-1][2].append("".join(parts) * count)
            continue
        stack.append((current, index + 1, parts, count))
        item = current[index]
        if isinstance(item, str):
            parts.append(item)
        else:
            stack.append((item[1], 0, [], item[0]))
    return "".join(out)
