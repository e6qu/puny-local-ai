"""Stock records.

A stock file has one item per line: "name,quantity,unit_price". Blank lines
and lines starting with "#" (after optional spaces) are ignored. Spaces around
each field are ignored.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Item:
    name: str
    quantity: int
    unit_price: Decimal

    def value(self):
        """Quantity times unit price."""
        return self.quantity * self.unit_price


def parse_line(line):
    """Parses one "name,quantity,unit_price" line into an Item."""
    name, quantity, price = (field.strip() for field in line.split(","))
    return Item(name, int(quantity), Decimal(price))


def parse_stock(text):
    """Parses a whole stock file into a list of Items, in file order."""
    return [
        parse_line(line)
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def needs_restock(items, threshold):
    """Names of the items whose quantity is at or below the threshold."""
    return [item.name for item in items if item.quantity <= threshold]


def most_valuable(items):
    """The item with the highest total value (quantity times unit price)."""
    return max(items, key=lambda item: item.value())
