"""Report downloads preserve artifacts, annotations, and report boundaries."""

import asyncio
import json
from functools import partial
from io import BytesIO
from zipfile import ZipFile

import pytest

from app import database, storage
from app.main import app
from app.models import File, Report, Requirement
from app.schemas.reports import ReportCreate
from app.services import archives, reports


@pytest.fixture
def completed_report(db, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setattr(archives, "NamedTemporaryFile", partial(archives.NamedTemporaryFile, dir=tmp_path))
    report = reports.create_report(ReportCreate(name="Итоговый отчёт"))
    with database.session_scope() as session:
        record = session.get(Report, report.id)
        record.stage = "verification"
        record.processing_status = "completed"
        session.add(File(file_id=1, report_id=report.id, name="Счёт.pdf", path=f"{report.id}/1/original.pdf",
                         hash="a" * 64, role="supporting", size=3, content_type="application/pdf",
                         status="completed", markdown_path=f"{report.id}/1/versions/2/ocr/document.md"))
        session.add(Requirement(report_id=report.id, json_data={"kind": "formula", "equation": "1 == 1"},
                                text="Проверка", source="manual", note="Сохранённая заметка", is_silenced=True))
    return report.id


def download(report_id):
    async def run():
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app({
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "GET", "path": f"/api/v1/reports/{report_id}/archive",
            "query_string": b"", "root_path": "", "scheme": "http", "headers": [],
            "client": ("127.0.0.1", 1234), "server": ("test", 80),
        }, receive, send)
        return messages[0], b"".join(message.get("body", b"") for message in messages[1:])

    return asyncio.run(run())


def test_download_bundles_all_artifacts_and_saved_annotations(completed_report, tmp_path):
    report_id = completed_report
    artifacts = {
        "processing.log": b"report log",
        "1/original.pdf": b"pdf",
        "1/processing.log": b"model prompt and output",
        "1/versions/1/ocr/document.md": b"initial OCR",
        "1/versions/2/ocr/document.md": b"edited OCR",
        "1/versions/2/ocr/document.boxes.json": b"{}",
        "1/versions/2/ocr/document.assets/preview.pdf": b"preview",
        "1/versions/2/entities/document.md": b"extracted entities",
        "verification/entities.json": b"{}",
        "verification/replaced/1.md": b"replaced entities",
        "verification/report-final.json": b'{"requirement_results": []}',
    }
    for relative, content in artifacts.items():
        path = storage.artifact_path(f"{report_id}/{relative}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    unrelated = storage.artifact_path(f"{report_id + 1}/private.log")
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("other report")
    storage.artifact_path(f"{report_id}/linked.log").symlink_to(unrelated)
    storage.artifact_path(f"{report_id}/linked-directory").symlink_to(unrelated.parent, target_is_directory=True)

    response, content = download(report_id)

    assert response["status"] == 200
    headers = dict(response["headers"])
    assert headers[b"content-type"] == b"application/zip"
    assert headers[b"content-disposition"] == f'attachment; filename="report-{report_id}.zip"'.encode()
    assert headers[b"cache-control"] == b"no-store"
    assert not list(tmp_path.glob("*.zip"))
    prefix = f"report-{report_id}/"
    with ZipFile(BytesIO(content)) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {prefix + name for name in artifacts} | {
            prefix + "report.json", prefix + "requirements.json",
        }
        for relative, expected in artifacts.items():
            assert archive.read(prefix + relative) == expected
        metadata = json.loads(archive.read(prefix + "report.json"))
        assert metadata["name"] == "Итоговый отчёт"
        assert metadata["documents"][0]["name"] == "Счёт.pdf"
        requirement = json.loads(archive.read(prefix + "requirements.json"))["requirements"][0]
        assert requirement["equation"] == "1 == 1"
        assert requirement["notes"] == "Сохранённая заметка"
        assert requirement["silenced"] is True


@pytest.mark.parametrize("stage, status", [("documents", "completed"), ("verification", "running"),
                                           ("verification", "failed")])
def test_download_requires_completed_verification(completed_report, stage, status, tmp_path):
    with database.session_scope() as session:
        report = session.get(Report, completed_report)
        report.stage = stage
        report.processing_status = status
    response, _ = download(completed_report)
    assert response["status"] == 409
    assert not list(tmp_path.glob("*.zip"))


def test_download_returns_404_for_unknown_report(completed_report):
    response, _ = download(completed_report + 1)
    assert response["status"] == 404


def test_download_returns_404_for_missing_artifacts(completed_report):
    storage.artifact_path(str(completed_report)).rename(storage.artifact_path("missing"))
    response, _ = download(completed_report)
    assert response["status"] == 404


def test_archive_failure_removes_temporary_zip(completed_report, monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ZipFile, "write", fail)
    with pytest.raises(OSError, match="disk full"):
        archives.report_archive(completed_report)
    assert not list(tmp_path.glob("*.zip"))
