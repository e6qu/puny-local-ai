import unittest

from logstats.report import report

LOG = """2026-10-30T09:00:00Z GET /api/items/1 200 10ms user=ana
2026-10-30T09:00:01Z GET /api/items/2 200 30ms user=bo
2026-10-30T09:00:02Z POST /api/items 201 50ms user=ana
2026-10-30T09:00:03Z GET /api/items/3 503 90ms user=cy
not a log line
2026-10-30T09:00:04Z GET /health 200 1ms user=probe
""".splitlines()


class ReportTest(unittest.TestCase):
    def test_report(self):
        self.assertEqual(report(LOG), """lines: 6, malformed: 1
GET /api/items/{id} requests=3 errors=1 p50=30ms p95=90ms users=3
POST /api/items requests=1 errors=0 p50=50ms p95=50ms users=1
GET /health requests=1 errors=0 p50=1ms p95=1ms users=1
top users:
  ana 2
  bo 1
  cy 1
  probe 1
""")


if __name__ == "__main__":
    unittest.main()
