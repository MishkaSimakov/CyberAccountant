"""HTTP controllers for report creation, listing, and processing progress."""

from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ..schemas.reports import Report, ReportCreate
from ..services.archives import report_archive
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


@router.get("/reports/{report_id}/archive")
def download_report_archive(report_id: int):
    path = report_archive(report_id)
    return FileResponse(path, media_type="application/zip", filename=f"report-{report_id}.zip",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
                        background=BackgroundTask(path.unlink, missing_ok=True))


@router.get("/reports/{report_id}/log")
def report_log(report_id: int):
    return FileResponse(service.report_log(report_id), media_type="text/plain; charset=utf-8",
                        filename=f"report-{report_id}.log", content_disposition_type="inline",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
