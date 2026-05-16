"""
Database connection and session management
"""
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from backend.config import get_settings
import logging

logger = logging.getLogger(__name__)

# Create base class for models
Base = declarative_base()

# Create engine
settings = get_settings()
database_url = settings.database_url
is_sqlite = database_url.strip().lower().startswith("sqlite")

engine_kwargs = {
    "pool_pre_ping": True,  # Verify connections before using
    "echo": settings.debug,  # Log SQL queries in debug mode
}
if is_sqlite:
    # SQLite concurrency tuning for API polling during background writes.
    engine_kwargs["connect_args"] = {
        "check_same_thread": False,
        "timeout": 30,
    }

engine = create_engine(database_url, **engine_kwargs)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=30000;")
            cursor.execute("PRAGMA foreign_keys=ON;")
        finally:
            cursor.close()

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """
    Dependency function to get database session
    Yields a session and closes it after use
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables and apply lightweight column migrations."""
    logger.info("Initializing database tables...")
    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()
    logger.info("Database tables created successfully")


def _apply_lightweight_migrations() -> None:
    """Add columns that exist on the model but not yet on the live table.

    Base.metadata.create_all() only creates missing TABLES, never adds new
    COLUMNS to an existing table. We patch that here with a tiny inspector-
    driven loop so the schema can evolve without alembic for one-off adds.

    Add new (table, column_name, column_ddl) tuples to MIGRATIONS below.
    column_ddl must be valid for SQLite AND PostgreSQL (the two backends we
    support).
    """
    MIGRATIONS = [
        ("exam_submissions", "template_id", "VARCHAR(100)"),
    ]
    insp = inspect(engine)
    for table, column, ddl in MIGRATIONS:
        if table not in insp.get_table_names():
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        if column in existing:
            continue
        stmt = f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
        logger.info("Applying migration: %s", stmt)
        with engine.begin() as conn:
            conn.execute(text(stmt))


def drop_db():
    """Drop all database tables (use with caution!)"""
    logger.warning("Dropping all database tables...")
    Base.metadata.drop_all(bind=engine)
    logger.warning("All database tables dropped")
