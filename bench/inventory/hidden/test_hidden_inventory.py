import unittest
from decimal import Decimal

from inventory import Item, most_valuable, needs_restock, parse_line, parse_stock


class HiddenInventoryTest(unittest.TestCase):
    def test_parse_line(self):
        self.assertEqual(parse_line("washer,7,0.05"), Item("washer", 7, Decimal("0.05")))
        self.assertEqual(parse_line("  big bolt ,  2,1.50  "), Item("big bolt", 2, Decimal("1.50")))

    def test_parse_stock(self):
        text = "  # header\n\n   \nbolt,3,0.25\n  # middle\nnut , 10 , 0.10\n"
        items = parse_stock(text)
        self.assertEqual([i.name for i in items], ["bolt", "nut"])
        self.assertEqual(items[1].quantity, 10)
        self.assertEqual(parse_stock(""), [])

    def test_needs_restock(self):
        items = [Item("a", 0, Decimal(1)), Item("b", 3, Decimal(1)), Item("c", 4, Decimal(1))]
        self.assertEqual(needs_restock(items, 3), ["a", "b"])
        self.assertEqual(needs_restock(items, -1), [])

    def test_most_valuable(self):
        items = [Item("x", 3, Decimal("2")), Item("y", 2, Decimal("4")), Item("z", 10, Decimal("0.5"))]
        self.assertEqual(most_valuable(items).name, "y")

    def test_value_is_exact(self):
        self.assertEqual(Item("a", 3, Decimal("0.10")).value(), Decimal("0.30"))


if __name__ == "__main__":
    unittest.main()
