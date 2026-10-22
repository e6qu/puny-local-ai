"""Tax arithmetic. Amounts are Decimals rounded to whole cents."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def compute_tax(amount, rate):
    """Tax on amount at rate, a percentage such as 20 for 20%."""
    return (amount * Decimal(rate) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
