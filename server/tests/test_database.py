"""PostgreSQL integration checks, isolated in a temporary schema."""

from datetime import date

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from app import database
from app.models import EntityFile, File, Report, ReportEntityCounter, Requirement, VerificationResult


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
        session.add(Requirement(report_id=report_id, text="Проверить", source="llm",
                                json_data={"kind": "assertion", "condition": None, "ids_related": [0]}))
        session.add(VerificationResult(report_id=report_id, path=f"{report_id}/verification/report.json"))
        session.add(EntityFile(report_id=report_id, entity_id=0, file_id=1))
        session.add(ReportEntityCounter(report_id=report_id, next_entity_id=7))

    database.initialize_database()

    with database.session_scope() as session:
        report = session.get(Report, report_id)
        assert report.name == "Тестовый отчёт"
        assert report.stage == "documents"
        assert report.created_at.tzinfo is not None
        file = session.scalar(select(File))
        assert (file.version, file.status, file.hand_edited) == (1, "ocr_queued", False)
        assert session.get(EntityFile, (report_id, 0)).file_id == file.file_id
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 7
        requirement = session.scalar(select(Requirement))
        assert requirement.json_data["ids_related"] == [0]
        assert requirement.text == "Проверить"
        assert requirement.source == "llm"
        assert requirement.json_data["condition"] is None
        assert "verdict" not in Requirement.__table__.c
        assert session.get(VerificationResult, report_id).path == f"{report_id}/verification/report.json"
        requirement.text = "Пояснение"

    with database.session_scope() as session:
        assert session.scalar(select(Requirement)).text == "Пояснение"


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
        session.add(Requirement(report_id=report_id, json_data={"kind": "formula", "equation": "#1 == 2"}))
        session.add(VerificationResult(report_id=report_id, path="result.json"))
        session.add(EntityFile(report_id=report_id, entity_id=1, file_id=1))
        session.add(ReportEntityCounter(report_id=report_id))

    with pytest.raises(IntegrityError):
        with database.session_scope() as session:
            session.add(File(**fields, version=2))

    with database.session_scope() as session:
        session.execute(delete(Report).where(Report.id == report_id))
    with database.session_scope() as session:
        for model in (File, Requirement, EntityFile, ReportEntityCounter, VerificationResult):
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


def test_one_verification_result_per_report(db):
    with database.session_scope() as session:
        report = Report(name="Verification result")
        session.add(report)
        session.flush()
        report_id = report.id
        session.add(VerificationResult(report_id=report_id, path="first.json"))
    with pytest.raises(IntegrityError):
        with database.session_scope() as session:
            session.add(VerificationResult(report_id=report_id, path="second.json"))


def test_report_entity_counters_default_to_zero_and_are_isolated(db):
    with database.session_scope() as session:
        first = Report(name="First report")
        second = Report(name="Second report")
        session.add_all([first, second])
        session.flush()
        first_id, second_id = first.id, second.id
        session.add_all([
            ReportEntityCounter(report_id=first_id),
            ReportEntityCounter(report_id=second_id),
        ])

    with database.session_scope() as session:
        first_counter = session.get(ReportEntityCounter, first_id)
        assert first_counter.next_entity_id == 0
        assert session.get(ReportEntityCounter, second_id).next_entity_id == 0
        first_counter.next_entity_id = 12

    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, first_id).next_entity_id == 12
        assert session.get(ReportEntityCounter, second_id).next_entity_id == 0
        session.execute(delete(Report).where(Report.id == first_id))

    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, first_id) is None
        assert session.get(ReportEntityCounter, second_id).next_entity_id == 0


@pytest.mark.parametrize("next_entity_id", [-1, None])
def test_database_rejects_invalid_report_entity_counters(db, next_entity_id):
    with pytest.raises(IntegrityError):
        with database.session_scope() as session:
            report = Report(name="Invalid counter")
            session.add(report)
            session.flush()
            session.execute(ReportEntityCounter.__table__.insert().values(
                report_id=report.id, next_entity_id=next_entity_id,
            ))


def test_report_entity_counter_requires_existing_report(db):
    with pytest.raises(IntegrityError):
        with database.session_scope() as session:
            session.add(ReportEntityCounter(report_id=1))


def test_one_entity_counter_per_report(db):
    with database.session_scope() as session:
        report = Report(name="Entity counter")
        session.add(report)
        session.flush()
        report_id = report.id
        session.add(ReportEntityCounter(report_id=report_id))

    with pytest.raises(IntegrityError):
        with database.session_scope() as session:
            session.add(ReportEntityCounter(report_id=report_id, next_entity_id=1))
