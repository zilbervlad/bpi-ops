from __future__ import annotations

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
