from __future__ import annotations

import unittest
from datetime import date, timedelta


class DoughyChecklistHistoryContextTests(unittest.TestCase):
    def test_expected_date_math_for_last_week(self):
        start = date(2026, 7, 27)
        end = date(2026, 8, 2)
        expected_days = (end - start).days + 1
        dates = [
            start + timedelta(days=offset)
            for offset in range(expected_days)
        ]

        self.assertEqual(expected_days, 7)
        self.assertEqual(dates[0].isoformat(), "2026-07-27")
        self.assertEqual(dates[-1].isoformat(), "2026-08-02")

    def test_missing_dates_do_not_include_recorded_dates(self):
        expected_dates = [
            date(2026, 7, 27) + timedelta(days=offset)
            for offset in range(7)
        ]
        recorded = {"2026-07-27", "2026-07-29"}
        missing = [
            value.isoformat()
            for value in expected_dates
            if value.isoformat() not in recorded
        ]

        self.assertEqual(len(missing), 5)
        self.assertNotIn("2026-07-27", missing)
        self.assertNotIn("2026-07-29", missing)


if __name__ == "__main__":
    unittest.main()
