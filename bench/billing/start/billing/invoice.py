"""Invoices: a list of (description, net amount) lines plus VAT."""

from decimal import Decimal

from .tax import calc_tax

VAT_RATE = Decimal("0.2")  # as a fraction, the form calc_tax expects


class Invoice:
    def __init__(self, lines, rate=VAT_RATE):
        self.lines = [(text, Decimal(amount)) for text, amount in lines]
        self.rate = rate

    def net(self):
        return sum((amount for _, amount in self.lines), Decimal("0"))

    def tax(self):
        return calc_tax(self.net(), self.rate)

    def total(self):
        return self.net() + self.tax()
