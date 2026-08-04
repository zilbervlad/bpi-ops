from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from app.services.doughy_universal_gateway import (
    _prep_build_to_for_date,
    _synthetic_prep_rows,
)


class DoughyPrepLiveContextTests(unittest.TestCase):
    def template(self, store, name, monday=True, monday_build=None):
        return SimpleNamespace(
            store_number=store,
            section_name="Chicken / Wings / Boneless",
            item_name=name,
            build_to="4 bags",
            monday_build_to=monday_build,
            monday=monday,
        )

    def test_weekday_build_to_wins(self):
        template = self.template("3219", "Wings", monday_build="6 bags")
        self.assertEqual(
            _prep_build_to_for_date(template, date(2026, 8, 3)),
            "6 bags",
        )

    def test_missing_store_is_synthesized_not_started(self):
        rows = _synthetic_prep_rows(
            template_rows=[self.template("3219", "Wings")],
            store_numbers={"3219", "3225"},
            prep_date=date(2026, 8, 3),
            existing_store_numbers={"3225"},
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["store_number"], "3219")
        self.assertEqual(rows[0]["status"], "not_started")
        self.assertEqual(rows[0]["item_count"], 1)
        self.assertFalse(rows[0]["record_exists"])

    def test_existing_store_is_not_duplicated(self):
        rows = _synthetic_prep_rows(
            template_rows=[self.template("3219", "Wings")],
            store_numbers={"3219"},
            prep_date=date(2026, 8, 3),
            existing_store_numbers={"3219"},
        )
        self.assertEqual(rows, [])

    def test_no_template_is_reported_separately(self):
        rows = _synthetic_prep_rows(
            template_rows=[],
            store_numbers={"3003"},
            prep_date=date(2026, 8, 3),
            existing_store_numbers=set(),
        )
        self.assertEqual(rows[0]["status"], "no_active_template")
        self.assertEqual(rows[0]["item_count"], 0)


if __name__ == "__main__":
    unittest.main()
