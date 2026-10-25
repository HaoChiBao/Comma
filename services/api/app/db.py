from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base

_engine = None
_Session = None


def configure(database_url: str) -> None:
    global _engine, _Session
    kwargs = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url.endswith(":memory:"):
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(database_url, future=True, **kwargs)
    if database_url.startswith("postgres"):

        @event.listens_for(_engine, "connect")
        def _worldstate_schema(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("CREATE SCHEMA IF NOT EXISTS worldstate")
            cursor.execute("SET search_path TO worldstate")
            cursor.close()

    _Session = sessionmaker(_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)


def engine():
    if _engine is None:
        raise RuntimeError("Database is not configured")
    return _engine


def session() -> Session:
    if _Session is None:
        raise RuntimeError("Database is not configured")
    return _Session()


def db_session() -> Generator[Session, None, None]:
    db = session()
    try:
        yield db
    finally:
        db.close()
