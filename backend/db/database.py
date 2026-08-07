"""
Database connection and session management
"""
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pathlib import Path
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


def init_db(bind=None):
    """Initialize schema and idempotently synchronize bundled answer keys."""
    # Ensure every model has registered its table with Base even when this
    # module is imported directly rather than through the API routes.
    from backend.db import models as _models  # noqa: F401

    target_engine = bind or engine
    logger.info("Initializing database tables...")
    Base.metadata.create_all(bind=target_engine)
    _apply_lightweight_migrations(target_engine)
    _synchronize_bundled_answer_keys(target_engine)
    logger.info("Database tables created successfully")


def _synchronize_bundled_answer_keys(target_engine) -> None:
    """Activate bundled keys and recover stale runs on the supplied DB bind."""
    from backend.services.marking_service import ManifestRegistry, synchronize_answer_keys
    from backend.services.marking_workflow import recover_stale_marking_runs
    from backend.services.template_service import TemplateRegistry

    root = Path(__file__).resolve().parents[2]
    registry = ManifestRegistry(
        root / "answer_keys",
        root / "answer_keys",
        TemplateRegistry(root / "backend" / "templates"),
    )
    startup_session = sessionmaker(
        autocommit=False, autoflush=False, bind=target_engine
    )
    with startup_session() as db:
        synchronize_answer_keys(db, registry)
        db.commit()
        recover_stale_marking_runs(db)


def _apply_lightweight_migrations(bind=None) -> None:
    """Add columns that exist on the model but not yet on the live table.

    Base.metadata.create_all() only creates missing TABLES, never adds new
    COLUMNS to an existing table. We patch that here with a tiny inspector-
    driven loop so the schema can evolve without alembic for one-off adds.

    Add new (table, column_name, column_ddl) tuples to MIGRATIONS below.
    column_ddl must be valid for SQLite AND PostgreSQL (the two backends we
    support).
    """
    target_engine = bind or engine
    migrations = [
        ("exam_submissions", "template_id", "VARCHAR(100)"),
        ("answer_keys", "template_id", "VARCHAR(100)"),
        ("answer_keys", "version", "INTEGER DEFAULT 1"),
        ("answer_keys", "source_filename", "VARCHAR(255)"),
        ("answer_keys", "source_sha256", "VARCHAR(64)"),
        ("answer_keys", "total_marks", "INTEGER"),
        ("answer_keys", "question_spec", "JSON"),
        ("answer_keys", "is_active", "BOOLEAN DEFAULT FALSE"),
        ("candidate_results", "template_id", "VARCHAR(100)"),
        ("candidate_results", "detection", "JSON"),
        ("candidate_markings", "answer_key_id", "INTEGER"),
    ]
    insp = inspect(target_engine)
    for table, column, ddl in migrations:
        if table not in insp.get_table_names():
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        if column in existing:
            continue
        stmt = f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
        logger.info("Applying migration: %s", stmt)
        with target_engine.begin() as conn:
            conn.execute(text(stmt))

    if "answer_keys" in inspect(target_engine).get_table_names():
        answer_key_inspector = inspect(target_engine)
        existing_indexes = {
            item["name"]
            for item in answer_key_inspector.get_indexes("answer_keys")
            if item.get("name")
        }
        active_predicate = (
            "is_active = 1"
            if target_engine.dialect.name == "sqlite"
            else "is_active IS TRUE"
        )
        index_statements = (
            (
                "ix_answer_keys_template_id",
                "CREATE INDEX IF NOT EXISTS ix_answer_keys_template_id "
                "ON answer_keys (template_id)",
            ),
            (
                "ix_answer_keys_template_active",
                "CREATE INDEX IF NOT EXISTS ix_answer_keys_template_active "
                "ON answer_keys (template_id, is_active)",
            ),
            (
                "uq_answer_keys_template_version",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_answer_keys_template_version "
                "ON answer_keys (template_id, version)",
            ),
            (
                "uq_answer_keys_one_active_template",
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_answer_keys_one_active_template "
                f"ON answer_keys (template_id) WHERE {active_predicate}",
            ),
        )
        missing_indexes = [
            stmt for name, stmt in index_statements if name not in existing_indexes
        ]
        if missing_indexes:
            with target_engine.begin() as conn:
                if "uq_answer_keys_one_active_template" not in existing_indexes:
                    conn.execute(
                        text(
                            "UPDATE answer_keys SET is_active = FALSE "
                            "WHERE id IN ("
                            "SELECT stale.id FROM answer_keys AS stale "
                            "JOIN answer_keys AS newer "
                            "ON newer.template_id = stale.template_id "
                            "AND newer.is_active = TRUE "
                            "AND ("
                            "COALESCE(newer.version, 0) > COALESCE(stale.version, 0) "
                            "OR (COALESCE(newer.version, 0) = COALESCE(stale.version, 0) "
                            "AND newer.id > stale.id)"
                            ") "
                            "WHERE stale.is_active = TRUE"
                            ")"
                        )
                    )
                for stmt in missing_indexes:
                    conn.execute(text(stmt))

    if "marking_runs" in inspect(target_engine).get_table_names():
        marking_inspector = inspect(target_engine)
        marking_indexes = {
            item["name"]
            for item in marking_inspector.get_indexes("marking_runs")
            if item.get("name")
        }
        processing_index = "uq_marking_runs_one_processing_submission"
        if processing_index not in marking_indexes:
            with target_engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE marking_runs "
                        "SET status = 'failed', "
                        "completed_at = CURRENT_TIMESTAMP, "
                        "error_message = 'Marking interrupted; retry is available' "
                        "WHERE id IN ("
                        "SELECT stale.id FROM marking_runs AS stale "
                        "JOIN marking_runs AS newer "
                        "ON newer.submission_id = stale.submission_id "
                        "AND newer.status = 'processing' "
                        "AND newer.id > stale.id "
                        "WHERE stale.status = 'processing'"
                        ")"
                    )
                )
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS "
                        "uq_marking_runs_one_processing_submission "
                        "ON marking_runs (submission_id) "
                        "WHERE status = 'processing'"
                    )
                )


def drop_db():
    """Drop all database tables (use with caution!)"""
    logger.warning("Dropping all database tables...")
    Base.metadata.drop_all(bind=engine)
    logger.warning("All database tables dropped")
