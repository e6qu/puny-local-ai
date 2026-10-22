"""Tax arithmetic. Amounts are Decimals rounded to whole cents."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def calc_tax(amount, rate):
    """Tax on amount at rate, a fraction such as Decimal("0.2") for 20%."""
    return (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)
