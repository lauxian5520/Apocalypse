"""SQLAlchemy engine, session factory and declarative base.

Session *lifecycle* (the `get_db` dependency) lives in `core.deps`; this module
only owns the connection itself so it can be imported by models without pulling
in FastAPI.
"""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from core.config import get_settings

settings = get_settings()

connect_args: dict = {}
if settings.db_type.lower() == "sqlite":
    connect_args = {"check_same_thread": False}
    os.makedirs(os.path.dirname(settings.sqlite_path), exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=False,
    pool_pre_ping=True,
)

if settings.db_type.lower() == "sqlite":
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")   # better concurrent reads
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass
