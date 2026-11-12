"""PostgreSQL integration checks, isolated in a temporary schema."""

import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError

from app import database
from app.models import EntityFile, File, Report, Requirement


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


def test_initialization_preserves_data_and_round_trips_models(db):
    with database.session_scope() as session:
        report = Report(name="Тестовый отчёт")
        session.add(report)
        session.flush()
        report_id = report.id
        session.add(File(
            file_id=1, report_id=report_id, name="акт.pdf", path="originals/акт.pdf",
            hash="a" * 64, role="supporting", size=42, content_type="application/pdf",
        ))
        session.add(Requirement(report_id=report_id, json_data={"evidence": "Проверить", "ids_related": [1]}))
        session.add(EntityFile(report_id=report_id, entity_id=1, file_id=1))

    database.initialize_database()

    with database.session_scope() as session:
        report = session.get(Report, report_id)
        assert report.name == "Тестовый отчёт"
        assert report.stage == "documents"
        assert report.created_at.tzinfo is not None
        file = session.scalar(select(File))
        assert (file.version, file.status, file.hand_edited) == (1, "queued", False)
        assert session.get(EntityFile, (report_id, 1)).file_id == file.file_id
        requirement = session.scalar(select(Requirement))
        assert requirement.json_data["ids_related"] == [1]
        requirement.json_data["verdict"] = False

    with database.session_scope() as session:
        assert session.scalar(select(Requirement)).json_data["verdict"] is False


def test_transaction_rolls_back_on_error(db):
    with pytest.raises(RuntimeError, match="abort"):
        with database.session_scope() as session:
            session.add(Report(name="Must roll back"))
            session.flush()
            raise RuntimeError("abort")
    with database.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Report)) == 0


def test_file_revision_uniqueness_and_report_cascade(db):
    with database.session_scope() as session:
        report = Report(name="Revision test")
        session.add(report)
        session.flush()
        report_id = report.id
        fields = dict(
            file_id=1, report_id=report_id, name="file.pdf", path="original.pdf",
            hash="a" * 64, role="supporting", size=42, content_type="application/pdf",
        )
        session.add_all([File(**fields, version=1), File(**fields, version=2, hand_edited=True)])
        session.add(Requirement(report_id=report_id, json_data={"equation": "#1 == 2"}))
        session.add(EntityFile(report_id=report_id, entity_id=1, file_id=1))

    with pytest.raises(IntegrityError):
        with database.session_scope() as session:
            session.add(File(**fields, version=2))

    with database.session_scope() as session:
        session.execute(delete(Report).where(Report.id == report_id))
    with database.session_scope() as session:
        for model in (File, Requirement, EntityFile):
            assert session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("fields", [
    {"period_start": date(2026, 1, 1)},
    {"period_start": date(2026, 2, 1), "period_end": date(2026, 1, 1)},
    {"stage": "unknown"},
])
def test_database_rejects_invalid_report_state(db, fields):
    with pytest.raises(IntegrityError):
        with database.session_scope() as session:
            session.add(Report(name="Invalid report", **fields))
