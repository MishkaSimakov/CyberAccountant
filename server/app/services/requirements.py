"""Manage editable requirements and queue verification of saved definitions."""

import logging
from pathlib import Path

from sqlalchemy import func, select

from ..database import session_scope
from .. import storage
from ..models import EntityFile, Requirement, VerificationResult
from ..queue import enqueue_requirement_generation, enqueue_verification
from ..schemas.requirements import (RequirementDefinition, RequirementResponse, RequirementsResponse,
                                    RequirementUpdate, FormulaValidationResponse, requirement_adapter)
from ..verification import validate_formula, validation_registry
from . import ServiceError
from .reports import blockers_for, find_report, report_documents

logger = logging.getLogger(__name__)


def generate_requirements(report_id: int) -> None:
    with session_scope() as session:
        report = find_report(session, report_id, lock=True)
        if report.stage == "requirements" and report.processing_status != "failed":
            return
        if report.stage not in {"entities", "requirements"}:
            raise ServiceError(409, "Сначала завершите разметку сущностей.")
        files = report_documents(session, report_id)
        blockers = blockers_for(report, files, extracting=True)
        if blockers or any(file.status != "completed" or not file.entities_path for file in files):
            raise ServiceError(409, " ".join(blockers) or "Завершите извлечение сущностей из всех документов.")
        report.stage = "requirements"
        report.processing_status = "queued"

    try:
        enqueue_requirement_generation(report_id)
    except Exception:
        logger.exception("Could not enqueue requirements for report %s", report_id)
        with session_scope() as session:
            report = find_report(session, report_id, lock=True)
            if report.stage == "requirements" and report.processing_status == "queued":
                report.processing_status = "failed"
        raise ServiceError(503, "Не удалось поставить генерацию требований в очередь. Повторите попытку.") from None


def require_editing(report) -> None:
    if report.stage != "requirements" or report.processing_status != "completed":
        raise ServiceError(409, "Изменение требований доступно только на этапе редактирования требований.")


def edit_requirements(report_id: int) -> None:
    with session_scope() as session:
        report = find_report(session, report_id, lock=True)
        if report.stage == "requirements" and report.processing_status == "completed":
            return
        if report.stage != "verification" or report.processing_status not in {"completed", "failed"}:
            raise ServiceError(409, "Дождитесь завершения проверки требований.")
        report.stage = "requirements"
        report.processing_status = "completed"


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
                    raise ServiceError(422, f"Требование {requirement.requirement_id}: {error}")
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


def requirement_response(requirement: Requirement) -> RequirementResponse:
    return requirement_adapter.validate_python(dict(
        **requirement.json_data, id=requirement.requirement_id, text=requirement.text, source=requirement.source,
        notes=requirement.note, silenced=requirement.is_silenced,
    ))


def report_requirements(report_id: int) -> RequirementsResponse:
    with session_scope() as session:
        find_report(session, report_id)
        return RequirementsResponse(
            requirements=[requirement_response(requirement) for requirement in session.scalars(
                select(Requirement).where(Requirement.report_id == report_id).order_by(Requirement.requirement_id)
            )],
            entity_documents={item.entity_id: item.file_id for item in session.scalars(
                select(EntityFile).where(EntityFile.report_id == report_id)
            )},
        )


def verification_result_file(report_id: int) -> Path:
    with session_scope() as session:
        report = find_report(session, report_id)
        if report.stage != "verification" or report.processing_status != "completed":
            raise ServiceError(409, "Дождитесь завершения проверки требований.")
        result = session.get(VerificationResult, report_id)
        if result is None or not storage.artifact_path(result.path).is_file():
            raise ServiceError(404, "Результат проверки не найден.")
        return storage.artifact_path(result.path)


def update_requirement(report_id: int, requirement_id: int, payload: RequirementUpdate) -> RequirementResponse:
    with session_scope() as session:
        report = find_report(session, report_id, lock=True)
        requirement = session.scalar(select(Requirement).where(
            Requirement.requirement_id == requirement_id, Requirement.report_id == report_id,
        ).with_for_update())
        if requirement is None:
            raise ServiceError(404, "Требование не найдено.")
        if report.processing_status != "completed" or report.stage not in {"requirements", "verification"}:
            raise ServiceError(409, "Дождитесь завершения обработки требований.")
        changes = payload.model_dump(exclude_unset=True)
        if "notes" in changes:
            requirement.note = changes["notes"]
        if "silenced" in changes:
            requirement.is_silenced = changes["silenced"]
        return requirement_response(requirement)


def validate_requirement_formula(report_id: int, equation: str) -> FormulaValidationResponse:
    with session_scope() as session:
        require_editing(find_report(session, report_id))
        error = validate_formula(equation, validation_registry(report_documents(session, report_id)))
        return FormulaValidationResponse(valid=error is None, error=error)


def save_requirement(report_id: int, payload: RequirementDefinition,
                     requirement_id: int | None = None) -> RequirementResponse:
    with session_scope() as session:
        require_editing(find_report(session, report_id, lock=True))
        if payload.kind == "formula":
            error = validate_formula(payload.equation, validation_registry(report_documents(session, report_id)))
            if error:
                raise ServiceError(422, error)
        if requirement_id is None:
            # The report row lock serializes allocation with other writers for this report.
            next_id = session.scalar(select(func.coalesce(func.max(Requirement.requirement_id), 0) + 1).where(
                Requirement.report_id == report_id,
            ))
            requirement = Requirement(report_id=report_id, requirement_id=next_id, source="manual")
            session.add(requirement)
        else:
            requirement = session.scalar(select(Requirement).where(
                Requirement.requirement_id == requirement_id, Requirement.report_id == report_id,
            ))
            if requirement is None:
                raise ServiceError(404, "Требование не найдено.")
        data = payload.model_dump()
        requirement.text = data.pop("text")
        requirement.json_data = data
        session.flush()
        return requirement_response(requirement)


def delete_requirement(report_id: int, requirement_id: int) -> None:
    with session_scope() as session:
        require_editing(find_report(session, report_id, lock=True))
        requirement = session.scalar(select(Requirement).where(
            Requirement.requirement_id == requirement_id, Requirement.report_id == report_id,
        ))
        if requirement is None:
            raise ServiceError(404, "Требование не найдено.")
        session.delete(requirement)
