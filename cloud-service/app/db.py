from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import Settings


class Base(DeclarativeBase):
    pass


def build_database(settings: Settings):
    if not settings.database_url:
        return None, None
    if settings.database_url.startswith("sqlite:"):
        database_path = settings.database_url.removeprefix("sqlite:///")
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )
    else:
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=1,
            max_overflow=0,
            pool_timeout=5,
        )
    return engine, sessionmaker(bind=engine, expire_on_commit=False)

