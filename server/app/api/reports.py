"""HTTP controllers for report creation, listing, and processing progress."""

from fastapi import APIRouter

from ..schemas.reports import Report, ReportCreate
from ..services import reports as service

router = APIRouter()


@router.get("/reports", response_model=list[Report])
def reports():
    return service.list_reports()


@router.post("/reports", response_model=Report, status_code=201)
def create_report(payload: ReportCreate):
    return service.create_report(payload)


@router.get("/reports/{report_id}")
def report_details(report_id: int):
    return service.report_details(report_id)
