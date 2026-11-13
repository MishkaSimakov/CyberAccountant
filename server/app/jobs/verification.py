"""Generate editable definitions, then evaluate saved requirements in a separate job."""

import logging

from sqlalchemy import delete, select

from ..database import session_scope
from .. import storage
from ..logging import create_report_log, file_log_run
from ..models import EntityFile, Report, Requirement, VerificationResult
from ..services.requirements import requirement_response
from ..services.reports import find_report, report_documents
from ..verification import evaluate_requirements, generate_requirements, prepare_verification

logger = logging.getLogger(__name__)


def process_requirement_generation(report_id: int) -> None:
    _process(report_id, "requirements")


def process_verification(report_id: int) -> None:
    _process(report_id, "verification")


def _process(report_id: int, stage: str) -> None:
    with session_scope() as session:
        if session.get(Report, report_id) is None:
            return
        report = find_report(session, report_id, lock=True)
        if report.stage != stage or report.processing_status != "queued":
            return
        report.log_path = create_report_log(report_id)
        report.processing_status = "running"
        files = report_documents(session, report_id)
        definitions = [requirement_response(requirement).model_dump() for requirement in session.scalars(
            select(Requirement).where(Requirement.report_id == report_id).order_by(Requirement.id)
        )]

    try:
        with file_log_run(storage.artifact_path(report.log_path), stage, report_id=report_id) as run_logger:
            run_logger.info("Preparing %d reviewed documents", len(files))
            context = prepare_verification(report, files)
            if stage == "requirements":
                generated = generate_requirements(context, logger=run_logger)
                with session_scope() as session:
                    report = find_report(session, report_id, lock=True)
                    if report.stage != stage or report.processing_status != "running":
                        return
                    session.execute(delete(Requirement).where(Requirement.report_id == report_id))
                    for definition in generated:
                        data = dict(definition)
                        session.add(Requirement(report_id=report_id, text=data.pop("text"),
                                                source=data.pop("source"), json_data=data))
                    session.execute(delete(EntityFile).where(EntityFile.report_id == report_id))
                    session.add_all(EntityFile(report_id=report_id, entity_id=entity.num, file_id=int(entity.file))
                                    for entity in context.registry.entities)
                    report.processing_status = "completed"
                return

            result = evaluate_requirements(context, definitions, logger=run_logger)
            result_path = storage.save_verification_result(report_id, result)
            with session_scope() as session:
                report = find_report(session, report_id, lock=True)
                if report.stage != stage or report.processing_status != "running":
                    return
                record = session.get(VerificationResult, report_id)
                if record is None:
                    session.add(VerificationResult(report_id=report_id, path=result_path))
                else:
                    record.path = result_path
                report.processing_status = "completed"
    except Exception:
        logger.exception("%s failed for report %s", stage, report_id)
        with session_scope() as session:
            report = find_report(session, report_id, lock=True)
            if report.stage == stage and report.processing_status == "running":
                report.processing_status = "failed"
        raise
