#!/usr/bin/env python3
"""Eliminate N+1 checklist-item queries in Doughy checklist history."""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BACKUP_SUFFIX = ".backup-before-doughy-checklist-history-performance-20260804"


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
        "from typing import Any\n",
        "from typing import Any\n\nfrom sqlalchemy.orm import selectinload\n",
    )

    replace_once(
        path,
        '''    query = DailyChecklist.query

    if store:
''',
        '''    query = DailyChecklist.query.options(
        selectinload(DailyChecklist.items)
    )

    if store:
''',
    )


def write_tests() -> None:
    tests_dir = ROOT / "tests"
    tests_dir.mkdir(exist_ok=True)
    path = tests_dir / "test_doughy_checklist_history_performance.py"

    content = '''from __future__ import annotations

import inspect
import unittest

from app.services.doughy_universal_gateway import _checklist_history


class DoughyChecklistHistoryPerformanceTests(unittest.TestCase):
    def test_history_eager_loads_checklist_items(self):
        source = inspect.getsource(_checklist_history)

        self.assertIn(
            "selectinload(DailyChecklist.items)",
            source,
        )


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
    print("\nDoughy checklist-history performance repair applied.")


if __name__ == "__main__":
    main()
