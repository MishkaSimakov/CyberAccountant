"""Local artifact storage shared by the API and OCR worker."""

import os
import json
import shutil
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO
from uuid import uuid4

from .services import ServiceError

MAX_FILE_SIZE = 20 * 1024 * 1024
FILE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def storage_root() -> Path:
    return Path(os.environ.get("STORAGE_ROOT", Path(__file__).resolve().parents[2] / "storage")).resolve()


def artifact_path(relative_path: str) -> Path:
    return storage_root() / relative_path


def save_verification_result(report_id: int, result: dict) -> str:
    relative = f"{report_id}/verification/report-{uuid4().hex}.json"
    destination = artifact_path(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.close()
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return relative


def stage_upload(stream: BinaryIO) -> tuple[Path, int, str]:
    """Bound memory use even when many uploads arrive together."""
    staging = storage_root() / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    digest = sha256()
    size = 0
    with NamedTemporaryFile(dir=staging, delete=False) as output:
        path = Path(output.name)
        try:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise ServiceError(413, "Размер файла превышает 20 МБ.")
                digest.update(chunk)
                output.write(chunk)
            if not size:
                raise ServiceError(422, "Файл пуст. Выберите файл с содержимым.")
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return path, size, digest.hexdigest()


def save_original(staged: Path, report_id: int, file_id: int, suffix: str) -> str:
    relative = Path(str(report_id)) / str(file_id) / ("original" + suffix)
    destination = artifact_path(str(relative))
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged.replace(destination)
    return relative.as_posix()


def save_ocr(report_id: int, file_id: int, version: int, output: Path) -> str:
    relative = Path(str(report_id)) / str(file_id) / "versions" / str(version) / "ocr"
    shutil.copytree(output.parent, artifact_path(str(relative)), dirs_exist_ok=True)
    return (relative / output.name).as_posix()


def save_entities(report_id: int, file_id: int, version: int, markdown: str) -> str:
    relative = Path(str(report_id)) / str(file_id) / "versions" / str(version) / "entities" / "document.md"
    destination = artifact_path(str(relative))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(markdown, encoding="utf-8")
    return relative.as_posix()
