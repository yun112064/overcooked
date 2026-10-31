from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def create_database(database_url: str | None = None):
    url = database_url or os.getenv(
        "MOCK_STORE_DATABASE_URL", "sqlite:///./mock_store.sqlite3"
    )
    options = {"future": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if url.endswith(":memory:"):
            options["poolclass"] = StaticPool
    engine = create_engine(url, **options)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
