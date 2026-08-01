import re
from collections import defaultdict

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app import create_app
from app.extensions import db
from app.models import User
from app.auth.routes import sync_user_to_bpi_connect

PROTECTED_IDS = {1, 14}
PROTECTED_ROLES = {"admin", "store"}


def valid_email(value):
    value = str(value or "").strip().lower()
    return value if "@" in value and "." in value.split("@")[-1] else ""


app = create_app()

with app.app_context():
    groups = defaultdict(list)
    for user in User.query.order_by(User.id.asc()).all():
        email = valid_email(user.email)
        if email:
            groups[email].append(user)

    refs = []
    inspector = inspect(db.engine)
    for table in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(table):
            if fk.get("referred_table") == "users":
                cols = fk.get("constrained_columns") or []
                if len(cols) == 1:
                    refs.append((table, cols[0]))

    candidates = []
    for email, rows in groups.items():
        if len(rows) < 2:
            continue
        rows = sorted(rows, key=lambda u: u.id)
        keep = rows[-1]
        if keep.id in PROTECTED_IDS or keep.role in PROTECTED_ROLES or not keep.is_active:
            continue
        remove = [
            old for old in rows[:-1]
            if old.id not in PROTECTED_IDS
            and old.role not in PROTECTED_ROLES
            and old.store_number == keep.store_number
            and old.role == keep.role
        ]
        if remove:
            candidates.append((email, keep, remove))

    print("\nSAFE DUPLICATE MERGE CANDIDATES\n")
    for email, keep, remove in candidates:
        print(f"KEEP {keep.id}: {keep.name} | {keep.role} | store {keep.store_number} | {email}")
        for old in remove:
            print(f"  MERGE {old.id}: {old.name} | active={old.is_active}")

    print(f"\nGroups ready: {len(candidates)}")
    if input("Type MERGE to continue: ").strip() != "MERGE":
        print("Cancelled. No changes made.")
        raise SystemExit

    success = 0
    skipped = 0

    for _, keep, remove in candidates:
        for old in remove:
            moved = 0
            try:
                with db.session.begin_nested():
                    for table, column in refs:
                        result = db.session.execute(
                            text(
                                f'UPDATE "{table}" SET "{column}" = :keep_id '
                                f'WHERE "{column}" = :old_id'
                            ),
                            {"keep_id": keep.id, "old_id": old.id},
                        )
                        moved += result.rowcount or 0
                    old.is_active = False
                    db.session.flush()

                db.session.commit()
                sync = sync_user_to_bpi_connect(old, send_invite=False)
                sync_note = "Connect synced" if sync.get("success") else f"Connect sync failed: {sync.get('error')}"
                print(f"SUCCESS {old.id} -> {keep.id} | moved {moved} | {sync_note}")
                success += 1

            except (SQLAlchemyError, Exception) as exc:
                db.session.rollback()
                print(f"SKIPPED {old.id} -> {keep.id} | {str(exc).splitlines()[0][:180]}")
                skipped += 1

    print(f"\nDone. Success: {success} | Skipped: {skipped}")
    print("Protected IDs 1 and 14 were not touched.")
