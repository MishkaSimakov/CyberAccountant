"""Database integration tests use disposable PostgreSQL schemas."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from app import database


@pytest.fixture
def db(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests.")
    engine = create_engine(url)
    schema = "test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_engine = engine.execution_options(schema_translate_map={None: schema})
    monkeypatch.setattr(database, "get_engine", lambda: scoped_engine)
    try:
        database.initialize_database()
        yield
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def llm_client(monkeypatch):
    """Use the real shared client with a fake single-attempt inference adapter."""
    from types import SimpleNamespace

    from processing.common.llm import ModelClient

    def unexpected(*args, **kwargs):
        pytest.fail("Unexpected model request; supply a fake response in the test")

    client = ModelClient(SimpleNamespace(generate=unexpected))
    monkeypatch.setattr("app.jobs.entities.create_model_client", lambda: client)
    monkeypatch.setattr("app.jobs.verification.create_model_client", lambda: client)
    return client
