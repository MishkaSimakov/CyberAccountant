"""Persisted upload API and sequential Redis/RQ processing."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
from uuid import uuid4

import pymupdf as fitz
import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.job import Job
from sqlalchemy import select

from app import database, queue, storage
from app.api.documents import document_pdf
from app.jobs import ocr
from app.main import app
from app.models import File, Report
from app.ocr import ConversionError, ParsedDocument
from app.services import documents, entities, reports


def request(method, path, payload=None, *, body=None, content_type="application/json"):
    body = body if body is not None else json.dumps(payload).encode() if payload is not None else b""

    async def run():
        messages = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        await app({
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": method, "path": "/api/v1" + path,
            "query_string": b"", "root_path": "", "scheme": "http",
            "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())],
            "client": ("127.0.0.1", 1234), "server": ("test", 80),
        }, receive, send)
        content = b"".join(message.get("body", b"") for message in messages
                           if message["type"] == "http.response.body")
        return messages[0]["status"], content

    status, content = asyncio.run(run())
    return status, json.loads(content) if content and not path.endswith(("/original", "/pdf", "/log")) else content


def upload(report_id, name="invoice.pdf", role="supporting", content=b"%PDF-1.4\ninvoice\n%%EOF"):
    body = (
               f'--boundary\r\nContent-Disposition: form-data; name="role"\r\n\r\n{role}\r\n'
               f'--boundary\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
               'Content-Type: application/octet-stream\r\n\r\n'
           ).encode() + content + b"\r\n--boundary--\r\n"
    return request("POST", f"/reports/{report_id}/documents", body=body,
                   content_type="multipart/form-data; boundary=boundary")


@pytest.fixture
def api(db, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    pending = []

    def enqueue(revision_id):
        # A separate session must see the committed row and its saved original.
        with database.session_scope() as session:
            file = session.get(File, revision_id)
            assert file is not None and file.status == "ocr_queued"
            assert storage.artifact_path(file.path).is_file()
        pending.append(revision_id)

    monkeypatch.setattr(documents, "enqueue_ocr", enqueue)
    status, report = request("POST", "/reports", {"name": "Upload test"})
    assert status == 201
    return report["id"], pending


def test_report_and_uploads_survive_across_requests(api):
    report_id, pending = api
    with database.session_scope() as session:
        assert session.get(Report, report_id).name == "Upload test"
    assert request("GET", "/reports")[1][0]["id"] == report_id
    status, main = upload(report_id, role="final_report")
    assert status == 201
    assert main["status"] == "ocr_queued"
    assert "ocr_status" not in main
    assert "extraction_status" not in main
    assert upload(report_id, role="final_report")[0] == 409
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: upload(report_id, name=f"invoice-{i}.pdf",
                                                 content=f"%PDF-1.4\n{i}\n%%EOF".encode()), range(25)))
    assert all(status == 201 for status, _ in results)
    assert len({item["id"] for _, item in results}) == 25
    assert len(pending) == 26
    with database.session_scope() as session:
        files = list(session.scalars(select(File)))
        assert len(files) == 26
        assert all(file.status == "ocr_queued" and file.version == 1 for file in files)
    details = request("GET", f"/reports/{report_id}")[1]
    assert details["progress"] == {"completed": 0, "total": 26}
    assert details["processing_status"] == "queued"
    assert details["available_actions"] == []
    assert request("GET", f'/reports/{report_id}/documents/{main["id"]}/original')[1].startswith(b"%PDF-")


@pytest.mark.parametrize("name, content, role, expected", [
    ("bad.txt", b"text", "supporting", 415),
    ("empty.pdf", b"", "supporting", 422),
    ("large.pdf", b"x" * (storage.MAX_FILE_SIZE + 1), "supporting", 413),
    ("report.pdf", b"data", "unknown", 422),
])
def test_invalid_upload_does_not_create_record_or_job(api, name, content, role, expected):
    report_id, pending = api
    assert upload(report_id, name=name, content=content, role=role)[0] == expected
    assert pending == []
    assert request("GET", f"/reports/{report_id}")[1]["documents"] == []
    assert not list(storage.storage_root().rglob("original*"))


def test_documents_are_scoped_and_can_be_deleted_while_queued(api):
    report_id, pending = api
    _, other = request("POST", "/reports", {"name": "Other report"})
    _, uploaded = upload(report_id, name="../../invoice.pdf")
    assert uploaded["name"] == "invoice.pdf"
    document_id = uploaded["id"]
    for suffix in ("", "/original", "/pdf", "/markdown", "/log"):
        assert request("GET", f'/reports/{other["id"]}/documents/{document_id}{suffix}')[0] == 404
    assert request("GET", f"/reports/{report_id}/documents/{document_id}/markdown")[0] == 409
    assert request("GET", f"/reports/{report_id}/documents/{document_id}/pdf")[0] == 409
    assert request("DELETE", f"/reports/{report_id}/documents/{document_id}")[0] == 204
    ocr.process_ocr(pending[0])
    assert request("GET", f"/reports/{report_id}")[1]["documents"] == []
    assert not (storage.storage_root() / str(report_id) / str(document_id)).exists()


def test_document_log_is_separate_from_metadata_and_served_as_plain_text(api):
    report_id, pending = api
    status, document = upload(report_id)
    assert status == 201
    path = f'/reports/{report_id}/documents/{document["id"]}'
    assert request("GET", path + "/log") == (200, b"")
    content = "<script>raw log</script>\r\nСтрока журнала\n".encode("utf-8")
    with database.session_scope() as session:
        file = session.get(File, pending[0])
        log_path = storage.artifact_path(file.log_path)
        with log_path.open("ab") as output:
            output.write(content)
    assert request("GET", path + "/log") == (200, content)
    assert "raw log" not in json.dumps(request("GET", path)[1])
    from app.api.documents import document_log
    response = document_log(report_id, document["id"])
    assert response.media_type == "text/plain; charset=utf-8"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    log_path.unlink()
    assert request("GET", path + "/log")[0] == 404


def test_real_ocr_preserves_artifacts_and_updates_progress(api):
    report_id, pending = api
    with fitz.open() as pdf:
        pdf.new_page().insert_text((50, 50), "Invoice total 12345.67")
        original = pdf.tobytes()
    _, uploaded = upload(report_id, content=original)
    ocr.process_ocr(pending[0])
    details = request("GET", f"/reports/{report_id}")[1]
    assert details["documents"][0]["status"] == "ocr_completed"
    _, document_log = request("GET", f'/reports/{report_id}/documents/{uploaded["id"]}/log')
    assert b"Converting original.pdf" in document_log
    assert b"Page 1/1: native text" in document_log
    _, report_log = request("GET", f"/reports/{report_id}/log")
    assert b"Document job started" in report_log and b"Document job completed" in report_log
    assert b"Converting" not in report_log
    assert f"report={report_id} | document={uploaded['id']} | revision_id={pending[0]} | revision=1 | stage=ocr".encode() in report_log
    assert details["progress"] == {"completed": 1, "total": 1}
    assert details["processing_status"] == "completed"
    assert "Invoice total 12345.67" in request("GET", f'/reports/{report_id}/documents/{uploaded["id"]}/markdown')[1][
        "content"]
    with database.session_scope() as session:
        file = session.get(File, pending[0])
        assert file.status == "ocr_completed"
        markdown = storage.artifact_path(file.markdown_path)
        data = json.loads(markdown.with_suffix(".boxes.json").read_text())
        preview = markdown.parent / data["preview_pdf"]
        assert storage.artifact_path(file.pdf_path) == preview
        assert request("GET", f'/reports/{report_id}/documents/{uploaded["id"]}/pdf') == (200, preview.read_bytes())
        assert storage.artifact_path(file.path).read_bytes() == original
    ocr.process_ocr(pending[0])  # Completed jobs do not run again.


def test_missing_target_blocks_after_ocr_until_main_report_text_is_corrected(api, monkeypatch):
    report_id, ocr_ids = api
    path = f"/reports/{report_id}"
    target = "Target 2026"
    with database.session_scope() as session:
        session.get(Report, report_id).target_row = target
    extraction_ids = []
    monkeypatch.setattr(entities, "enqueue_entities", extraction_ids.append)

    for role, text in [("final_report", "Target 2025"), ("supporting", target)]:
        with fitz.open() as pdf:
            pdf.new_page().insert_text((50, 50), text)
            assert upload(report_id, role=role, content=pdf.tobytes())[0] == 201

    assert not any(target in blocker for blocker in request("GET", path)[1]["blockers"])
    ocr.process_ocr(ocr_ids[0])
    details = request("GET", path)[1]
    blocker = next(blocker for blocker in details["blockers"] if target in blocker)
    assert "OCR итогового отчёта" in blocker
    assert details["available_actions"] == []
    assert details["progress"] == {"completed": 1, "total": 2}

    # A match in a supporting file cannot satisfy the main report check.
    ocr.process_ocr(ocr_ids[1])
    details = request("GET", path)[1]
    assert details["blockers"] == [blocker]
    assert details["processing_status"] == "completed"
    assert details["available_actions"] == []
    status, response = request("POST", path + "/run-entity-extraction")
    assert status == 409 and response["detail"] == blocker
    assert extraction_ids == []
    with database.session_scope() as session:
        assert session.get(Report, report_id).stage == "documents"
        assert all(file.entities_path is None for file in reports.report_documents(session, report_id))

    main = next(file for file in details["documents"] if file["role"] == "final_report")
    assert request("POST", path + f'/documents/{main["id"]}/markdown', {
        "content": "| Target 2026 | 100 |\n", "base_revision": 1,
    })[0] == 200
    details = request("GET", path)[1]
    assert details["blockers"] == []
    assert details["available_actions"] == ["run_entity_extraction"]
    assert request("POST", path + "/run-entity-extraction")[0] == 200
    with database.session_scope() as session:
        latest = reports.report_documents(session, report_id)
        assert extraction_ids == [file.id for file in latest]
        assert latest[0].version == 2
        assert all(file.entities_path is None for file in latest)


@pytest.mark.parametrize("name", ["scan.png", "invoice.docx", "workbook.xlsx"])
def test_pdf_endpoint_serves_converted_artifact_across_revisions(api, monkeypatch, tmp_path, name):
    report_id, pending = api
    original = b"original upload"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((50, 50), "Converted preview")
        preview = pdf.tobytes()

    @contextmanager
    def parse(source, *, logger):
        assert source.read_bytes() == original
        output = tmp_path / "converted"
        output.mkdir()
        markdown = output / "document.md"
        markdown.write_text("Converted preview", encoding="utf-8")
        assets = output / "document.assets"
        assets.mkdir()
        pdf_path = assets / "preview.pdf"
        pdf_path.write_bytes(preview)
        sidecar = output / "document.boxes.json"
        sidecar.write_text("{}")
        yield ParsedDocument(markdown=markdown, pdf=pdf_path, json=sidecar)

    monkeypatch.setattr(ocr, "parse_document", parse)
    _, uploaded = upload(report_id, name=name, content=original)
    ocr.process_ocr(pending[0])
    path = f'/reports/{report_id}/documents/{uploaded["id"]}'
    assert request("GET", path + "/pdf") == (200, preview)
    response = document_pdf(report_id, uploaded["id"])
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.filename == name.rsplit(".", 1)[0] + ".pdf"
    assert request("POST", path + "/markdown", {"content": "Edited text", "base_revision": 1})[0] == 200
    assert request("GET", path + "/pdf") == (200, preview)
    assert request("GET", path + "/original") == (200, original)
    with database.session_scope() as session:
        revisions = list(session.scalars(select(File).where(File.file_id == uploaded["id"]).order_by(File.version)))
        assert len(revisions) == 2
        assert revisions[0].pdf_path == revisions[1].pdf_path
        storage.artifact_path(revisions[0].pdf_path).unlink()
    assert request("GET", path + "/pdf")[0] == 404


def test_ocr_failure_is_persisted(api, monkeypatch):
    report_id, pending = api
    upload(report_id)

    def fail(_, **kwargs):
        raise ConversionError("broken document")

    monkeypatch.setattr(ocr, "parse_document", fail)
    with pytest.raises(ConversionError):
        ocr.process_ocr(pending[0])
    details = request("GET", f"/reports/{report_id}")[1]
    assert details["processing_status"] == "failed"
    assert details["documents"][0]["status"] == "failed"
    assert details["documents"][0]["error"]
    _, report_log = request("GET", f"/reports/{report_id}/log")
    assert b"Document job failed (ConversionError)" in report_log
    assert b"broken document" not in report_log
    _, document_log = request("GET", f'/reports/{report_id}/documents/{details["documents"][0]["id"]}/log')
    assert b"ERROR (ConversionError): broken document" in document_log


def test_queue_failure_keeps_original_and_marks_failed(api, monkeypatch):
    report_id, _ = api

    def fail(_):
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(documents, "enqueue_ocr", fail)
    assert upload(report_id)[0] == 503
    details = request("GET", f"/reports/{report_id}")[1]
    assert details["documents"][0]["status"] == "failed"
    with database.session_scope() as session:
        assert storage.artifact_path(session.scalar(select(File)).path).is_file()


def test_real_rq_processes_bundle_in_fifo_order(api, monkeypatch, tmp_path):
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to run the Redis/RQ integration test.")
    report_id, _ = api
    connection = Redis.from_url(url)
    test_queue = Queue("test-" + uuid4().hex, connection=connection)
    monkeypatch.setattr(queue, "ocr_queue", lambda: test_queue)
    monkeypatch.setattr(documents, "enqueue_ocr", queue.enqueue_ocr)
    events = []

    @contextmanager
    def parse(source, *, logger):
        with database.session_scope() as session:
            files = reports.report_documents(session, report_id)
            assert sum(file.status == "ocr_running" for file in files) == 1
            assert session.get(Report, report_id).processing_status == "running"
        events.append(("start", source.parent.name))
        output = tmp_path / source.parent.name
        output.mkdir()
        markdown = output / "document.md"
        markdown.write_text("OCR text", encoding="utf-8")
        pdf = output / "preview.pdf"
        pdf.write_bytes(b"preview")
        sidecar = output / "document.boxes.json"
        sidecar.write_text("{}")
        yield ParsedDocument(markdown=markdown, pdf=pdf, json=sidecar)
        events.append(("end", source.parent.name))

    monkeypatch.setattr(ocr, "parse_document", parse)
    ids = []
    job_ids = []
    try:
        for i in range(8):
            status, uploaded = upload(report_id, name=f"file-{i}.pdf",
                                      role="final_report" if i == 0 else "supporting")
            assert status == 201
            ids.append(str(uploaded["id"]))
        job_ids = test_queue.job_ids
        assert len(job_ids) == 8
        SimpleWorker([test_queue], connection=connection).work(burst=True, logging_level="WARNING")
        assert events == [(event, file_id) for file_id in ids for event in ("start", "end")]
        assert request("GET", f"/reports/{report_id}")[1]["progress"] == {"completed": 8, "total": 8}
    finally:
        for job_id in job_ids:
            Job.fetch(job_id, connection=connection).delete()
        test_queue.delete(delete_jobs=True)
        connection.close()
