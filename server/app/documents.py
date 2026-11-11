"""In-memory document API with simulated asynchronous OCR, pending the real pipeline."""

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Literal
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

router = APIRouter()
MAX_FILE_SIZE = 20 * 1024 * 1024
FILE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class Document(BaseModel):
    id: int
    name: str
    size: int
    content_type: str
    role: Literal["final_report", "supporting"]
    ocr_status: Literal["queued", "running", "completed", "failed"] = "queued"
    extraction_status: Literal["idle"] = "idle"
    markdown_revision: int = 0
    user_edited: bool = False
    error: str | None = None


class MarkdownRevision(BaseModel):
    content: str = Field(max_length=1_000_000)
    revision: int


class MarkdownEdit(BaseModel):
    content: str = Field(min_length=1, max_length=1_000_000)
    base_revision: int = Field(ge=1)


documents: dict[int, dict[int, Document]] = {}
originals: dict[int, bytes] = {}
revisions: dict[int, list[MarkdownRevision]] = {}
next_document_id = 1


def find_report(report_id: int):
    from .main import report_store

    report = next((item for item in report_store if item.id == report_id), None)
    if report is None:
        raise HTTPException(404, "Отчёт не найден.")
    return report


def find_document(report_id: int, document_id: int) -> Document:
    find_report(report_id)
    document = documents.get(report_id, {}).get(document_id)
    if document is None:
        raise HTTPException(404, "Документ не найден.")
    return document


def require_upload_stage(report_id: int):
    report = find_report(report_id)
    if report.stage != "documents":
        raise HTTPException(409, "Изменять документы можно только на этапе загрузки.")
    return report


def blockers_for(items: list[Document]) -> list[str]:
    blockers = []
    if not any(item.role == "final_report" for item in items):
        blockers.append("Загрузите итоговый отчёт.")
    if not any(item.role == "supporting" for item in items):
        blockers.append("Загрузите подтверждающие документы.")
    if any(item.ocr_status in ("queued", "running") for item in items):
        blockers.append("Дождитесь обработки всех документов.")
    if any(item.ocr_status == "failed" for item in items):
        blockers.append("Удалите документы с ошибками и загрузите их повторно.")
    return blockers


def sync_processing_status(report_id: int):
    report = find_report(report_id)
    items = list(documents.get(report_id, {}).values())
    if any(item.ocr_status in ("queued", "running") for item in items):
        report.processing_status = "running"
    elif any(item.ocr_status == "failed" for item in items):
        report.processing_status = "failed"
    elif items and not blockers_for(items):
        report.processing_status = "completed"
    else:
        report.processing_status = "idle"


