from datetime import date, timedelta

from app import create_app
from app.checklist.routes import run_checklist_closeout


app = create_app()

with app.app_context():

    print("Running BPI Ops checklist closeout...")

    yesterday = date.today() - timedelta(days=1)

    result = run_checklist_closeout(yesterday)

    print("=== BPI OPS CHECKLIST CLOSEOUT ===")
    print("Closeout date:", result.get("closeout_date"))
    print("Exceptions created:", result.get("created_count"))
    print("Stores skipped:", result.get("skipped_count"))
    print("Skipped existing:", result.get("skipped_existing_count"))
    print("Skipped complete:", result.get("skipped_complete_count"))
    print("Not started:", result.get("not_started_count"))
    print("Archive shells created:", result.get("archived_shell_count"))
