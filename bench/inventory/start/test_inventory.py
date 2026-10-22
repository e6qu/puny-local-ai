import unittest
from decimal import Decimal

from inventory import Item, most_valuable, needs_restock, parse_line, parse_stock


class InventoryTest(unittest.TestCase):
    def test_parse_line_ignores_spaces(self):
        self.assertEqual(parse_line(" bolt , 3 , 0.25 "), Item("bolt", 3, Decimal("0.25")))

    def test_parse_stock_skips_blank_and_comment_lines(self):
        text = "# stock\nbolt,3,0.25\n\nnut,10,0.10\n"
        self.assertEqual([i.name for i in parse_stock(text)], ["bolt", "nut"])

    def test_needs_restock_includes_threshold(self):
        items = [Item("a", 5, Decimal(1)), Item("b", 6, Decimal(1))]
        self.assertEqual(needs_restock(items, 5), ["a"])

    def test_most_valuable_uses_total_value(self):
        items = [Item("cheap", 100, Decimal("1")), Item("dear", 1, Decimal("50"))]
        self.assertEqual(most_valuable(items).name, "cheap")


if __name__ == "__main__":
    unittest.main()
