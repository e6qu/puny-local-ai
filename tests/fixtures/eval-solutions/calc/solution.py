"""Reference solution: a tokenizer and a recursive-descent parser."""

import re
from fractions import Fraction

TOKEN = re.compile(r"\s*(?:(\d+)|([A-Za-z_]\w*)|(\*\*|//|[-+*/%()]))")


def tokenize(text):
    tokens = []
    pos = 0
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos == len(text):
            break
        m = TOKEN.match(text, pos)
        if not m:
            raise syntax(text, pos)
        start = m.start(m.lastindex)
        kind = ("num", "name", "op")[m.lastindex - 1]
        tokens.append((kind, m.group(m.lastindex), start))
        pos = m.end()
    tokens.append(("end", "", len(text)))
    return tokens


def syntax(text, offset):
    error = SyntaxError(f"cannot read {text!r} at {offset}")
    error.offset = offset
    return error


def evaluate(expression, variables=None):
    variables = variables or {}
    tokens = tokenize(expression)
    pos = 0

    def peek():
        return tokens[pos]

    def take():
        nonlocal pos
        token = tokens[pos]
        pos += 1
        return token

    def additive():
        value = term()
        while peek()[1] in ("+", "-") and peek()[0] == "op":
            op = take()[1]
            right = term()
            value = value + right if op == "+" else value - right
        return value

    def term():
        value = unary()
        while peek()[0] == "op" and peek()[1] in ("*", "/", "//", "%"):
            op = take()[1]
            right = unary()
            if op == "*":
                value = value * right
            elif right == 0:
                raise ZeroDivisionError("division by zero")
            elif op == "/":
                value = value / right
            elif op == "//":
                value = Fraction(value // right)
            else:
                value = value % right
        return value

    def unary():
        if peek()[0] == "op" and peek()[1] in ("+", "-"):
            op = take()[1]
            value = unary()
            return -value if op == "-" else value
        return power()

    def power():
        base = atom()
        if peek()[0] == "op" and peek()[1] == "**":
            take()
            exponent = unary()
            if exponent.denominator != 1:
                raise ValueError("the exponent must be a whole number")
            if base == 0 and exponent < 0:
                raise ZeroDivisionError("0 cannot be raised to a negative power")
            return Fraction(base) ** int(exponent)
        return base

    def atom():
        kind, text, offset = take()
        if kind == "num":
            return Fraction(int(text))
        if kind == "name":
            if text not in variables:
                raise NameError(f"name {text!r} is not defined")
            return Fraction(variables[text])
        if text == "(":
            value = additive()
            kind, text, offset = take()
            if text != ")":
                raise syntax(expression, offset)
            return value
        raise syntax(expression, offset)

    value = additive()
    kind, _, offset = peek()
    if kind != "end":
        raise syntax(expression, offset)
    return value
