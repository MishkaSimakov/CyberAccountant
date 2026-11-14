"""Generate editable definitions from a snapshot and publish only current inputs."""

import logging
import os
from dataclasses import asdict

from sqlalchemy import delete

from processing.common.corpus import ReviewedCorpus
from processing.common.documents import DocumentRevision, ReviewedDocument
from processing.common.scope import Scope
from processing.requirements.interface import RequirementInput, RequirementProcessor
from processing.requirements.options import RequirementOptions
from processing.requirements.processor import RequirementsProcessor
from .. import storage
from ..database import session_scope
from ..llm import create_model_client
from ..logging import create_report_log, file_log_run
from ..models import EntityFile, Report, Requirement
from ..services.reports import report_documents
from ..target_row import find_target_row_keys

logger = logging.getLogger(__name__)


def create_requirement_processor() -> RequirementProcessor:
    options = RequirementOptions(rel_model=os.environ.get("AI_REL_MODEL", RequirementOptions().rel_model))
    return RequirementsProcessor(create_model_client(), options)


def prepare_requirement_input(report: Report, files) -> RequirementInput:
    corpus = ReviewedCorpus(report.id, tuple(ReviewedDocument(
        revision=DocumentRevision(report.id, file.file_id, file.version), name=file.name, role=file.role,
        markdown=storage.artifact_path(file.entities_path).read_text(encoding="utf-8"),
    ) for file in files))
    source = RequirementInput(corpus, Scope(report.contract_number, report.period_start, report.period_end,
                                            report.target_row))
    tagged = {str(doc.revision.document_id): (
        "card" if doc.role == "final_report" else "data", doc.markdown) for doc in corpus.documents}
    target = find_target_row_keys(tagged, source.registry, report.target_row)
    if report.target_row and not target:
        raise ValueError("Не удалось найти проверяемую строку в итоговом отчёте.")
    return RequirementInput(corpus, Scope(report.contract_number, report.period_start, report.period_end,
                                          report.target_row, tuple(sorted(target)) if target else None))


def _input_signature(report, files):
    return (report.contract_number, report.period_start, report.period_end, report.target_row,
            tuple((file.id, file.file_id, file.version, file.name, file.role, file.entities_path,
                   file.status, file.updated_at) for file in files))


def process_requirement_generation(report_id: int) -> None:
    with session_scope() as session:
        report = session.get(Report, report_id, with_for_update=True)
        if report is None or report.stage != "requirements" or report.processing_status != "queued":
            return
        report.log_path = create_report_log(report_id)
        report.processing_status = "running"
        session.flush()
        run_updated_at = report.updated_at
        log_path = report.log_path
        files = report_documents(session, report_id)
        signature = _input_signature(report, files)

    def is_current(session, current):
        return (current is not None and current.stage == "requirements"
                and current.processing_status == "running" and current.updated_at == run_updated_at
                and _input_signature(current, report_documents(session, report_id)) == signature)

    try:
        with file_log_run(storage.artifact_path(log_path), "requirements", report_id=report_id) as run_logger:
            run_logger.info("Preparing %d reviewed documents", len(files))
            source = prepare_requirement_input(report, files)
            output = create_requirement_processor().process(source, logger=run_logger)
            with session_scope() as session:
                current = session.get(Report, report_id, with_for_update=True)
                if not is_current(session, current):
                    run_logger.info("Discarding stale requirement generation completion")
                    return
                session.execute(delete(Requirement).where(Requirement.report_id == report_id))
                for definition in output.definitions:
                    data = asdict(definition)
                    session.add(Requirement(report_id=report_id, text=data.pop("text"),
                                            source=data.pop("source"), json_data=data))
                session.execute(delete(EntityFile).where(EntityFile.report_id == report_id))
                session.add_all(EntityFile(report_id=report_id, entity_id=entity.num, file_id=int(entity.file))
                                for entity in source.registry.entities)
                current.processing_status = "completed"
    except Exception:
        logger.exception("Requirement generation failed for report %s", report_id)
        with session_scope() as session:
            current = session.get(Report, report_id, with_for_update=True)
            if is_current(session, current):
                current.processing_status = "failed"
        raise
