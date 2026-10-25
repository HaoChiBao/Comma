from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base

_engine = None
_Session = None


def configure(database_url: str) -> None:
    global _engine, _Session
    kwargs = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if database_url.endswith(":memory:"):
            kwargs["poolclass"] = StaticPool
    elif database_url.startswith("postgres"):
        _prepare_postgres_schema(database_url)
        for table in Base.metadata.tables.values():
            table.schema = "worldstate"
    _engine = create_engine(database_url, future=True, **kwargs)
    _Session = sessionmaker(_engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(_engine)


def _prepare_postgres_schema(database_url: str) -> None:
    bootstrap = create_engine(database_url, future=True)
    with bootstrap.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS worldstate"))
        ready = conn.execute(text("SELECT to_regclass('worldstate.organizations')")).scalar()
        populated = False
        if ready:
            populated = bool(conn.execute(text("SELECT 1 FROM worldstate.organizations LIMIT 1")).scalar())
        if not populated:
            conn.execute(text("DROP SCHEMA IF EXISTS worldstate CASCADE"))
            conn.execute(text("CREATE SCHEMA worldstate"))
    bootstrap.dispose()


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
