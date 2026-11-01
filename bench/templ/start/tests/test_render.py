import unittest

from templ import render


class RenderTest(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(render("hello", {}), "hello")

    def test_value(self):
        self.assertEqual(render("hi {{ name }}!", {"name": "Ana"}), "hi Ana!")


if __name__ == "__main__":
    unittest.main()
