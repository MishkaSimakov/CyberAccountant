"""Run OCR for a queued file revision, save its artifacts, and update report progress."""

import logging
from pathlib import Path

from ..database import session_scope
from ..models import File
from ..ocr import parse_document
from ..services.reports import find_report, sync_processing_status
from ..storage import artifact_path, save_ocr

logger = logging.getLogger(__name__)


def process_ocr(file_revision_id: int) -> None:
    """Recognize a queued document and persist its output or failure status."""
    with session_scope() as session:
        file = session.get(File, file_revision_id)
        if file is None or file.status != "ocr_queued":
            return
        report_id = file.report_id
        report = find_report(session, report_id, lock=True)
        session.refresh(file)
        if file.status != "ocr_queued":
            return
        file.status = "ocr_running"
        sync_processing_status(session, report)

    try:
        with parse_document(artifact_path(file.path)) as parsed:
            markdown_path = save_ocr(report_id, file.file_id, file.version, parsed.markdown)
            pdf_path = (Path(markdown_path).parent / parsed.pdf.relative_to(parsed.markdown.parent)).as_posix()
    except Exception:
        logger.exception("OCR failed for file revision %s", file_revision_id)
        with session_scope() as session:
            report = find_report(session, report_id, lock=True)
            file = session.get(File, file_revision_id)
            if file is not None:
                file.status = "failed"
                file.error = "Не удалось распознать документ. Удалите его и загрузите исправленный файл."
                sync_processing_status(session, report)
        raise

    with session_scope() as session:
        report = find_report(session, report_id, lock=True)
        file = session.get(File, file_revision_id)
        if file is not None:
            file.pdf_path = pdf_path
            file.markdown_path = markdown_path
            file.status = "ocr_completed"
            file.error = None
            sync_processing_status(session, report)
