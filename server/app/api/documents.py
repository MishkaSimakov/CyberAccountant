"""HTTP controllers for persisted uploads and OCR output."""

from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..schemas.documents import Document, MarkdownEdit, MarkdownRevision
from ..services import documents as service
from ..services.reports import report_details
from ..storage import artifact_path

router = APIRouter()


@router.post("/reports/{report_id}/documents", response_model=Document, status_code=201)
def upload_document(report_id: int, file: UploadFile = File(),
                    role: Literal["final_report", "supporting"] = Form()):
    try:
        return service.upload_document(report_id, file.filename or "", file.file, role)
    finally:
        file.file.close()


@router.get("/reports/{report_id}/documents/{document_id}", response_model=Document)
def document_details(report_id: int, document_id: int):
    return service.document_details(report_id, document_id)


@router.delete("/reports/{report_id}/documents/{document_id}", status_code=204)
def delete_document(report_id: int, document_id: int):
    service.delete_document(report_id, document_id)


@router.get("/reports/{report_id}/documents/{document_id}/original")
def original_document(report_id: int, document_id: int):
    file = service.original_document(report_id, document_id)
    return FileResponse(artifact_path(file.path), media_type=file.content_type, filename=file.name,
                        content_disposition_type="inline", headers={"X-Content-Type-Options": "nosniff"})


@router.get("/reports/{report_id}/documents/{document_id}/markdown", response_model=MarkdownRevision)
def document_markdown(report_id: int, document_id: int):
    return service.document_markdown(report_id, document_id)


@router.post("/reports/{report_id}/documents/{document_id}/markdown")
def edit_markdown(report_id: int, document_id: int, payload: MarkdownEdit):
    service.document_details(report_id, document_id)
    raise HTTPException(501, "Редактирование документов пока не подключено.")


@router.post("/reports/{report_id}/run-entity-extraction")
def run_entity_extraction(report_id: int):
    report_details(report_id)
    raise HTTPException(501, "Извлечение сущностей пока не подключено.")
