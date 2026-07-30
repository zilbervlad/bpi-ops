import json
from pathlib import Path

from app.extensions import db
from app.models import MITLevelTemplate


def ensure_academy_seed_data():
    """Install the tracked STS curriculum when a new database is empty."""

    if MITLevelTemplate.query.count() > 0:
        return {
            "created": 0,
            "reason": "existing_curriculum",
        }

    seed_path = Path(__file__).with_name("sts_curriculum_seed.json")

    if not seed_path.exists():
        return {
            "created": 0,
            "reason": "seed_file_missing",
        }

    rows = json.loads(seed_path.read_text())

    created = 0

    for row in rows:
        db.session.add(
            MITLevelTemplate(
                level_number=int(row["level_number"]),
                category=row.get("category"),
                item_name=row["item_name"],
                item_description=row.get("item_description"),
                sort_order=int(row.get("sort_order") or 0),
                is_required=bool(row.get("is_required", True)),
                source_ref=row.get("source_ref"),
            )
        )
        created += 1

    db.session.commit()

    return {
        "created": created,
        "reason": "seeded",
    }
