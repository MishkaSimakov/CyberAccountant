"""Persist reports and expose their current document-processing progress."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import session_scope
from ..models import File, Report
from ..schemas.documents import Document
from ..schemas.reports import Report as ReportResponse, ReportCreate
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


def document_response(file: File, *, extracting: bool = False) -> Document:
    status = ("running" if file.status == "ocr_running" else "completed" if file.markdown_path
    else "failed" if file.status == "failed" else "queued")
    extraction_status = (
        "completed" if file.entities_path else "running" if file.status == "entities_running"
        else "failed" if file.status == "failed" and file.markdown_path
        else "queued" if extracting and file.status == "ocr_completed" else "idle"
    )
    return Document(
        id=file.file_id, name=file.name, size=file.size, content_type=file.content_type,
        role=file.role, content_sha256=file.hash, ocr_status=status,
        extraction_status=extraction_status,
        markdown_revision=file.version if file.markdown_path else 0,
        user_edited=file.hand_edited, error=file.error,
    )


def duplicate_groups_for(items: list[Document]) -> list[list[int]]:
    groups: dict[str, list[int]] = {}
    for item in items:
        groups.setdefault(item.content_sha256, []).append(item.id)
    return [group for group in groups.values() if len(group) > 1]


def blockers_for(items: list[Document], *, extracting: bool = False) -> list[str]:
    blockers = []
    if not any(item.role == "final_report" for item in items):
        blockers.append("Загрузите итоговый отчёт.")
    if not any(item.role == "supporting" for item in items):
        blockers.append("Загрузите подтверждающие документы.")
    if any(item.ocr_status in {"queued", "running"} for item in items):
        blockers.append("Дождитесь обработки всех документов.")
    if any(item.ocr_status == "failed" for item in items):
        blockers.append("Удалите документы с ошибками и загрузите их повторно.")
    if extracting and any(item.extraction_status in {"queued", "running"} for item in items):
        blockers.append("Дождитесь извлечения сущностей из всех документов.")
    if extracting and any(item.extraction_status == "failed" for item in items):
        blockers.append("Не удалось извлечь сущности из некоторых документов. Повторите извлечение.")
    if extracting and any(item.extraction_status == "idle" for item in items):
        blockers.append("Извлеките сущности из всех документов.")
    names = {item.id: item.name for item in items}
    for group in duplicate_groups_for(items):
        names_text = ", ".join(f"«{names[file_id]}»" for file_id in group)
        blockers.append(f"Одинаковые файлы: {names_text}. Удалите лишние копии.")
    return blockers


def sync_processing_status(session: Session, report: Report) -> None:
    if report.stage == "verification":
        return
    session.flush()
    statuses = {file.status for file in report_documents(session, report.id)}
    if report.stage == "entities":
        report.processing_status = (
            "running" if "entities_running" in statuses else "queued" if "ocr_completed" in statuses
            else "failed" if "failed" in statuses else "completed" if statuses else "idle"
        )
        return
    report.processing_status = (
        "running" if "ocr_running" in statuses else "queued" if "queued" in statuses
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
        result = ReportResponse.model_validate(report, from_attributes=True)
    return result


def report_details(report_id: int) -> dict:
    with session_scope() as session:
        report = find_report(session, report_id)
        extracting = report.stage in {"entities", "verification"}
        items = [document_response(file, extracting=extracting) for file in report_documents(session, report_id)]
        blockers = blockers_for(items, extracting=extracting)
        actions = []
        if report.stage == "documents" and not blockers:
            actions = ["run_entity_extraction"]
        elif not blockers and (report.stage == "entities" or
                               report.stage == "verification" and report.processing_status == "failed"):
            actions = ["run_verification"]
        verifying = report.stage == "verification"
        return {
            **ReportResponse.model_validate(report, from_attributes=True).model_dump(),
            "documents": items,
            "duplicate_groups": duplicate_groups_for(items),
            "progress": {"completed": int(report.processing_status == "completed") if verifying else sum(
                (item.extraction_status if extracting else item.ocr_status) == "completed" for item in items
            ), "total": 1 if verifying else len(items)},
            "blockers": blockers,
            "available_actions": actions,
            "processing_mode": "verification" if verifying else "entities" if extracting else "ocr",
        }
