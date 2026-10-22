import unittest
from decimal import Decimal

from billing.invoice import Invoice
from billing.report import reduced_rate_tax, total_tax
from billing.tax import calc_tax


class BillingTest(unittest.TestCase):
    def test_calc_tax_rounds_to_cents(self):
        self.assertEqual(calc_tax(Decimal("10.05"), Decimal("0.2")), Decimal("2.01"))

    def test_invoice_total(self):
        invoice = Invoice([("a", "10.00"), ("b", "2.50")])
        self.assertEqual(invoice.total(), Decimal("15.00"))

    def test_report(self):
        invoices = [Invoice([("a", "100")]), Invoice([("b", "1.25")], rate=Decimal("0.1"))]
        self.assertEqual(total_tax(invoices), Decimal("20.13"))
        self.assertEqual(reduced_rate_tax("19.99"), Decimal("1.00"))


if __name__ == "__main__":
    unittest.main()
