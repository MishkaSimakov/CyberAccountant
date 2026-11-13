"""Extract entries from a queued OCR revision and preserve Checkier's markup."""

import logging

from checkier import PipelineConfig, normalize_document, tag_document
from checkier.file_log import file_log_run

from ..database import session_scope
from ..models import File
from ..services.documents import find_document
from ..services.reports import find_report, sync_processing_status
from ..storage import artifact_path, save_entities

logger = logging.getLogger(__name__)

def process_entities(file_revision_id: int) -> None:
    """Run one document extraction, keeping LLM calls outside transactions."""
    with session_scope() as session:
        file = session.get(File, file_revision_id)
        if file is None:
            return
        report_id = file.report_id
        report = find_report(session, report_id, lock=True)
        session.refresh(file)
        if (report.stage != "entities" or file.status != "entities_queued"
                or find_document(session, report_id, file.file_id).id != file_revision_id):
            return
        file.status = "entities_running"
        file.error = None
        sync_processing_status(session, report)

    try:
        with file_log_run(artifact_path(file.log_path), f"entity_extraction | revision={file.version}"):
            source = artifact_path(file.markdown_path)
            cfg = PipelineConfig(data_dir=source.parent, out_dir=source.parent, tag_workers=1)
            tagged, _retries = tag_document(cfg, file.name, source.read_text(encoding="utf-8"))
            tagged = normalize_document(cfg, tagged)
            entities_path = save_entities(report_id, file.file_id, file.version, tagged)
    except Exception:
        logger.exception("Entries extraction failed for file revision %s", file_revision_id)
        with session_scope() as session:
            report = find_report(session, report_id, lock=True)
            file = session.get(File, file_revision_id)
            if file is not None and file.status == "entities_running":
                file.status = "failed"
                file.error = "Не удалось извлечь сущности из документа. Повторите извлечение."
                sync_processing_status(session, report)
        raise

    with session_scope() as session:
        report = find_report(session, report_id, lock=True)
        file = session.get(File, file_revision_id)
        if (file is not None and file.status == "entities_running"
                and find_document(session, report_id, file.file_id).id == file_revision_id):
            file.entities_path = entities_path
            file.status = "completed"
            file.error = None
            sync_processing_status(session, report)
