"""Lightweight, idempotent schema upkeep for existing databases.

This project has no Alembic history: tables are created from the ORM metadata
and columns added later are back-filled here. Keeping it out of
`models/__init__.py` means importing a model never runs DDL as a side effect.
"""
import logging

from sqlalchemy import inspect, text

from core.database import Base, engine

logger = logging.getLogger(__name__)

# table -> column -> DDL type used when the column is missing
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "memos": {
        "location": "VARCHAR(120)",
        "is_anonymous": "BOOLEAN DEFAULT 0 NOT NULL",
    },
    "users": {
        "is_disabled": "BOOLEAN DEFAULT 0 NOT NULL",
    },
    "comments": {
        "image_url": "VARCHAR(500)",
    },
    "messages": {
        "image_url": "VARCHAR(500)",
        "attachment_url": "VARCHAR(500)",
        "attachment_type": "VARCHAR(120)",
        "attachment_name": "VARCHAR(255)",
    },
}


def init_database() -> None:
    """Create missing tables, then add any missing columns."""
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table, columns in _ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table)}
        for column, ddl_type in columns.items():
            if column in present:
                continue
            logger.info("[migrate] %s: adding column %s", table, column)
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
