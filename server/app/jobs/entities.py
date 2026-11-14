"""Extract entries from a queued OCR revision and preserve Checkier's markup."""

import logging
import os

from sqlalchemy import select

from processing.entities.interface import EntityInput, EntityProcessor
from processing.entities.options import EntityOptions
from processing.entities.processor import LLMEntityProcessor
from ..database import session_scope
from ..entities import assign_document_identities
from ..llm import create_model_client
from ..logging import create_file_log, create_report_log, file_log_run
from ..models import File, Report
from ..services.documents import find_document
from ..services.reports import sync_processing_status
from ..storage import artifact_path, save_entities

logger = logging.getLogger(__name__)


def create_entity_processor() -> EntityProcessor:
    client = create_model_client()
    options = EntityOptions(tag_model=os.environ.get("AI_TAG_MODEL", EntityOptions().tag_model))
    return LLMEntityProcessor(client, options)


def process_entities(file_revision_id: int) -> None:
    """Run one document extraction, keeping LLM calls outside transactions."""
    with session_scope() as session:
        report_id = session.scalar(select(File.report_id).where(File.id == file_revision_id))
        if report_id is None:
            return
        report = session.get(Report, report_id, with_for_update=True)
        file = session.get(File, file_revision_id)
        if (report is None or file is None or report.stage != "entities" or file.status != "entities_queued"
                or find_document(session, report_id, file.file_id).id != file_revision_id):
            return
        file.log_path = create_file_log(report_id, file.file_id)
        report.log_path = create_report_log(report_id)
        report_log_path = artifact_path(report.log_path)
        file.status = "entities_running"
        file.error = None
        sync_processing_status(session, report)
        run_updated_at = file.updated_at

    def is_current(session, report, current):
        return (report is not None and report.stage == "entities"
                and current is not None and current.status == "entities_running"
                and current.updated_at == run_updated_at
                and find_document(session, report_id, current.file_id).id == file_revision_id)

    try:
        with file_log_run(artifact_path(file.log_path), "entity_extraction", report_id=report_id,
                          document_id=file.file_id, revision_id=file.id, revision=file.version,
                          report_log_path=report_log_path) as run_logger:
            source = artifact_path(file.markdown_path)
            output = create_entity_processor().process(
                EntityInput(filename=file.name, markdown=source.read_text(encoding="utf-8")), logger=run_logger)
            tagged = output.markdown
            with session_scope() as session:
                report = session.get(Report, report_id, with_for_update=True)
                current = session.get(File, file_revision_id)
                if not is_current(session, report, current):
                    run_logger.info("Discarding stale extraction completion")
                    return
                tagged = assign_document_identities(session, report_id, current.file_id, tagged, previous="")
                current.entities_path = save_entities(report_id, current.file_id, current.version, tagged)
                current.status = "completed"
                current.error = None
                sync_processing_status(session, report)
    except Exception:
        logger.exception("Entries extraction failed for file revision %s", file_revision_id)
        with session_scope() as session:
            report = session.get(Report, report_id, with_for_update=True)
            current = session.get(File, file_revision_id)
            if is_current(session, report, current):
                current.status = "failed"
                current.error = "Не удалось извлечь сущности из документа. Повторите извлечение."
                sync_processing_status(session, report)
        raise
