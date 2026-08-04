from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app" / "services" / "doughy_universal_gateway.py"
TEST = ROOT / "tests" / "test_doughy_prep_live_context.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"Already patched: {label}")
        return text
    if old not in text:
        raise SystemExit(f"Could not find patch target: {label}")
    print(f"Patched: {label}")
    return text.replace(old, new, 1)


text = TARGET.read_text(encoding="utf-8")
text = replace_once(
    text,
    "    DailyPrep,\n",
    "    DailyPrep,\n    PrepTemplateItem,\n    today_et,\n",
    "PrepTemplateItem/today_et imports",
)

helpers = '''\n\n_PREP_WEEKDAY_FIELDS = {\n    "monday": "monday_build_to",\n    "tuesday": "tuesday_build_to",\n    "wednesday": "wednesday_build_to",\n    "thursday": "thursday_build_to",\n    "friday": "friday_build_to",\n    "saturday": "saturday_build_to",\n    "sunday": "sunday_build_to",\n}\n\n\ndef _prep_build_to_for_date(template: Any, prep_date: date) -> Any:\n    weekday_name = prep_date.strftime("%A").lower()\n    weekday_field = _PREP_WEEKDAY_FIELDS.get(weekday_name)\n    if weekday_field:\n        weekday_value = getattr(template, weekday_field, None)\n        if weekday_value:\n            return weekday_value\n    return getattr(template, "build_to", None)\n\n\ndef _synthetic_prep_rows(\n    *,\n    template_rows: list[Any],\n    store_numbers: set[str],\n    prep_date: date,\n    existing_store_numbers: set[str],\n) -> list[dict[str, Any]]:\n    weekday_name = prep_date.strftime("%A").lower()\n    grouped: dict[str, list[Any]] = {}\n\n    for template in template_rows:\n        store_number = str(getattr(template, "store_number", "") or "").strip()\n        if not store_number or store_number not in store_numbers:\n            continue\n        if not bool(getattr(template, weekday_name, False)):\n            continue\n        grouped.setdefault(store_number, []).append(template)\n\n    results = []\n    for store_number in sorted(store_numbers):\n        if store_number in existing_store_numbers:\n            continue\n        templates = grouped.get(store_number, [])\n        results.append({\n            "id": None,\n            "store_number": store_number,\n            "prep_date": prep_date.isoformat(),\n            "created_at": None,\n            "item_count": len(templates),\n            "completed_count": 0,\n            "percent_complete": 0,\n            "record_exists": False,\n            "status": "not_started" if templates else "no_active_template",\n            "incomplete_items": [\n                {\n                    "section_name": getattr(template, "section_name", None),\n                    "item_name": getattr(template, "item_name", None),\n                    "build_to": _prep_build_to_for_date(template, prep_date),\n                }\n                for template in templates[:30]\n            ],\n        })\n    return results\n'''

text = replace_once(
    text,
    "\n\ndef _prep_context(\n",
    helpers + "\n\ndef _prep_context(\n",
    "prep synthesis helpers",
)

