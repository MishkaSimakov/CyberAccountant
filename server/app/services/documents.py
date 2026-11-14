"""Upload and read documents using PostgreSQL metadata and local artifacts."""

import logging
import shutil
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..logging import create_file_log
from .. import storage
from ..database import session_scope
from ..entities import assign_document_identities
from ..entity_geometry import EntityNotFound, locate_entity
from ..models import EntityFile, File
from ..queue import enqueue_ocr
from ..schemas.documents import Document, MarkdownEdit, MarkdownRevision
from . import ServiceError
from .reports import document_response, find_report, sync_processing_status

logger = logging.getLogger(__name__)


def find_document(session: Session, report_id: int, file_id: int) -> File:
    file = session.scalar(select(File).where(File.report_id == report_id, File.file_id == file_id)
                          .order_by(File.version.desc()).limit(1))
    if file is None:
        raise ServiceError(404, "Документ не найден.")
    return file


def require_upload_stage(report) -> None:
    if report.stage != "documents":
        raise ServiceError(409, "Изменять документы можно только на этапе загрузки.")


def upload_document(report_id: int, filename: str, stream: BinaryIO, role: str) -> Document:
    name = Path(filename.replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if suffix not in storage.FILE_TYPES:
        raise ServiceError(415, "Поддерживаются PDF, DOCX, XLSX, PNG и JPEG.")
    with session_scope() as session:
        require_upload_stage(find_report(session, report_id))
    staged, size, digest = storage.stage_upload(stream)
    original = None
    try:
        with session_scope() as session:
            report = find_report(session, report_id, lock=True)
            require_upload_stage(report)
            if role == "final_report" and session.scalar(select(File.id).where(
                    File.report_id == report_id, File.role == role).limit(1)) is not None:
                raise ServiceError(409, "Итоговый отчёт уже загружен. Сначала удалите его, чтобы загрузить другой.")
            file = File(report_id=report_id, name=name, path="", hash=digest,
                        role=role, size=size, content_type=storage.FILE_TYPES[suffix])
            session.add(file)
            session.flush()
            file.path = storage.save_original(staged, report_id, file.file_id, suffix)
            original = storage.artifact_path(file.path)
            file.log_path = create_file_log(report_id, file.file_id)
            sync_processing_status(session, report)
            result = document_response(file)
            revision_id = file.id
    except Exception:
        if original is not None:
            original.unlink(missing_ok=True)
        raise
    finally:
        staged.unlink(missing_ok=True)

    # The row and original are committed before a worker can receive the ID.
    try:
        enqueue_ocr(revision_id)
    except Exception:
        logger.exception("Could not enqueue OCR for file revision %s", revision_id)
        with session_scope() as session:
            report = find_report(session, report_id, lock=True)
            file = session.get(File, revision_id)
            if file is not None:
                file.status = "failed"
                file.error = "Не удалось поставить документ в очередь распознавания. Удалите его и загрузите повторно."
                sync_processing_status(session, report)
        raise ServiceError(503, "Не удалось поставить документ в очередь распознавания.") from None
    return result


def document_details(report_id: int, file_id: int) -> Document:
    with session_scope() as session:
        find_report(session, report_id)
        return document_response(find_document(session, report_id, file_id))


def original_document(report_id: int, file_id: int) -> File:
    with session_scope() as session:
        find_report(session, report_id)
        return find_document(session, report_id, file_id)


def pdf_document(report_id: int, file_id: int) -> File:
    with session_scope() as session:
        find_report(session, report_id)
        file = find_document(session, report_id, file_id)
        if not file.pdf_path:
            raise ServiceError(409, "PDF документа ещё не готов.")
        if not storage.artifact_path(file.pdf_path).is_file():
            raise ServiceError(404, "PDF документа не найден.")
        return file


def entity_geometry(report_id: int, file_id: int, entity_id: int) -> dict:
    with session_scope() as session:
        report = find_report(session, report_id)
        file = find_document(session, report_id, file_id)
        tagged = storage.artifact_path(f'{report_id}/verification/replaced/{file_id}.md')
        if report.stage not in {'requirements', 'verification'} or not tagged.is_file():
            raise ServiceError(404, 'Сущности для этого документа ещё не подготовлены.')
        if not file.pdf_path or not storage.artifact_path(file.pdf_path).is_file():
            raise ServiceError(409, 'PDF документа ещё не готов.')
        try:
            geometry = locate_entity(tagged.read_text(encoding='utf-8'), entity_id,
                                     storage.artifact_path(file.pdf_path), file.hash)
        except EntityNotFound as exc:
            raise ServiceError(404, str(exc)) from exc
        return dict(geometry, document_revision=file.version)


def document_log(report_id: int, file_id: int) -> File:
    with session_scope() as session:
        find_report(session, report_id)
        file = find_document(session, report_id, file_id)
        if not file.log_path or not storage.artifact_path(file.log_path).is_file():
            raise ServiceError(404, "Журнал документа не найден.")
        return file


def document_markdown(report_id: int, file_id: int) -> MarkdownRevision:
    with session_scope() as session:
        report = find_report(session, report_id)
        file = find_document(session, report_id, file_id)
        if not file.markdown_path:
            raise ServiceError(409, "Распознавание документа ещё не завершено.")
        source = storage.artifact_path(file.entities_path or file.markdown_path)
        if report.stage in {"requirements", "verification"} and storage.artifact_path(
                f"{report_id}/verification/replaced/{file_id}.md").is_file():
            source = storage.artifact_path(f"{report_id}/verification/replaced/{file_id}.md")
        return MarkdownRevision(content=source.read_text(encoding="utf-8"),
                                revision=file.version)


def delete_document(report_id: int, file_id: int) -> None:
    with session_scope() as session:
        report = find_report(session, report_id, lock=True)
        require_upload_stage(report)
        file = find_document(session, report_id, file_id)
        if file.status == "ocr_running":
            raise ServiceError(409, "Дождитесь завершения распознавания документа.")
        session.execute(delete(EntityFile).where(EntityFile.report_id == report_id, EntityFile.file_id == file_id))
        session.execute(delete(File).where(File.report_id == report_id, File.file_id == file_id))
        sync_processing_status(session, report)
    shutil.rmtree(storage.storage_root() / str(report_id) / str(file_id), ignore_errors=True)


def edit_markdown(report_id: int, file_id: int, payload: MarkdownEdit) -> MarkdownRevision:
    with session_scope() as session:
        report = find_report(session, report_id, lock=True)
        file = find_document(session, report_id, file_id)
        if report.stage not in {"documents", "entities"}:
            raise ServiceError(409, "Редактирование на этом этапе недоступно.")
        if (not file.markdown_path or file.status not in {"ocr_completed", "completed"}
                or (report.stage == "entities" and not file.entities_path)):
            raise ServiceError(409, "Дождитесь завершения обработки документа.")
        if payload.base_revision != file.version:
            raise ServiceError(409, "Документ уже изменён. Откройте его заново.")
        if not payload.content.strip():
            raise ServiceError(422, "Текст документа не должен быть пустым.")
        previous = storage.artifact_path(file.entities_path or file.markdown_path).read_text(encoding="utf-8")
        content = assign_document_identities(session, report_id, file_id, payload.content, previous)
        version = file.version + 1
        # User edits are preserved separately from regenerable OCR/extraction output.
        relative = Path(str(report_id)) / str(file_id) / "versions" / str(version) / "edited" / "document.md"
        destination = storage.artifact_path(relative.as_posix())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        edited_path = relative.as_posix()
        revision = File(
            report_id=report_id, file_id=file_id, version=version, name=file.name,
            path=file.path, hash=file.hash, role=file.role, size=file.size, content_type=file.content_type,
            pdf_path=file.pdf_path,
            log_path=file.log_path,
            markdown_path=file.markdown_path if file.entities_path else edited_path,
            entities_path=edited_path if file.entities_path else None,
            status=file.status, hand_edited=True,
        )
        session.add(revision)
        sync_processing_status(session, report)
        return MarkdownRevision(content=content, revision=version)
