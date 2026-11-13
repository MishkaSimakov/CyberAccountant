"""Bundle a completed report and all its artifacts for download."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from .. import storage
from ..database import session_scope
from ..schemas.reports import Report as ReportResponse
from . import ServiceError
from .reports import document_response, find_report, report_documents
from .requirements import report_requirements


def report_archive(report_id: int) -> Path:
    with session_scope() as session:
        report = find_report(session, report_id)
        if report.stage != "verification" or report.processing_status != "completed":
            raise ServiceError(409, "Дождитесь завершения проверки требований.")
        metadata = ReportResponse.model_validate(report, from_attributes=True).model_dump(mode="json")
        metadata["documents"] = [document_response(file).model_dump(mode="json")
                                 for file in report_documents(session, report_id)]

    directory = storage.artifact_path(str(report_id))
    if not directory.is_dir():
        raise ServiceError(404, "Файлы отчёта не найдены.")
    requirements = report_requirements(report_id).model_dump(mode="json")
    prefix = f"report-{report_id}"
    with NamedTemporaryFile(suffix=".zip", delete=False) as temporary:
        archive_path = Path(temporary.name)
    try:
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
            for path in sorted(directory.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(directory.resolve()):
                    archive.write(path, f"{prefix}/{path.relative_to(directory).as_posix()}")
            archive.writestr(f"{prefix}/report.json", json.dumps(metadata, ensure_ascii=False, indent=2))
            archive.writestr(f"{prefix}/requirements.json", json.dumps(requirements, ensure_ascii=False, indent=2))
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path