start = text.index("def _prep_context(\n")
end = text.index("\n\ndef _checklist_history(\n", start)
new_function = '''def _prep_context(\n    *,\n    user_context: dict[str, Any],\n    store: str | None,\n    date_from: date | None,\n    date_to: date | None,\n    limit: int,\n) -> dict[str, Any]:\n    allowed_stores = visible_store_numbers(user_context)\n    target_stores = {store} if store else set(allowed_stores)\n\n    if store and store not in allowed_stores:\n        return {\n            "ok": False,\n            "error": "Store is outside the requester's visible scope.",\n        }\n\n    query = DailyPrep.query.filter(DailyPrep.store_number.in_(target_stores))\n    if date_from:\n        query = query.filter(DailyPrep.prep_date >= date_from)\n    if date_to:\n        query = query.filter(DailyPrep.prep_date <= date_to)\n\n    rows = (\n        query\n        .order_by(DailyPrep.prep_date.desc(), DailyPrep.id.desc())\n        .limit(limit)\n        .all()\n    )\n\n    results = []\n    for row in rows:\n        completed = sum(1 for item in row.items if item.is_completed)\n        results.append({\n            "id": row.id,\n            "store_number": row.store_number,\n            "prep_date": _iso(row.prep_date),\n            "created_at": _iso(row.created_at),\n            "item_count": len(row.items),\n            "completed_count": completed,\n            "percent_complete": round(\n                (completed / len(row.items) * 100) if row.items else 0,\n                1,\n            ),\n            "record_exists": True,\n            "status": (\n                "complete"\n                if row.items and completed == len(row.items)\n                else "in_progress"\n                if completed > 0\n                else "not_started"\n            ),\n            "incomplete_items": [\n                {\n                    "section_name": item.section_name,\n                    "item_name": item.item_name,\n                    "build_to": item.build_to,\n                }\n                for item in row.items\n                if not item.is_completed\n            ][:30],\n        })\n\n    synthesized = []\n    exact_date = date_from if date_from and date_from == date_to else None\n\n    # DailyPrep rows are created lazily when a store opens the Prep page.\n    # For today's read-only company status, synthesize missing stores from\n    # active templates so unopened pages do not disappear from Doughy.\n    if exact_date and exact_date == today_et():\n        existing_store_numbers = {\n            str(row.store_number)\n            for row in rows\n            if row.prep_date == exact_date\n        }\n        template_rows = (\n            PrepTemplateItem.query\n            .filter(\n                PrepTemplateItem.store_number.in_(target_stores),\n                PrepTemplateItem.is_active.is_(True),\n            )\n            .order_by(\n                PrepTemplateItem.store_number.asc(),\n                PrepTemplateItem.section_name.asc(),\n                PrepTemplateItem.sort_order.asc(),\n                PrepTemplateItem.id.asc(),\n            )\n            .all()\n        )\n        synthesized = _synthetic_prep_rows(\n            template_rows=template_rows,\n            store_numbers=target_stores,\n            prep_date=exact_date,\n            existing_store_numbers=existing_store_numbers,\n        )\n        results.extend(synthesized)\n\n    results.sort(\n        key=lambda item: (\n            str(item.get("prep_date") or ""),\n            str(item.get("store_number") or ""),\n        ),\n        reverse=True,\n    )\n\n    return {\n        "ok": True,\n        "module": "prep",\n        "count": len(results[:limit]),\n        "daily_preps": results[:limit],\n        "synthesized_count": len(synthesized),\n        "requested": {\n            "store": store,\n            "date_from": _iso(date_from),\n            "date_to": _iso(date_to),\n        },\n        "scope": {\n            "role": _role(user_context),\n            "visible_store_count": len(target_stores),\n        },\n    }\n'''

text = text[:start] + new_function + text[end:]
TARGET.write_text(text, encoding="utf-8")
print(f"Written: {TARGET.relative_to(ROOT)}")

TEST.parent.mkdir(parents=True, exist_ok=True)
TEST.write_text(
    '''from __future__ import annotations\n\nimport unittest\nfrom datetime import date\nfrom types import SimpleNamespace\n\nfrom app.services.doughy_universal_gateway import (\n    _prep_build_to_for_date,\n    _synthetic_prep_rows,\n)\n\n\nclass DoughyPrepLiveContextTests(unittest.TestCase):\n    def template(self, store, name, monday=True, monday_build=None):\n        return SimpleNamespace(\n            store_number=store,\n            section_name="Chicken / Wings / Boneless",\n            item_name=name,\n            build_to="4 bags",\n            monday_build_to=monday_build,\n            monday=monday,\n        )\n\n    def test_weekday_build_to_wins(self):\n        template = self.template("3219", "Wings", monday_build="6 bags")\n        self.assertEqual(\n            _prep_build_to_for_date(template, date(2026, 8, 3)),\n            "6 bags",\n        )\n\n    def test_missing_store_is_synthesized_not_started(self):\n        rows = _synthetic_prep_rows(\n            template_rows=[self.template("3219", "Wings")],\n            store_numbers={"3219", "3225"},\n            prep_date=date(2026, 8, 3),\n            existing_store_numbers={"3225"},\n        )\n        self.assertEqual(len(rows), 1)\n        self.assertEqual(rows[0]["store_number"], "3219")\n        self.assertEqual(rows[0]["status"], "not_started")\n        self.assertEqual(rows[0]["item_count"], 1)\n        self.assertFalse(rows[0]["record_exists"])\n\n    def test_existing_store_is_not_duplicated(self):\n        rows = _synthetic_prep_rows(\n            template_rows=[self.template("3219", "Wings")],\n            store_numbers={"3219"},\n            prep_date=date(2026, 8, 3),\n            existing_store_numbers={"3219"},\n        )\n        self.assertEqual(rows, [])\n\n    def test_no_template_is_reported_separately(self):\n        rows = _synthetic_prep_rows(\n            template_rows=[],\n            store_numbers={"3003"},\n            prep_date=date(2026, 8, 3),\n            existing_store_numbers=set(),\n        )\n        self.assertEqual(rows[0]["status"], "no_active_template")\n        self.assertEqual(rows[0]["item_count"], 0)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
    encoding="utf-8",
)
print(f"Written: {TEST.relative_to(ROOT)}")
print("Doughy Prep live-context repair applied.")
