"""PostgreSQL integration checks, isolated in a temporary schema."""

from datetime import date

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DataError, IntegrityError

from app import database
from app.models import EntityFile, File, Report, Requirement
from app.models.requirement import RequirementVerdict


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
        session.add(Requirement(report_id=report_id, verdict=RequirementVerdict.UNVERIFIABLE,
                                json_data={"evidence": "Проверить", "ids_related": [0]}))
        session.add(EntityFile(report_id=report_id, entity_id=0, file_id=1))

    database.initialize_database()

    with database.session_scope() as session:
        report = session.get(Report, report_id)
        assert report.name == "Тестовый отчёт"
        assert report.stage == "documents"
        assert report.created_at.tzinfo is not None
        file = session.scalar(select(File))
        assert (file.version, file.status, file.hand_edited) == (1, "queued", False)
        assert session.get(EntityFile, (report_id, 0)).file_id == file.file_id
        requirement = session.scalar(select(Requirement))
        assert requirement.json_data["ids_related"] == [0]
        assert requirement.verdict == RequirementVerdict.UNVERIFIABLE
        requirement.verdict = RequirementVerdict.FALSE

    with database.session_scope() as session:
        assert session.scalar(select(Requirement)).verdict == RequirementVerdict.FALSE


def test_initialization_adds_upload_sequence_to_existing_tables(db):
    sequence = File.__table__.c.file_id.default
    sequence.drop(database.get_engine())
    database.initialize_database()
    with database.session_scope() as session:
        report = Report(name="Upload sequence")
        session.add(report)
        session.flush()
        file = File(report_id=report.id, name="file.pdf", path="original.pdf", hash="a" * 64,
                    role="supporting", size=1, content_type="application/pdf")
        session.add(file)
        session.flush()
        assert file.file_id > 0


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
        session.add(Requirement(report_id=report_id, verdict=RequirementVerdict.TRUE,
                                json_data={"equation": "#1 == 2"}))
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


def test_database_rejects_negative_entity_ids(db):
    with pytest.raises(IntegrityError):
        with database.session_scope() as session:
            report = Report(name="Invalid entity")
            session.add(report)
            session.flush()
            session.add(EntityFile(report_id=report.id, entity_id=-1, file_id=1))


@pytest.mark.parametrize("verdict", list(RequirementVerdict))
def test_requirement_verdict_round_trip(db, verdict):
    with database.session_scope() as session:
        report = Report(name="Requirement verdict")
        session.add(report)
        session.flush()
        session.add(Requirement(report_id=report.id, verdict=verdict, json_data={"equation": "#0 == 2"}))

    with database.session_scope() as session:
        assert session.scalar(select(Requirement)).verdict is verdict


def test_database_rejects_unknown_requirement_verdict(db):
    with pytest.raises(DataError):
        with database.session_scope() as session:
            report = Report(name="Invalid verdict")
            session.add(report)
            session.flush()
            session.execute(Requirement.__table__.insert().values({
                "report_id": report.id, "verdict": text("'unknown'"), "json": {},
            }))
