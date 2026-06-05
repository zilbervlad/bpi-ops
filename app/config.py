import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"

INSTANCE_DIR.mkdir(exist_ok=True)


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

    database_url = os.getenv("DATABASE_URL", "").strip()

    if database_url:
        SQLALCHEMY_DATABASE_URI = database_url
    else:
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{INSTANCE_DIR / 'bpi_ops.db'}"

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Render/Postgres can occasionally recycle or drop SSL connections.
    # pool_pre_ping checks connections before using them.
    # pool_recycle prevents long-lived stale DB connections.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }