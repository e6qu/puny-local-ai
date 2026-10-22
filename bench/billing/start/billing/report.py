"""Summaries across invoices."""

from decimal import Decimal

from .tax import calc_tax

REDUCED_RATE = Decimal("0.05")  # fraction, for calc_tax


def total_tax(invoices):
    return sum((invoice.tax() for invoice in invoices), Decimal("0"))


def reduced_rate_tax(amount):
    """Tax on amount at the reduced rate."""
    return calc_tax(Decimal(amount), REDUCED_RATE)