def valid_content(name: str, data: bytes) -> bool:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return data.startswith(b"%PDF-") and b"%%EOF" in data[-1024:]
    if suffix == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in (".jpg", ".jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    try:
        with ZipFile(BytesIO(data)) as archive:
            expected = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
            return expected in archive.namelist()
    except BadZipFile:
        return False


async def simulate_ocr(report_id: int, document_id: int):
    await asyncio.sleep(1)
    document = documents.get(report_id, {}).get(document_id)
    if document is None:
        return
    document.ocr_status = "running"
    await asyncio.sleep(2)
    if document_id not in documents.get(report_id, {}):
        return
    data = originals[document_id]
    if not valid_content(document.name, data):
        document.ocr_status = "failed"
        document.error = "Файл повреждён или его содержимое не соответствует расширению. Удалите его и загрузите исправленный файл."
    else:
        content = (
            f"# {document.name}\n\n"
            "> Демонстрационный результат: OCR пока работает как заглушка. "
            "Текст документа не распознан.\n\n"
            "Здесь появится распознанный текст. Вы можете заменить его текстом оригинала "
            "и сохранить исправления.\n"
        )
        revisions[document_id] = [MarkdownRevision(content=content, revision=1)]
        document.markdown_revision = 1
        document.ocr_status = "completed"
    sync_processing_status(report_id)


@router.get("/reports/{report_id}")
async def report_details(report_id: int):
    report = find_report(report_id)
    items = list(documents.get(report_id, {}).values())
    blockers = blockers_for(items)
    return {
        **report.model_dump(),
        "documents": items,
        "progress": {"completed": sum(item.ocr_status == "completed" for item in items), "total": len(items)},
        "blockers": blockers,
        "available_actions": ["run_entity_extraction"] if not blockers and report.stage == "documents" else [],
        "processing_mode": "stub",
    }


@router.post("/reports/{report_id}/documents", response_model=Document, status_code=201)
async def upload_document(
        report_id: int,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(),
        role: Literal["final_report", "supporting"] = Form(),
):
    global next_document_id
    require_upload_stage(report_id)
    name = Path((file.filename or "").replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    try:
        if suffix not in FILE_TYPES:
            raise HTTPException(415, "Поддерживаются PDF, DOCX, XLSX, PNG и JPEG.")
        data = await file.read(MAX_FILE_SIZE + 1)
        if not data:
            raise HTTPException(422, "Файл пуст. Выберите файл с содержимым.")
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(413, "Размер файла превышает 20 МБ.")
    finally:
        await file.close()
    require_upload_stage(report_id)
    report_documents = documents.setdefault(report_id, {})
    if role == "final_report" and any(item.role == role for item in report_documents.values()):
        raise HTTPException(409, "Итоговый отчёт уже загружен. Сначала удалите его, чтобы загрузить другой.")
    document = Document(id=next_document_id, name=name, size=len(data), content_type=FILE_TYPES[suffix], role=role)
    next_document_id += 1
    report_documents[document.id] = document
    originals[document.id] = data
    sync_processing_status(report_id)
    background_tasks.add_task(simulate_ocr, report_id, document.id)
    return document


@router.get("/reports/{report_id}/documents/{document_id}", response_model=Document)
async def document_details(report_id: int, document_id: int):
    return find_document(report_id, document_id)


@router.delete("/reports/{report_id}/documents/{document_id}", status_code=204)
async def delete_document(report_id: int, document_id: int):
    require_upload_stage(report_id)
    find_document(report_id, document_id)
    del documents[report_id][document_id]
    originals.pop(document_id, None)
    revisions.pop(document_id, None)
    sync_processing_status(report_id)


@router.get("/reports/{report_id}/documents/{document_id}/original")
async def original_document(report_id: int, document_id: int):
    from urllib.parse import quote

    document = find_document(report_id, document_id)
    return Response(
        originals[document_id],
        media_type=document.content_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(document.name, safe='')}",
                 "X-Content-Type-Options": "nosniff"},
    )


@router.get("/reports/{report_id}/documents/{document_id}/markdown", response_model=MarkdownRevision)
async def document_markdown(report_id: int, document_id: int):
    document = find_document(report_id, document_id)
    if document.ocr_status != "completed":
        raise HTTPException(409, "Распознавание документа ещё не завершено.")
    return revisions[document_id][-1]


@router.post("/reports/{report_id}/documents/{document_id}/markdown", response_model=MarkdownRevision)
async def edit_markdown(report_id: int, document_id: int, payload: MarkdownEdit):
    require_upload_stage(report_id)
    document = find_document(report_id, document_id)
    if document.ocr_status != "completed":
        raise HTTPException(409, "Распознавание документа ещё не завершено.")
    if payload.base_revision != document.markdown_revision:
        raise HTTPException(409, "Текст уже изменён в другом окне. Скопируйте свои правки и заново откройте документ.")
    if not payload.content.strip():
        raise HTTPException(422, "Текст документа не может быть пустым.")
    revision = MarkdownRevision(content=payload.content, revision=document.markdown_revision + 1)
    revisions[document_id].append(revision)
    document.markdown_revision = revision.revision
    document.user_edited = True
    return revision


@router.post("/reports/{report_id}/run-entity-extraction")
async def run_entity_extraction(report_id: int):
    report = require_upload_stage(report_id)
    blockers = blockers_for(list(documents.get(report_id, {}).values()))
    if blockers:
        raise HTTPException(409, " ".join(blockers))
    # The next stage is a placeholder; no entity processing is simulated yet.
    report.stage = "entities"
    report.processing_status = "idle"
    return await report_details(report_id)
