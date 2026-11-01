import inspect
import re
import unittest
from types import SimpleNamespace

import templ
from templ import TemplateError, render


class HiddenRenderTest(unittest.TestCase):
    def check(self, cases, context=None):
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(render(source, context or {}), expected)

    def error(self, source, context=None, line=1):
        with self.assertRaises(TemplateError) as caught:
            render(source, context or {})
        self.assertEqual(caught.exception.line, line, str(caught.exception))
        return caught.exception

    def test_values_and_lookups(self):
        ctx = {"user": {"name": "Ana", "tags": ["a", "b"]}, "obj": SimpleNamespace(size=3, inner={"k": "v"}), "n": 0}
        self.check([
            ("{{user.name}}", "Ana"), ("{{  user.name  }}", "Ana"),
            ("{{ obj.size }}", "3"), ("{{ obj.inner.k }}", "v"), ("{{ n }}", "0"), ("text {{ user.tags }}", "text [&#x27;a&#x27;, &#x27;b&#x27;]"),
        ], ctx)

    def test_written_forms(self):
        self.check([("{{ a }}|{{ b }}|{{ c }}|{{ d }}", "|true|false|1.5")], {"a": None, "b": True, "c": False, "d": 1.5})

    def test_escaping(self):
        self.check([("{{ x }}", "&lt;a href=&quot;/&quot;&gt;Tom &amp; Jerry&#x27;s&lt;/a&gt;"),
                    ("{{ x | safe }}", "<a href=\"/\">Tom & Jerry's</a>"),
                    ("{{ x | safe | upper }}", "<A HREF=\"/\">TOM & JERRY'S</A>")],
                   {"x": "<a href=\"/\">Tom & Jerry's</a>"})

    def test_undefined(self):
        self.error("{{ missing }}")
        self.error("{{ user.age }}", {"user": {"name": "a"}})
        self.error("{{ obj.nope }}", {"obj": SimpleNamespace()})
        self.error("{{ missing.deeper }}")
        self.error("{{ missing | upper }}")
        self.check([("{{ missing | default(\"none\") }}", "none"), ("{{ u.age | default(7) }}", "7"),
                    ("{{ nothing | default(\"x\") }}", "x"), ("{{ zero | default(5) }}", "0")],
                   {"u": {}, "nothing": None, "zero": 0})

    def test_filters(self):
        ctx = {"s": "  Mixed Case  ", "items": [1, "two", 3], "long": "abcdefghij"}
        self.check([
            ("{{ s | trim | upper }}", "MIXED CASE"), ("{{ s | lower | trim }}", "mixed case"), ("{{ items | length }}", "3"),
            ("{{ long | length }}", "10"), ("{{ items | join(\", \") }}", "1, two, 3"), ("{{ items | join(\"\") }}", "1two3"),
            ("{{ long | truncate(4) }}", "abcd..."), ("{{ long | truncate(10) }}", "abcdefghij"), ("{{ long|truncate(0) }}", "..."),
            ("{{ s | default(\"x\") | trim }}", "Mixed Case"), ("{{ q | join(\"\\\"\") }}", "a&quot;b"),
            ("{{ q | join(\"\\\\\") }}", "a\\b"),
        ], dict(ctx, q=["a", "b"]))

    def test_filter_errors(self):
        for source in ("{{ s | shout }}", "{{ s | upper(1) }}", "{{ s | truncate }}", "{{ s | truncate(\"3\") }}",
                       "{{ s | join(2) }}", "{{ s | default }}", "{{ s | join(\"a\", \"b\") }}", "{{ s | truncate(1.5) }}",
                       "{{ n | upper }}"):
            with self.subTest(source):
                self.error(source, {"s": "x", "n": 5})

    def test_conditions(self):
        ctx = {"yes": True, "no": False, "empty": [], "full": [0], "user": {"admin": False}}
        self.check([
            ("{% if yes %}Y{% endif %}", "Y"), ("{% if no %}Y{% else %}N{% endif %}", "N"),
            ("{% if missing %}Y{% else %}N{% endif %}", "N"), ("{% if not missing %}Y{% endif %}", "Y"),
            ("{% if empty %}a{% elif full %}b{% else %}c{% endif %}", "b"),
            ("{% if no %}a{% elif empty %}b{% elif user.admin %}c{% else %}d{% endif %}", "d"),
            ("{% if not user.admin %}guest{% endif %}", "guest"), ("{% if full | length %}n{% endif %}", "n"),
            ("{% if yes %}{% if no %}x{% else %}y{% endif %}{% endif %}", "y"),
        ], ctx)

    def test_loops(self):
        ctx = {"items": ["a", "b", "c"], "rows": [[1, 2], [3]], "item": "outer", "none": []}
        self.check([
            ("{% for x in items %}{{ x }}{% endfor %}", "abc"),
            ("{% for x in items %}{{ loop.index }}{% if loop.first %}F{% endif %}{% if loop.last %}L{% endif %} {% endfor %}", "1F 2 3L "),
            ("{% for r in rows %}[{% for v in r %}{{ v }}{% if not loop.last %},{% endif %}{% endfor %}]{% endfor %}", "[1,2][3]"),
            ("{% for item in items %}{{ item }}{% endfor %}{{ item }}", "abcouter"),
            ("{% for x in none %}x{% else %}empty{% endfor %}", "empty"),
            ("{% for x in items %}{{ x }}{% else %}empty{% endfor %}", "abc"),
            ("{% for k in d %}{{ k }}{% endfor %}", "pq"),
        ], dict(ctx, d={"p": 1, "q": 2}))
        self.error("{% for x in items %}{% endfor %}{{ x }}", ctx)
        self.error("{% for x in missing %}{% endfor %}")
        self.error("{% for x in n %}{% endfor %}", {"n": 5})

    def test_comments_and_whitespace_control(self):
        self.check([
            ("a{# note #}b", "ab"), ("a {# multi\nline #} b", "a  b"),
            ("<ul>\n  {%- for x in items %}\n  <li>{{ x }}</li>\n  {%- endfor %}\n</ul>", "<ul>\n  <li>1</li>\n  <li>2</li>\n</ul>"),
            ("a   {{- x -}}   b", "a1b"), ("a \n {{- x }} \n b", "a1 \n b"), ("{% if x -%}  \n  yes{%- endif %}", "yes"),
        ], {"items": [1, 2], "x": 1})

    def test_malformed_templates(self):
        cases = [
            ("{{ x", 1), ("a\n{% if x %}", 2), ("{% while x %}{% endwhile %}", 1), ("x\ny\n{% endif %}", 3),
            ("{% if x %}{% else %}{% elif y %}{% endif %}", 1), ("{% else %}", 1), ("{% if x %}{% endfor %}", 1),
            ("{% for x %}{% endfor %}", 1), ("{% for in y %}{% endfor %}", 1), ("{{ }}", 1), ("{{ 1abc }}", 1),
            ("{{ a..b }}", 1), ("{# open", 1), ("\n\n{% if x %}{% else %}{% else %}{% endif %}", 3), ("{% if %}{% endif %}", 1),
            ("{% for x in xs %}\n\n{% endif %}", 3), ("{{ x | }}", 1),
        ]
        for source, line in cases:
            with self.subTest(source=source):
                self.error(source, {"x": 1, "y": 2, "xs": []}, line)

    def test_error_lines_point_at_the_tag(self):
        self.error("line one\nline two {{ missing }}\nline three", line=2)
        self.error("{% for x in xs %}\n\n  {{ x.nope }}\n{% endfor %}", {"xs": [{}]}, line=3)
        error = self.error("\n\n\n{{ a | bogus }}", {"a": 1}, line=4)
        self.assertIn("bogus", str(error))

    def test_template_error_is_a_value_error(self):
        self.assertTrue(issubclass(TemplateError, ValueError))

    def test_does_not_use_python_itself(self):
        source = inspect.getsource(templ)
        for forbidden in (r"(?<![.\w])eval\s*\(", r"(?<![.\w])exec\s*\(", r"(?<![.\w])compile\s*\(", r"__import__"):
            self.assertIsNone(re.search(forbidden, source), forbidden)

    def test_large_template(self):
        source = "{% for r in rows %}{{ r.name | upper }}:{% for c in r.cells %}{{ c }},{% endfor %}\n{% endfor %}"
        rows = [{"name": f"row{i}", "cells": list(range(20))} for i in range(500)]
        out = render(source, {"rows": rows})
        self.assertEqual(out.count("\n"), 500)
        self.assertTrue(out.startswith("ROW0:0,1,2,"))


if __name__ == "__main__":
    unittest.main()
