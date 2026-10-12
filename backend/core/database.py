"""SQLAlchemy engine, session factory and declarative base.

Session *lifecycle* (the `get_db` dependency) lives in `core.deps`; this module
only owns the connection itself so it can be imported by models without pulling
in FastAPI.
"""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

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


def track_file(db: Session, path: str) -> None:
    """Remove new files if their owning transaction fails."""
    db.info.setdefault("pending_files", []).append(path)


def discard_pending_files(db: Session) -> None:
    for path in db.info.pop("pending_files", []):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


@event.listens_for(Session, "after_commit")
def _files_committed(db):
    db.info.pop("pending_files", None)


@event.listens_for(Session, "after_rollback")
def _files_rolled_back(db):
    discard_pending_files(db)


class Base(DeclarativeBase):
    pass
