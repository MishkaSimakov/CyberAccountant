"""Schedule verification and retrieve its published result artifact."""

import logging
from pathlib import Path

from sqlalchemy import select

from .. import storage
from ..database import session_scope
from ..models import Requirement, VerificationResult
from ..queue import enqueue_verification
from processing.requirements.validation import validate_formula
from . import ServiceError
from .corpus import validation_registry
from .requirements import require_editing
from .reports import find_report, report_documents

logger = logging.getLogger(__name__)


def run_verification(report_id: int) -> None:
    with session_scope() as session:
        report = find_report(session, report_id, lock=True)
        if report.stage == "verification" and report.processing_status != "failed":
            return
        if not (report.stage == "verification" and report.processing_status == "failed"):
            require_editing(report)
        registry = validation_registry(report_documents(session, report_id))
        for requirement in session.scalars(select(Requirement).where(Requirement.report_id == report_id)):
            if requirement.json_data["kind"] == "formula":
                error = validate_formula(requirement.json_data["equation"], registry)
                if error:
                    raise ServiceError(422, f"Требование {requirement.id}: {error}")
        report.stage = "verification"
        report.processing_status = "queued"

    try:
        enqueue_verification(report_id)
    except Exception:
        logger.exception("Could not enqueue verification for report %s", report_id)
        with session_scope() as session:
            report = find_report(session, report_id, lock=True)
            if report.stage == "verification" and report.processing_status == "queued":
                report.processing_status = "failed"
        raise ServiceError(503, "Не удалось поставить проверку в очередь. Повторите проверку.") from None


def verification_result_file(report_id: int) -> Path:
    with session_scope() as session:
        report = find_report(session, report_id)
        if report.stage != "verification" or report.processing_status != "completed":
            raise ServiceError(409, "Дождитесь завершения проверки требований.")
        result = session.get(VerificationResult, report_id)
        if result is None or not storage.artifact_path(result.path).is_file():
            raise ServiceError(404, "Результат проверки не найден.")
        return storage.artifact_path(result.path)
