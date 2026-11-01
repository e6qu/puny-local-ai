"""Reference solution: tokens per segment, a linear star matcher, and a DP over segments."""


def parse_segment(segment):
    """Tokens of one pattern segment: ("lit", c), ("any",), ("class", negate, items), ("star",)."""
    tokens = []
    i = 0
    while i < len(segment):
        c = segment[i]
        if c == "\\":
            if i + 1 == len(segment):
                raise ValueError("pattern ends with a lone backslash")
            tokens.append(("lit", segment[i + 1]))
            i += 2
        elif c == "*":
            if not tokens or tokens[-1] != ("star",):
                tokens.append(("star",))
            i += 1
        elif c == "?":
            tokens.append(("any",))
            i += 1
        elif c == "[":
            j = i + 1
            negate = j < len(segment) and segment[j] == "!"
            if negate:
                j += 1
            items = []
            first = True
            while True:
                if j >= len(segment):
                    raise ValueError("unclosed [ in pattern")
                if segment[j] == "]" and not first:
                    break
                first = False
                if j + 2 < len(segment) and segment[j + 1] == "-" and segment[j + 2] != "]":
                    items.append((segment[j], segment[j + 2]))
                    j += 3
                else:
                    items.append((segment[j], segment[j]))
                    j += 1
            tokens.append(("class", negate, items))
            i = j + 1
        else:
            tokens.append(("lit", c))
            i += 1
    return tokens


def one(token, ch):
    kind = token[0]
    if kind == "lit":
        return token[1] == ch
    if kind == "any":
        return True
    inside = any(lo <= ch <= hi for lo, hi in token[2])
    return inside != token[1]


def match_segment(tokens, text):
    """Greedy star matching with one backtrack point: linear for a fixed pattern."""
    t = s = 0
    star_t = star_s = -1
    while s < len(text):
        if t < len(tokens) and tokens[t][0] != "star" and one(tokens[t], text[s]):
            t += 1
            s += 1
        elif t < len(tokens) and tokens[t][0] == "star":
            star_t, star_s = t, s
            t += 1
        elif star_t >= 0:
            star_s += 1
            s = star_s
            t = star_t + 1
        else:
            return False
    while t < len(tokens) and tokens[t][0] == "star":
        t += 1
    return t == len(tokens)


def match(pattern, path):
    segments = ["**" if raw == "**" else parse_segment(raw) for raw in pattern.split("/")]
    parts = path.split("/")
    if pattern == "**":
        return True
    # reach[j]: the first i pattern segments can match the first j path parts.
    reach = [True] + [False] * len(parts)
    for segment in segments:
        nxt = [False] * (len(parts) + 1)
        if segment == "**":
            seen = False
            for j in range(len(parts) + 1):
                seen = seen or reach[j]
                nxt[j] = seen
        else:
            for j in range(len(parts)):
                if reach[j] and match_segment(segment, parts[j]):
                    nxt[j + 1] = True
        reach = nxt
    return reach[len(parts)]
