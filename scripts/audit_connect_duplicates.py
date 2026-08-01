import re
from collections import defaultdict

from app import create_app
from app.connect_admin.routes import fetch_connect_users


def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def phone(value):
    return re.sub(r"\D", "", str(value or ""))[-10:]


app = create_app()

with app.app_context():
    result = fetch_connect_users()
    if not result.get("connected"):
        print("CONNECT ERROR:", result.get("error"))
        raise SystemExit(1)

    users = result.get("users", []) or []
    active = [u for u in users if u.get("is_active")]

    groups = defaultdict(list)
    for user in active:
        email = norm(user.get("email"))
        number = phone(user.get("phone_number"))
        name = norm(user.get("name"))

        if email:
            groups[("email", email)].append(user)
        if len(number) == 10:
            groups[("phone", number)].append(user)
        if name:
            groups[("name", name)].append(user)

    seen = set()
    duplicates = []

    for (kind, key), rows in groups.items():
        ids = tuple(sorted(str(r.get("id") or r.get("bpi_ops_user_id") or "") for r in rows))
        if len(rows) < 2 or ids in seen:
            continue
        seen.add(ids)
        duplicates.append((kind, key, rows))

    print("\nACTIVE CONNECT DUPLICATES\n")
    if not duplicates:
        print("None found.")
        raise SystemExit

    for kind, key, rows in duplicates:
        print(f"MATCH BY {kind.upper()}: {key}")
        for row in sorted(rows, key=lambda r: int(r.get("id") or 0)):
            print(
                f"  ID={row.get('id')} | OPS_ID={row.get('bpi_ops_user_id')} | "
                f"{row.get('name')} | store={row.get('store_number')} | "
                f"email={row.get('email')} | phone={row.get('phone_number')} | "
                f"logged_in={row.get('has_logged_in')} | last_login={row.get('last_login_at')}"
            )
        print()

    print(f"Duplicate groups: {len(duplicates)}")
