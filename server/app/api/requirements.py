"""Keep verification explicitly unavailable until its pipeline is connected."""

from fastapi import APIRouter, HTTPException

from ..schemas.requirements import RequirementUpdate
from ..services.reports import report_details

router = APIRouter()


@router.post("/reports/{report_id}/run-verification")
@router.get("/reports/{report_id}/requirements")
def verification_unavailable(report_id: int):
    report_details(report_id)
    raise HTTPException(501, "Проверка требований пока не подключена.")


@router.patch("/reports/{report_id}/requirements/{requirement_id}")
def update_requirement(report_id: int, requirement_id: int, payload: RequirementUpdate):
    return verification_unavailable(report_id)
