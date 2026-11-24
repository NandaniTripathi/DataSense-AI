"""SQLAlchemy engine/session for the metadata database (datasets, chats)."""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def init_engine():
    global _engine, _SessionLocal
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(
        settings.metadata_db_url, connect_args={"check_same_thread": False}
    )
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    from app.db import models  # noqa: F401  (register tables)

    Base.metadata.create_all(_engine)
    return _engine


def get_db() -> Iterator[Session]:
    if _SessionLocal is None:
        init_engine()
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
