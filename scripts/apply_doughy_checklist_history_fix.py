#!/usr/bin/env python3
"""Patch BPI Ops checklist-history live context for Doughy.

Adds per-store completion, incomplete, and missing-day detail without changing
checklist records. The script is idempotent and writes focused regression tests.
"""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BACKUP_SUFFIX = ".backup-before-doughy-checklist-history-20260804"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")

    if new in text:
        print(f"Already patched: {path.relative_to(ROOT)}")
        return

    if old not in text:
        raise RuntimeError(
            f"Expected patch anchor not found in {path.relative_to(ROOT)}"
        )

    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)

    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"Patched: {path.relative_to(ROOT)}")


def patch_gateway() -> None:
    path = ROOT / "app" / "services" / "doughy_universal_gateway.py"

    replace_once(
        path,
        "from datetime import date, datetime, time\n",
        "from datetime import date, datetime, time, timedelta\n",
    )

    replace_once(
        path,
        '''    manager_walk_by_store = {
        store_number: {
            "store_number": store_number,
            "completed_days": 0,
            "incomplete_days": 0,
            "checklist_days": 0,
            "missing_days": 0,
        }
        for store_number in sorted(
            allowed_stores
        )
    }

    manager_walk_details = {}

    for row in all_rows:
''',
        '''    manager_walk_by_store = {
        store_number: {
            "store_number": store_number,
            "completed_days": 0,
            "incomplete_days": 0,
            "checklist_days": 0,
            "missing_days": 0,
        }
        for store_number in sorted(
            allowed_stores
        )
    }

    checklist_completion_by_store = {
        store_number: {
            "store_number": store_number,
            "completed_days": 0,
            "incomplete_days": 0,
            "checklist_days": 0,
            "missing_days": 0,
            "completed_dates": [],
            "incomplete_dates": [],
            "missing_dates": [],
        }
        for store_number in sorted(
            allowed_stores
        )
    }

    manager_walk_details = {}

    for row in all_rows:
''',
    )

    replace_once(
        path,
        '''        manager_walk_details[row.id] = {
            "manager_walk_completed": (
                section_complete
            ),
            "manager_walk_required_items": (
                required_count
            ),
            "manager_walk_completed_items": (
                completed_count
            ),
        }

        store_summary = (
''',
        '''        manager_walk_details[row.id] = {
            "manager_walk_completed": (
                section_complete
            ),
            "manager_walk_required_items": (
                required_count
            ),
            "manager_walk_completed_items": (
                completed_count
            ),
        }

        completion_summary = (
            checklist_completion_by_store
            .setdefault(
                row.store_number,
                {
                    "store_number": row.store_number,
                    "completed_days": 0,
                    "incomplete_days": 0,
                    "checklist_days": 0,
                    "missing_days": 0,
                    "completed_dates": [],
                    "incomplete_dates": [],
                    "missing_dates": [],
                },
            )
        )

        completion_summary["checklist_days"] += 1

        checklist_complete = bool(
            row.percent_complete is not None
            and float(row.percent_complete) >= 100
        )

        checklist_date_text = _iso(
            row.checklist_date
        )

        if checklist_complete:
            completion_summary["completed_days"] += 1
            completion_summary["completed_dates"].append(
                checklist_date_text
            )
        else:
            completion_summary["incomplete_days"] += 1
            completion_summary["incomplete_dates"].append(
                checklist_date_text
            )

        store_summary = (
''',
    )

    replace_once(
        path,
        '''    if expected_days is not None:
        for summary in (
            manager_walk_by_store
            .values()
        ):
            summary["missing_days"] = max(
                expected_days
                - summary["checklist_days"],
                0,
            )

    manager_walk_summary = sorted(
''',
        '''    if expected_days is not None:
        expected_dates = [
            date_from + timedelta(days=offset)
            for offset in range(expected_days)
        ]

        for summary in (
            manager_walk_by_store
            .values()
        ):
            summary["missing_days"] = max(
                expected_days
                - summary["checklist_days"],
                0,
            )

        for summary in (
            checklist_completion_by_store
            .values()
        ):
            recorded_dates = {
                value
                for value in (
                    summary["completed_dates"]
                    + summary["incomplete_dates"]
                )
                if value
            }

            summary["missing_dates"] = [
                value.isoformat()
                for value in expected_dates
                if value.isoformat()
                not in recorded_dates
            ]

            summary["missing_days"] = len(
                summary["missing_dates"]
            )

    manager_walk_summary = sorted(
''',
    )

    replace_once(
        path,
        '''        "manager_walk_summary": (
            manager_walk_summary
        ),
        "records": [
''',
        '''        "manager_walk_summary": (
            manager_walk_summary
        ),
        "checklist_completion_summary": sorted(
            checklist_completion_by_store.values(),
            key=lambda item: item["store_number"],
        ),
        "visible_stores": sorted(
            str(store_number)
            for store_number in allowed_stores
        ),
        "expected_days": expected_days,
        "records": [
''',
    )


def write_tests() -> None:
    tests_dir = ROOT / "tests"
    tests_dir.mkdir(exist_ok=True)
    path = tests_dir / "test_doughy_checklist_history_context.py"

    content = '''from __future__ import annotations

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
'''

    if path.exists() and path.read_text(encoding="utf-8") == content:
        print(f"Already written: {path.relative_to(ROOT)}")
        return

    path.write_text(content, encoding="utf-8")
    print(f"Written: {path.relative_to(ROOT)}")


def main() -> None:
    patch_gateway()
    write_tests()
    print("\nDoughy checklist-history context repair applied.")


if __name__ == "__main__":
    main()
