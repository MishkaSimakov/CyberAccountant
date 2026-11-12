"""Shared synchronous PostgreSQL sessions for API handlers and workers."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


def database_url() -> URL:
    load_dotenv()
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is not set. Configure it in the environment or the project .env file.")
    url = make_url(value)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("DATABASE_URL must use postgresql:// or postgresql+psycopg://.")
    return url.set(drivername="postgresql+psycopg")


@lru_cache
def get_engine() -> Engine:
    """Create a connection pool lazily, once per process."""
    return create_engine(database_url(), pool_pre_ping=True)


def initialize_database() -> None:
    """Create missing tables from the models; existing tables are left intact."""
    from . import models

    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success; roll back on failure and always close the session.

    Keep OCR/LLM calls outside this scope. Each worker/thread needs its own
    session; synchronous database access belongs in synchronous API handlers.
    """
    with Session(get_engine(), expire_on_commit=False) as session, session.begin():
        yield session


def get_session() -> Iterator[Session]:
    """FastAPI dependency using the same transaction lifecycle as workers."""
    with session_scope() as session:
        yield session
