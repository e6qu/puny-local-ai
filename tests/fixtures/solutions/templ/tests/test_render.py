import unittest

from templ import TemplateError, render


class RenderTest(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(render("hello", {}), "hello")

    def test_value(self):
        self.assertEqual(render("hi {{ name }}!", {"name": "Ana"}), "hi Ana!")

    def test_filters_and_escaping(self):
        self.assertEqual(render("{{ x | trim | upper }}", {"x": " <b> "}), "&lt;B&gt;")
        self.assertEqual(render("{{ x | default(\"-\") }}", {}), "-")

    def test_if_and_for(self):
        self.assertEqual(render("{% for i in xs %}{% if loop.last %}{{ i }}{% endif %}{% endfor %}", {"xs": [1, 2]}), "2")

    def test_errors_have_lines(self):
        with self.assertRaises(TemplateError) as caught:
            render("a\n{{ nope }}", {})
        self.assertEqual(caught.exception.line, 2)


if __name__ == "__main__":
    unittest.main()
