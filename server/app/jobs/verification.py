"""One report job generates all requirements before invoking the evaluator."""

import logging

from sqlalchemy import delete

from ..database import session_scope
from ..models import EntityFile, Report, Requirement
from ..services.reports import find_report, report_documents
from ..verification import evaluate_requirements, generate_requirements, prepare_verification

logger = logging.getLogger(__name__)


def process_verification(report_id: int) -> None:
    with session_scope() as session:
        report = session.get(Report, report_id)
        if report is None:
            return
        report = find_report(session, report_id, lock=True)
        if report.stage != "verification" or report.processing_status != "queued":
            return
        report.processing_status = "running"
        files = report_documents(session, report_id)

    try:
        context = prepare_verification(report, files)
        requirements = generate_requirements(context)
        results = evaluate_requirements(context, requirements)
        with session_scope() as session:
            report = find_report(session, report_id, lock=True)
            if report.processing_status != "running":
                return
            session.execute(delete(Requirement).where(Requirement.report_id == report_id))
            session.execute(delete(EntityFile).where(EntityFile.report_id == report_id))
            for result in results:
                definition = dict(result)
                verdict = definition.pop("verdict")
                session.add(Requirement(report_id=report_id, verdict=verdict, json_data=definition))
            session.add_all(EntityFile(report_id=report_id, entity_id=entity.num, file_id=int(entity.file))
                            for entity in context.registry.entities)
            report.processing_status = "completed"
    except Exception:
        logger.exception("Verification failed for report %s", report_id)
        with session_scope() as session:
            report = find_report(session, report_id, lock=True)
            if report.processing_status == "running":
                report.processing_status = "failed"
        raise
