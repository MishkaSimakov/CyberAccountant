"""Persist reports and expose their current document-processing progress."""

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..logging import create_report_log
from .. import storage
from ..database import session_scope
from ..models import File, Report
from ..schemas.documents import Document
from ..schemas.reports import Report as ReportResponse, ReportCreate
from ..target_row import find_target_row_spans
from . import ServiceError


def find_report(session: Session, report_id: int, *, lock: bool = False) -> Report:
    query = select(Report).where(Report.id == report_id)
    if lock:
        query = query.with_for_update()
    report = session.scalar(query)
    if report is None:
        raise ServiceError(404, "Отчёт не найден.")
    return report


def report_documents(session: Session, report_id: int) -> list[File]:
    latest = (select(File.file_id, func.max(File.version).label("version"))
              .where(File.report_id == report_id).group_by(File.file_id).subquery())
    return list(session.scalars(
        select(File).join(latest, (File.file_id == latest.c.file_id) & (File.version == latest.c.version))
        .order_by(File.file_id)
    ))


def document_response(file: File) -> Document:
    return Document(
        id=file.file_id, name=file.name, size=file.size, content_type=file.content_type,
        role=file.role, content_sha256=file.hash, status=file.status,
        markdown_revision=file.version if file.markdown_path else 0,
        user_edited=file.hand_edited, error=file.error,
    )


def duplicate_groups_for(items: list[Document]) -> list[list[int]]:
    groups: dict[str, list[int]] = {}
    for item in items:
        groups.setdefault(item.content_sha256, []).append(item.id)
    return [group for group in groups.values() if len(group) > 1]


def blockers_for(report: Report, files: list[File], *, extracting: bool = False) -> list[str]:
    items = [document_response(file) for file in files]
    blockers = []
    if not any(item.role == "final_report" for item in items):
        blockers.append("Загрузите итоговый отчёт.")
    if not any(item.role == "supporting" for item in items):
        blockers.append("Загрузите подтверждающие документы.")
    if any(item.status in {"ocr_queued", "ocr_running"} for item in items):
        blockers.append("Дождитесь обработки всех документов.")
    if any(item.status == "failed" for item in items):
        blockers.append("Не удалось извлечь сущности из некоторых документов. Повторите извлечение." if extracting
                        else "Удалите документы с ошибками и загрузите их повторно.")
    if report.stage == "documents" and report.target_row:
        main = next((file for file in files if file.role == "final_report"), None)
        if main is not None and main.markdown_path:
            text = storage.artifact_path(main.markdown_path).read_text(encoding="utf-8")
            if not find_target_row_spans(text, report.target_row):
                blockers.append(
                    f"Не удалось найти проверяемую строку «{report.target_row}» в итоговом отчёте. "
                    "Проверьте результат OCR итогового отчёта и при необходимости исправьте распознанный текст."
                )
    if extracting and any(item.status in {"entities_queued", "entities_running"} for item in items):
        blockers.append("Дождитесь извлечения сущностей из всех документов.")
    if extracting and any(item.status == "ocr_completed" for item in items):
        blockers.append("Извлеките сущности из всех документов.")
    names = {item.id: item.name for item in items}
    for group in duplicate_groups_for(items):
        names_text = ", ".join(f"«{names[file_id]}»" for file_id in group)
        blockers.append(f"Одинаковые файлы: {names_text}. Удалите лишние копии.")
    return blockers


def sync_processing_status(session: Session, report: Report) -> None:
    if report.stage in {"requirements", "verification"}:
        return
    session.flush()
    statuses = {file.status for file in report_documents(session, report.id)}
    if report.stage == "entities":
        report.processing_status = (
            "running" if "entities_running" in statuses else "queued" if "entities_queued" in statuses
            else "failed" if "failed" in statuses else "idle" if "ocr_completed" in statuses
            else "completed" if statuses else "idle"
        )
        return
    report.processing_status = (
        "running" if "ocr_running" in statuses else "queued" if "ocr_queued" in statuses
        else "failed" if "failed" in statuses else "completed" if statuses else "idle"
    )


def list_reports() -> list[ReportResponse]:
    with session_scope() as session:
        return [ReportResponse.model_validate(report, from_attributes=True)
                for report in session.scalars(select(Report).order_by(Report.id.desc()))]


def create_report(payload: ReportCreate) -> ReportResponse:
    with session_scope() as session:
        report = Report(**payload.model_dump())
        session.add(report)
        session.flush()
        report.log_path = create_report_log(report.id)
        result = ReportResponse.model_validate(report, from_attributes=True)
    return result


def report_log(report_id: int) -> Path:
    with session_scope() as session:
        report = find_report(session, report_id)
        if not report.log_path or not storage.artifact_path(report.log_path).is_file():
            raise ServiceError(404, "Журнал отчёта не найден.")
        return storage.artifact_path(report.log_path)


def report_details(report_id: int) -> dict:
    with session_scope() as session:
        report = find_report(session, report_id)
        extracting = report.stage in {"entities", "requirements", "verification"}
        files = report_documents(session, report_id)
        items = [document_response(file) for file in files]
        blockers = blockers_for(report, files, extracting=extracting)
        actions = []
        if report.stage == "documents" and not blockers:
            actions = ["run_entity_extraction"]
        elif not blockers and (report.stage == "entities" or
                               report.stage == "requirements" and report.processing_status == "failed"):
            actions = ["generate_requirements"]
        elif not blockers and (report.stage == "requirements" and report.processing_status == "completed" or
                               report.stage == "verification" and report.processing_status == "failed"):
            actions = ["run_verification"]
        verifying = report.stage in {"requirements", "verification"}
        return {
            **ReportResponse.model_validate(report, from_attributes=True).model_dump(),
            "documents": items,
            "duplicate_groups": duplicate_groups_for(items),
            "progress": {"completed": int(report.processing_status == "completed") if verifying else sum(
                item.status in ({"completed"} if extracting else {"ocr_completed", "completed"}) for item in items
            ), "total": 1 if verifying else len(items)},
            "blockers": blockers,
            "available_actions": actions,
            "processing_mode": report.stage if verifying else "entities" if extracting else "ocr",
        }
