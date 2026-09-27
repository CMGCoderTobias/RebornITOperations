import unittest
from core import parse_date

class TimestampCompatibilityTest(unittest.TestCase):
    def test_windows_and_variable_fraction_precision(self):
        for fraction, expected in [('0259362', 25936), ('1', 100000), ('1234', 123400)]:
            self.assertEqual(parse_date('2026-09-27T01:04:59.'+fraction+'Z').microsecond, expected)
        self.assertEqual(parse_date('2026-09-27T01:04:59.1234567-04:00').utcoffset().total_seconds(), -14400)

