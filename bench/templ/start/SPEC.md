# templ

`render(source, context)` returns `source` with its tags filled in from `context`, a dict. Text outside tags is copied unchanged.

## Values

`{{ expr }}` writes the value of `expr`. An expression is a *name* (letters, digits and `_`, not starting with a digit) followed by any number of `.attr` parts, then any number of filters.

- `a.b` looks up key `b` if `a` is a dict, otherwise attribute `b`. A missing name, key or attribute is *undefined*.
- Writing an undefined value raises `TemplateError`, unless a `default` filter replaces it.
- Values are written with `str()`, except `None`, which is written as an empty string, and `True` and `False`, written as `true` and `false`.
- The written text is HTML-escaped: `&`, `<`, `>`, `"` and `'` become `&amp;`, `&lt;`, `&gt;`, `&quot;` and `&#x27;`. The `safe` filter turns escaping off for that value.

Spaces inside `{{ }}` and around `|` are ignored.

## Filters

A filter is `| name` or `| name(argument)`, where the argument is a string literal in double quotes (`\"` and `\\` are its only escapes) or a whole number, possibly negative.

- `upper`, `lower`: the string in upper or lower case.
- `trim`: the string without leading and trailing whitespace.
- `length`: the number of items or characters.
- `default(x)`: `x` if the value is undefined or `None`, otherwise the value.
- `join(sep)`: the items converted with `str()` and joined by `sep`.
- `truncate(n)`: the string unchanged if it has at most `n` characters, otherwise its first `n` characters followed by `...`.
- `safe`: the value, not escaped when written.

Filters run left to right. Any filter other than `default` applied to an undefined value raises `TemplateError`. An unknown filter, a missing or extra argument, or an argument of the wrong type raises `TemplateError`.

## Conditions

    {% if expr %}...{% elif expr %}...{% else %}...{% endif %}

`elif` and `else` are optional, `else` comes last, and there may be any number of `elif`. A condition is an expression as above, optionally preceded by `not`. It is true when the value is defined and truthy in Python.

## Loops

    {% for name in expr %}...{% else %}...{% endfor %}

The body is rendered once per item, with `name` bound to the item and `loop` bound to an object with `index` (from 1), `first` and `last`. The optional `else` part is rendered instead when there are no items. A loop over an undefined value or one that is not iterable raises `TemplateError`. Names bound by a loop are visible only inside it, and hide names of the same name outside it. Loops nest.

## Comments and whitespace

`{# ... #}` is a comment and writes nothing. A tag written with a dash, such as `{%- if x -%}` or `{{- x -}}`, removes all whitespace, newlines included, on that side of the tag in the text next to it.

## Errors

`TemplateError` is a subclass of `ValueError` defined in `templ`. Its `line` attribute is the 1-based line of `source` where the tag that caused the error starts. Malformed templates raise it too: an unclosed tag such as `{{ x`, an unknown tag such as `{% while %}`, an `endif` without an `if`, an `elif` or `else` out of place, a block left open at the end, or a tag whose content is not valid for it.
