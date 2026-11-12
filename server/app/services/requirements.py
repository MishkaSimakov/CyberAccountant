"""Queue report verification and expose its atomically published results."""

import logging

from sqlalchemy import select

from ..database import session_scope
from ..models import EntityFile, Requirement
from ..queue import enqueue_verification
from ..schemas.requirements import RequirementResponse, RequirementsResult, RequirementUpdate
from . import ServiceError
from .reports import blockers_for, document_response, find_report, report_documents

logger = logging.getLogger(__name__)


def run_verification(report_id: int) -> None:
    with session_scope() as session:
        report = find_report(session, report_id, lock=True)
        if report.stage == "verification" and report.processing_status != "failed":
            return
        if report.stage not in {"entities", "verification"}:
            raise ServiceError(409, "Сначала завершите разметку сущностей.")
        files = report_documents(session, report_id)
        blockers = blockers_for([document_response(file, extracting=True) for file in files], extracting=True)
        if blockers or any(file.status != "completed" or not file.entities_path for file in files):
            raise ServiceError(409, " ".join(blockers) or "Завершите извлечение сущностей из всех документов.")
        report.stage = "verification"
        report.processing_status = "queued"

    try:
        enqueue_verification(report_id)
    except Exception:
        logger.exception("Could not enqueue verification for report %s", report_id)
        with session_scope() as session:
            report = find_report(session, report_id, lock=True)
            if report.processing_status == "queued":
                report.processing_status = "failed"
        raise ServiceError(503, "Не удалось поставить проверку в очередь. Повторите проверку.") from None


def requirement_response(requirement: Requirement) -> RequirementResponse:
    return RequirementResponse(
        **requirement.json_data, id=requirement.id, verdict=requirement.verdict,
        notes=requirement.note, silenced=requirement.is_silenced,
    )


def requirements_result(report_id: int) -> RequirementsResult:
    with session_scope() as session:
        report = find_report(session, report_id)
        if report.stage != "verification" or report.processing_status != "completed":
            raise ServiceError(409, "Дождитесь завершения проверки требований.")
        return RequirementsResult(
            requirements=[requirement_response(requirement) for requirement in session.scalars(
                select(Requirement).where(Requirement.report_id == report_id).order_by(Requirement.id)
            )],
            entity_documents={entity.entity_id: entity.file_id for entity in session.scalars(
                select(EntityFile).where(EntityFile.report_id == report_id)
            )},
        )


def update_requirement(report_id: int, requirement_id: int, payload: RequirementUpdate) -> RequirementResponse:
    with session_scope() as session:
        find_report(session, report_id)
        requirement = session.scalar(select(Requirement).where(
            Requirement.id == requirement_id, Requirement.report_id == report_id,
        ).with_for_update())
        if requirement is None:
            raise ServiceError(404, "Требование не найдено.")
        changes = payload.model_dump(exclude_unset=True)
        if "notes" in changes:
            requirement.note = changes["notes"]
        if "silenced" in changes:
            requirement.is_silenced = changes["silenced"]
        return requirement_response(requirement)
