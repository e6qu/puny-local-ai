"""Summaries across invoices."""

from decimal import Decimal

from .tax import compute_tax

REDUCED_RATE = Decimal("5")  # percentage, for compute_tax


def total_tax(invoices):
    return sum((invoice.tax() for invoice in invoices), Decimal("0"))


def reduced_rate_tax(amount):
    """Tax on amount at the reduced rate."""
    return compute_tax(Decimal(amount), REDUCED_RATE)
