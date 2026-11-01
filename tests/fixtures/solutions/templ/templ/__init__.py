"""A small template language; SPEC.md describes it."""

import re

__all__ = ["TemplateError", "render"]


class TemplateError(ValueError):
    def __init__(self, message, line):
        super().__init__(f"line {line}: {message}")
        self.line = line


class Undefined:
    def __init__(self, what):
        self.what = what


TAG = re.compile(r"\{(\{|%|#)(-?)")
CLOSERS = {"{": "}}", "%": "%}", "#": "#}"}
NAME = r"[A-Za-z_][A-Za-z0-9_]*"
EXPR = re.compile(rf"\s*({NAME}(?:\.{NAME})*)\s*((?:\|\s*{NAME}\s*(?:\([^)]*\))?\s*)*)$")
FILTER = re.compile(rf"\|\s*({NAME})\s*(?:\(([^)]*)\))?\s*")
STRING = re.compile(r'"((?:[^"\\]|\\["\\])*)"$')
INTEGER = re.compile(r"-?[0-9]+$")


def tokenize(source):
    """Text and tag tokens: ("text", s, line) or (kind, content, line)."""
    tokens = []
    pos = 0
    strip_next = False
    while True:
        m = TAG.search(source, pos)
        if not m:
            text = source[pos:]
            tokens.append(["text", text.lstrip() if strip_next else text, source.count("\n", 0, pos) + 1])
            return tokens
        text = source[pos:m.start()]
        if strip_next:
            text = text.lstrip()
        if m.group(2):
            text = text.rstrip()
        tokens.append(["text", text, source.count("\n", 0, pos) + 1])
        line = source.count("\n", 0, m.start()) + 1
        kind = m.group(1)
        closer = CLOSERS[kind]
        end = source.find(closer, m.end())
        if end < 0:
            raise TemplateError(f"unclosed {{{kind} tag", line)
        content = source[m.end():end]
        strip_next = content.endswith("-")
        if strip_next:
            content = content[:-1]
        if kind != "#":
            tokens.append([kind, content.strip(), line])
        pos = end + len(closer)


def parse_argument(text, line):
    text = text.strip()
    m = STRING.match(text)
    if m:
        return re.sub(r"\\([\"\\])", r"\1", m.group(1))
    if INTEGER.match(text):
        return int(text)
    raise TemplateError(f"bad filter argument {text!r}", line)


def parse_expr(text, line):
    m = EXPR.match(text)
    if not m:
        raise TemplateError(f"bad expression {text!r}", line)
    path = m.group(1).split(".")
    filters = []
    for f in FILTER.finditer(m.group(2)):
        argument = f.group(2)
        filters.append((f.group(1), None if argument is None else parse_argument(argument, line), argument is not None))
    return path, filters, line


ARGUMENTS = {"upper": None, "lower": None, "trim": None, "length": None, "safe": None,
             "default": (str, int), "join": (str,), "truncate": (int,)}


def lookup(path, scopes):
    for scope in reversed(scopes):
        if path[0] in scope:
            value = scope[path[0]]
            break
    else:
        return Undefined(path[0])
    for part in path[1:]:
        if isinstance(value, Undefined):
            return value
        if isinstance(value, dict):
            value = value[part] if part in value else Undefined(part)
        elif hasattr(value, part):
            value = getattr(value, part)
        else:
            value = Undefined(part)
    return value


def evaluate(expr, scopes):
    """The value and whether it is safe from escaping."""
    path, filters, line = expr
    value = lookup(path, scopes)
    safe = False
    for name, argument, has_argument in filters:
        if name not in ARGUMENTS:
            raise TemplateError(f"unknown filter {name!r}", line)
        types = ARGUMENTS[name]
        if (types is None) == has_argument or (types and (type(argument) not in types)):
            raise TemplateError(f"bad argument for filter {name!r}", line)
        if name == "default":
            if isinstance(value, Undefined) or value is None:
                value = argument
            continue
        if isinstance(value, Undefined):
            raise TemplateError(f"{value.what!r} is undefined", line)
        try:
            if name == "upper":
                value = value.upper()
            elif name == "lower":
                value = value.lower()
            elif name == "trim":
                value = value.strip()
            elif name == "length":
                value = len(value)
            elif name == "join":
                value = argument.join(str(v) for v in value)
            elif name == "truncate":
                value = value if len(value) <= argument else value[:argument] + "..."
            elif name == "safe":
                safe = True
        except (AttributeError, TypeError) as e:
            raise TemplateError(f"filter {name!r} cannot take {value!r}: {e}", line) from None
    return value, safe


def escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#x27;"))


def write(value, safe, line):
    if isinstance(value, Undefined):
        raise TemplateError(f"{value.what!r} is undefined", line)
    if value is None:
        text = ""
    elif value is True:
        text = "true"
    elif value is False:
        text = "false"
    else:
        text = str(value)
    return text if safe else escape(text)


def parse(tokens):
    """A tree: lists of ("text", s), ("value", expr), ("if", [(cond, body)], else_body), ("for", name, expr, body, else_body)."""
    root = []
    stack = [("root", root, None, 0)]
    for kind, content, line in tokens:
        body = stack[-1][1]
        if kind == "text":
            if content:
                body.append(("text", content))
        elif kind == "{":
            body.append(("value", parse_expr(content, line)))
        else:
            word, _, rest = content.partition(" ")
            rest = rest.strip()
            top = stack[-1]
            if word == "if":
                node = ["if", [], None]
                branch = []
                node[1].append((parse_condition(rest, line), branch))
                body.append(node)
                stack.append(("if", branch, node, line))
            elif word == "elif":
                if top[0] != "if" or top[2][2] is not None:
                    raise TemplateError("elif without an open if", line)
                branch = []
                top[2][1].append((parse_condition(rest, line), branch))
                stack[-1] = ("if", branch, top[2], top[3])
            elif word == "else":
                if rest or top[0] not in ("if", "for") or (top[0] == "if" and top[2][2] is not None) or (top[0] == "for" and top[2][4] is not None):
                    raise TemplateError("else out of place", line)
                branch = []
                if top[0] == "if":
                    top[2][2] = branch
                else:
                    top[2][4] = branch
                stack[-1] = (top[0], branch, top[2], top[3])
            elif word == "endif":
                if top[0] != "if" or rest:
                    raise TemplateError("endif without an if", line)
                stack.pop()
            elif word == "for":
                m = re.match(rf"({NAME})\s+in\s+(.+)$", rest)
                if not m:
                    raise TemplateError(f"bad for tag {content!r}", line)
                node = ["for", m.group(1), parse_expr(m.group(2), line), [], None]
                body.append(node)
                stack.append(("for", node[3], node, line))
            elif word == "endfor":
                if top[0] != "for" or rest:
                    raise TemplateError("endfor without a for", line)
                stack.pop()
            else:
                raise TemplateError(f"unknown tag {word!r}", line)
    if len(stack) > 1:
        raise TemplateError(f"{stack[-1][0]} is never closed", stack[-1][3])
    return root


def parse_condition(text, line):
    negate = False
    m = re.match(r"not\s+(.+)$", text)
    if m:
        negate, text = True, m.group(1)
    if not text:
        raise TemplateError("empty condition", line)
    return negate, parse_expr(text, line)


class Loop:
    def __init__(self, index, count):
        self.index = index
        self.first = index == 1
        self.last = index == count


def run(nodes, scopes, out):
    for node in nodes:
        kind = node[0]
        if kind == "text":
            out.append(node[1])
        elif kind == "value":
            value, safe = evaluate(node[1], scopes)
            out.append(write(value, safe, node[1][2]))
        elif kind == "if":
            for (negate, expr), body in node[1]:
                value, _ = evaluate(expr, scopes)
                truth = not isinstance(value, Undefined) and bool(value)
                if truth != negate:
                    run(body, scopes, out)
                    break
            else:
                if node[2] is not None:
                    run(node[2], scopes, out)
        else:
            _, name, expr, body, else_body = node
            items, _ = evaluate(expr, scopes)
            if isinstance(items, Undefined):
                raise TemplateError(f"{items.what!r} is undefined", expr[2])
            try:
                items = list(items)
            except TypeError:
                raise TemplateError(f"cannot loop over {items!r}", expr[2]) from None
            if not items and else_body is not None:
                run(else_body, scopes, out)
            for i, item in enumerate(items, 1):
                scopes.append({name: item, "loop": Loop(i, len(items))})
                try:
                    run(body, scopes, out)
                finally:
                    scopes.pop()


def render(source, context):
    tree = parse(tokenize(source))
    out = []
    run(tree, [dict(context)], out)
    return "".join(out)
