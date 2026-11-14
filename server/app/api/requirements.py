"""Report verification and requirement annotations."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..schemas.requirements import (RequirementDefinition, RequirementResponse, RequirementsResponse,
                                    RequirementUpdate, FormulaValidationRequest, FormulaValidationResponse)
from ..services import requirements as service
from ..services import verification
from ..services.reports import report_details

router = APIRouter()


@router.post("/reports/{report_id}/generate-requirements")
def generate_requirements(report_id: int):
    service.generate_requirements(report_id)
    return report_details(report_id)


@router.post("/reports/{report_id}/edit-requirements")
def edit_requirements(report_id: int):
    service.edit_requirements(report_id)
    return report_details(report_id)


@router.post("/reports/{report_id}/requirements/validate-formula", response_model=FormulaValidationResponse)
def validate_formula(report_id: int, payload: FormulaValidationRequest):
    return service.validate_requirement_formula(report_id, payload.equation)


@router.post("/reports/{report_id}/requirements", response_model=RequirementResponse, status_code=201)
def create_requirement(report_id: int, payload: RequirementDefinition):
    return service.save_requirement(report_id, payload)


@router.put("/reports/{report_id}/requirements/{requirement_id}", response_model=RequirementResponse)
def save_requirement(report_id: int, requirement_id: int, payload: RequirementDefinition):
    return service.save_requirement(report_id, payload, requirement_id)


@router.delete("/reports/{report_id}/requirements/{requirement_id}", status_code=204)
def delete_requirement(report_id: int, requirement_id: int):
    service.delete_requirement(report_id, requirement_id)


@router.post("/reports/{report_id}/run-verification")
def run_verification(report_id: int):
    verification.run_verification(report_id)
    return report_details(report_id)


@router.get("/reports/{report_id}/requirements", response_model=RequirementsResponse)
def requirements(report_id: int):
    return service.report_requirements(report_id)


@router.get("/reports/{report_id}/verification-result")
def verification_result(report_id: int):
    return FileResponse(verification.verification_result_file(report_id), media_type="application/json")


@router.patch("/reports/{report_id}/requirements/{requirement_id}", response_model=RequirementResponse)
def update_requirement(report_id: int, requirement_id: int, payload: RequirementUpdate):
    return service.update_requirement(report_id, requirement_id, payload)
