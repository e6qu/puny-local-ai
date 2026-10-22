import unittest
from decimal import Decimal

import billing.tax
from billing.invoice import VAT_RATE, Invoice
from billing.report import REDUCED_RATE, reduced_rate_tax, total_tax
from billing.tax import compute_tax


class HiddenBillingTest(unittest.TestCase):
    def test_old_name_is_gone(self):
        self.assertFalse(hasattr(billing.tax, "calc_tax"))

    def test_rate_is_a_percentage(self):
        self.assertEqual(compute_tax(Decimal("100"), 20), Decimal("20.00"))
        self.assertEqual(compute_tax(Decimal("10.05"), Decimal("20")), Decimal("2.01"))
        self.assertEqual(compute_tax(Decimal("0.10"), Decimal("5")), Decimal("0.01"))
        self.assertEqual(VAT_RATE, 20)
        self.assertEqual(REDUCED_RATE, 5)

    def test_results_unchanged(self):
        self.assertEqual(Invoice([("a", "10.00"), ("b", "2.50")]).total(), Decimal("15.00"))
        invoices = [Invoice([("a", "100")]), Invoice([("b", "1.25")], rate=Decimal("10"))]
        self.assertEqual(total_tax(invoices), Decimal("20.13"))
        self.assertEqual(reduced_rate_tax("19.99"), Decimal("1.00"))


if __name__ == "__main__":
    unittest.main()
