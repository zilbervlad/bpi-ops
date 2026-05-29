from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import MaintenanceTicket


def add_column_sql(table_name, column_name, column_sql, dialect_name):
    if dialect_name == "postgresql":
        return f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS {column_name} {column_sql}'
    return f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}'


app = create_app()

with app.app_context():
    table_name = MaintenanceTicket.__table__.name
    dialect_name = db.engine.dialect.name

    inspector = inspect(db.engine)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}

    columns_to_add = [
        ("assigned_to", "VARCHAR(120)"),
        ("scheduled_date", "DATE"),
        ("scheduled_time", "TIME"),
        ("estimated_minutes", "INTEGER"),
        ("priority", "VARCHAR(30)"),
    ]

    added = []

    with db.engine.begin() as connection:
        for column_name, column_sql in columns_to_add:
            if column_name in existing_columns:
                continue

            connection.execute(text(add_column_sql(table_name, column_name, column_sql, dialect_name)))
            added.append(column_name)

        if "priority" in added or "priority" in existing_columns:
            try:
                connection.execute(text(f"UPDATE {table_name} SET priority = 'normal' WHERE priority IS NULL OR priority = ''"))
            except Exception:
                pass

    print("Added columns:", added if added else "none - already up to date")
