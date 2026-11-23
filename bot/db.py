"""Database connection manager supporting MSSQL (SQL Server), SQLite, and PostgreSQL."""

import os
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from bot.models import Base

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "buffett_bot.db"


def get_database_url() -> str:
    """Read DATABASE_URL from environment or .env, normalizing postgres:// for Neon/Supabase and defaulting to SQLite."""
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith("DATABASE_URL=") and not s.startswith("#"):
                    db_url = s.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not db_url:
        DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        sqlite_posix = DEFAULT_SQLITE_PATH.as_posix()
        db_url = f"sqlite:///{sqlite_posix}"
    elif db_url.startswith("postgres://"):
        # SQLAlchemy 2.0 requires postgresql:// instead of legacy postgres://
        db_url = "postgresql+psycopg2://" + db_url[len("postgres://") :]
    elif db_url.startswith("postgresql://"):
        db_url = "postgresql+psycopg2://" + db_url[len("postgresql://") :]

    return db_url


def create_db_engine():
    db_url = get_database_url()
    connect_args = {}
    engine_kwargs = {"pool_pre_ping": True}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    else:
        # Neon / serverless PostgreSQL suspends idle connections after ~5 min; recycle cleanly at 4 min
        engine_kwargs["pool_recycle"] = 240
        engine_kwargs["pool_size"] = 5
        engine_kwargs["max_overflow"] = 5
    return create_engine(db_url, connect_args=connect_args, **engine_kwargs)


engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _ensure_column_migrations() -> None:
    """Idempotently add newly introduced columns to existing SQLite / MSSQL tables."""
    inspector = inspect(engine)
    if "users" in inspector.get_table_names():
        existing_cols = {c["name"].lower() for c in inspector.get_columns("users")}
        if "gemini_api_key_enc" not in existing_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN gemini_api_key_enc VARCHAR(512)"))


def init_db() -> str:
    """Create all tables if they do not exist, apply safe column migrations, and return the active DB URL (masked)."""
    Base.metadata.create_all(bind=engine)
    _ensure_column_migrations()
    url = get_database_url()
    if "@" in url:
        prefix, suffix = url.split("@", 1)
        scheme = prefix.split("://")[0]
        return f"{scheme}://***@{suffix}"
    return url


def get_session() -> Session:
    return SessionLocal()
