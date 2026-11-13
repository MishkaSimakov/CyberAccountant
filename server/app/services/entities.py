"""Start entries extraction for the report's current OCR revisions."""

import logging

from ..database import session_scope
from ..models import File
from ..queue import enqueue_entities
from . import ServiceError
from .documents import find_document
from .reports import blockers_for, find_report, report_documents, sync_processing_status

logger = logging.getLogger(__name__)


def run_entity_extraction(report_id: int, document_id: int | None = None) -> None:
    with session_scope() as session:
        report = find_report(session, report_id, lock=True)
        if document_id is not None:
            if report.stage != "entities":
                raise ServiceError(409, "Повторное извлечение доступно только на этапе разметки сущностей.")
            files = [find_document(session, report_id, document_id)]
        else:
            files = report_documents(session, report_id)
        if report.stage == "documents":
            blockers = blockers_for(report, files)
            if blockers:
                raise ServiceError(409, " ".join(blockers))
            report.stage = "entities"
            pending = files
        elif report.stage == "entities":
            # Repeated requests while queued/running are harmless. A later
            # request retries failed documents without repeating successes.
            pending = [file for file in files if file.status == "failed" and file.markdown_path]
        else:
            raise ServiceError(409, "Извлечение сущностей на этом этапе недоступно.")
        for file in pending:
            file.status = "ocr_completed"
            file.error = None
        sync_processing_status(session, report)
        revision_ids = [file.id for file in pending]

    failed = False
    for revision_id in revision_ids:
        try:
            enqueue_entities(revision_id)
        except Exception:
            failed = True
            logger.exception("Could not enqueue entries extraction for file revision %s", revision_id)
            with session_scope() as session:
                report = find_report(session, report_id, lock=True)
                file = session.get(File, revision_id)
                if file is not None and file.status == "ocr_completed":
                    file.status = "failed"
                    file.error = "Не удалось поставить документ в очередь извлечения сущностей. Повторите извлечение."
                    sync_processing_status(session, report)
    if failed:
        raise ServiceError(503, "Не удалось поставить документы в очередь извлечения сущностей.")
