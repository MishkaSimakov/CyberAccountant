"""Report verification and requirement annotations."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..schemas.requirements import RequirementResponse, RequirementsResponse, RequirementUpdate
from ..services import requirements as service
from ..services.reports import report_details

router = APIRouter()


@router.post("/reports/{report_id}/run-verification")
def run_verification(report_id: int):
    service.run_verification(report_id)
    return report_details(report_id)


@router.get("/reports/{report_id}/requirements", response_model=RequirementsResponse)
def requirements(report_id: int):
    return service.report_requirements(report_id)


@router.get("/reports/{report_id}/verification-result")
def verification_result(report_id: int):
    return FileResponse(service.verification_result_file(report_id), media_type="application/json")


@router.patch("/reports/{report_id}/requirements/{requirement_id}", response_model=RequirementResponse)
def update_requirement(report_id: int, requirement_id: int, payload: RequirementUpdate):
    return service.update_requirement(report_id, requirement_id, payload)
