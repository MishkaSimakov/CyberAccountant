"""Verify a canonical snapshot outside transactions; publish only current inputs."""

import logging

from sqlalchemy import select

from processing.common.requirements import AssertionDefinition, FormulaDefinition
from processing.common.scope import Scope
from processing.verification.evaluation import SavedRequirement
from processing.verification.interface import VerificationInput, VerificationProcessor
from processing.verification.processor import Verifier
from .. import storage
from ..database import session_scope
from ..logging import create_report_log, file_log_run
from ..models import Report, Requirement, VerificationResult
from ..services.corpus import input_signature, load_corpus
from ..services.reports import report_documents

logger = logging.getLogger(__name__)


def create_verification_processor() -> VerificationProcessor:
    return Verifier()


def prepare_verification_input(report, files, definitions=()) -> VerificationInput:
    saved = []
    for definition in definitions:
        common = dict(source=definition["source"], text=definition.get("text"))
        if definition["kind"] == "formula":
            value = FormulaDefinition(equation=definition["equation"], tag=definition.get("tag"), **common)
        else:
            value = AssertionDefinition(condition=definition["condition"],
                                        ids_related=tuple(definition["ids_related"]), **common)
        saved.append(SavedRequirement(definition["id"], value))
    return VerificationInput(load_corpus(report.id, files), Scope(
        report.contract_number, report.period_start, report.period_end, report.target_row), tuple(saved))


def _definitions(session, report_id):
    # Notes and silencing are presentation metadata, independent of evaluation.
    return [dict(requirement.json_data, id=requirement.id, text=requirement.text, source=requirement.source)
            for requirement in session.scalars(select(Requirement).where(
            Requirement.report_id == report_id).order_by(Requirement.id))]


def process_verification(report_id: int) -> None:
    with session_scope() as session:
        report = session.get(Report, report_id, with_for_update=True)
        if report is None or report.stage != "verification" or report.processing_status != "queued":
            return
        report.log_path = create_report_log(report_id)
        report.processing_status = "running"
        session.flush()
        run_updated_at, log_path = report.updated_at, report.log_path
        files = report_documents(session, report_id)
        signature = input_signature(report, files)
        definitions = _definitions(session, report_id)

    def is_current(session, current):
        return (current is not None and current.stage == "verification"
                and current.processing_status == "running" and current.updated_at == run_updated_at
                and input_signature(current, report_documents(session, report_id)) == signature
                and _definitions(session, report_id) == definitions)

    try:
        with file_log_run(storage.artifact_path(log_path), "verification", report_id=report_id) as run_logger:
            run_logger.info("Preparing %d reviewed documents", len(files))
            source = prepare_verification_input(report, files, definitions)
            output = create_verification_processor().process(source, logger=run_logger)
            result_path = storage.save_verification_result(report_id, output.report)
            with session_scope() as session:
                current = session.get(Report, report_id, with_for_update=True)
                if not is_current(session, current):
                    run_logger.info("Discarding stale verification completion")
                    return
                record = session.get(VerificationResult, report_id)
                if record is None:
                    session.add(VerificationResult(report_id=report_id, path=result_path))
                else:
                    record.path = result_path
                current.processing_status = "completed"
    except Exception:
        logger.exception("Verification failed for report %s", report_id)
        with session_scope() as session:
            current = session.get(Report, report_id, with_for_update=True)
            if is_current(session, current):
                current.processing_status = "failed"
        raise
