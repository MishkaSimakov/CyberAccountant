"""Persisted entries extraction, its queue, and report progress."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from redis import Redis
from rq import Queue, SimpleWorker
from rq.job import Job
from sqlalchemy import select

from processing.common.llm import ModelClient
from processing.entities.identifiers import assign_identities, read_identities

from app import database, queue, storage
from app.jobs import entities as job
from app.models import EntityFile, File, Report, ReportEntityCounter
from app.services import entities, reports
from test_documents import api, request, upload

MARKDOWN = "# Сумма\nИтого: 100,00 руб.\n"
SEGMENTS = [
    {"content": "# Сумма\nИтого: ", "wrap": "100,00", "type": "amount", "unit": "RUB"},
    {"content": " руб.\n", "wrap": "", "type": "EOF"},
]
TAGGED = '# Сумма\nИтого: <ent t="amount" u="RUB" value="100.00">100,00</ent> руб.\n'


def canonical(num=0):
    return assign_identities(TAGGED, range(num, num + 1))


@pytest.fixture
def ready(api, monkeypatch, llm_client):
    report_id, ocr_ids = api
    upload(report_id, role="final_report", content=b"report")
    upload(report_id, content=b"invoice")
    with database.session_scope() as session:
        for revision_id in ocr_ids:
            file = session.get(File, revision_id)
            relative = f"{report_id}/{file.file_id}/versions/1/ocr/document.md"
            path = storage.artifact_path(relative)
            path.parent.mkdir(parents=True)
            path.write_text(MARKDOWN, encoding="utf-8")
            file.markdown_path = relative
            file.status = "ocr_completed"
        reports.sync_processing_status(session, session.get(Report, report_id))
    extraction_ids = []

    def enqueue(revision_id):
        with database.session_scope() as session:
            file = session.get(File, revision_id)
            assert file.status == "entities_queued"
            assert session.get(Report, report_id).stage == "entities"
            assert storage.artifact_path(file.markdown_path).read_text(encoding="utf-8") == MARKDOWN
        extraction_ids.append(revision_id)

    monkeypatch.setattr(entities, "enqueue_entities", enqueue)
    monkeypatch.setattr(llm_client, "call_llm_json", lambda *args, **kwargs: SEGMENTS)
    return report_id, ocr_ids, extraction_ids


def test_extraction_preserves_full_model_logs_across_runs(ready, monkeypatch, llm_client):
    report_id, revisions, _ = ready
    path = f"/reports/{report_id}"
    output = '{"incomplete": "' + "Ответ модели\r\n" * 1_000
    monkeypatch.setattr(llm_client.adapter, "generate", lambda *args, **kwargs: output)
    monkeypatch.setattr(llm_client, "call_llm_json", ModelClient.call_llm_json.__get__(llm_client))
    for run in range(1, 3):
        assert request("POST", path + "/run-entity-extraction")[0] == 200
        for revision_id in revisions:
            with pytest.raises(RuntimeError, match="tagging failed"):
                job.process_entities(revision_id)
        details = request("GET", path)[1]
        assert details["processing_status"] == "failed"
        assert all(document["status"] == "failed" for document in details["documents"])
        assert all(document["error"] == "Не удалось извлечь сущности из документа. Повторите извлечение."
                   for document in details["documents"])
        _, report_log = request("GET", path + "/log")
        assert report_log.count(b"Document job started") == run * len(revisions)
        assert report_log.count(b"Document job failed (RuntimeError)") == run * len(revisions)
        assert b"MODEL PROMPT" not in report_log and b"MODEL OUTPUT" not in report_log
        for document in details["documents"]:
            assert request("GET", path + f'/documents/{document["id"]}')[1]["error"] == document["error"]
            status, content = request("GET", path + f'/documents/{document["id"]}/log')
            assert status == 200
            log = content.decode("utf-8")
            assert log.count("\nRUN ") == run
            assert log.count("--- MODEL PROMPT |") == run * 9
            assert f"Filename: {document['name']}" in log
            assert MARKDOWN in log
            assert content.count(output.encode("utf-8")) == run * 9
            assert log.count("ERROR (RuntimeError): tagging failed") == run
        with database.session_scope() as session:
            for revision_id in revisions:
                file = session.get(File, revision_id)
                assert file.entities_path is None
                assert storage.artifact_path(file.markdown_path).read_text(encoding="utf-8") == MARKDOWN


def test_all_nested_model_retries_append_to_the_document_log(ready, monkeypatch, llm_client):
    report_id, revisions, _ = ready
    path = f"/reports/{report_id}"
    request("POST", path + "/run-entity-extraction")
    outputs = []

    def respond(*args, **kwargs):
        output = f"Attempt {len(outputs) + 1}: " + "Некорректный ответ\r\n" * 4_000
        outputs.append(output)
        return output

    monkeypatch.setattr(llm_client.adapter, "generate", respond)
    monkeypatch.setattr(llm_client, "call_llm_json", ModelClient.call_llm_json.__get__(llm_client))
    with pytest.raises(RuntimeError, match="tagging failed"):
        job.process_entities(revisions[0])
    assert len(outputs) == 9
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        document_id = file.file_id
        assert file.error == "Не удалось извлечь сущности из документа. Повторите извлечение."
        other = session.get(File, revisions[1])
        assert storage.artifact_path(other.log_path).read_bytes() == b""
    status, content = request("GET", path + f"/documents/{document_id}/log")
    assert status == 200
    assert content.count(b"--- MODEL OUTPUT |") == 9
    assert content.count(b"\nRUN ") == 1
    for output in outputs:
        assert output.encode("utf-8") in content


def test_extraction_requires_ready_documents_and_rejects_duplicates(api):
    report_id, _ = api
    path = f"/reports/{report_id}/run-entity-extraction"
    assert request("POST", path)[0] == 409
    upload(report_id, role="final_report")
    upload(report_id)
    assert request("POST", path)[0] == 409
    with database.session_scope() as session:
        for file in reports.report_documents(session, report_id):
            file.markdown_path = "unused.md"
            file.status = "ocr_completed"
    status, response = request("POST", path)
    assert status == 409
    assert "Одинаковые файлы" in response["detail"]


def test_completed_ocr_is_not_an_extraction_queue_entry(ready, monkeypatch):
    report_id, revisions, pending = ready
    with database.session_scope() as session:
        session.get(Report, report_id).stage = "entities"
        reports.sync_processing_status(session, session.get(Report, report_id))

    def unexpected(*args, **kwargs):
        pytest.fail("Extraction must not start before the document is queued")

    monkeypatch.setattr(job, "tag_document", unexpected)
    job.process_entities(revisions[0])
    details = request("GET", f"/reports/{report_id}")[1]
    assert details["processing_status"] == "idle"
    assert {file["status"] for file in details["documents"]} == {"ocr_completed"}
    assert details["blockers"] == ["Извлеките сущности из всех документов."]
    assert details["available_actions"] == []
    assert pending == []


def test_extraction_preserves_ocr_and_exposes_raw_markup(ready):
    report_id, revisions, pending = ready
    path = f"/reports/{report_id}"
    assert request("GET", path)[1]["available_actions"] == ["run_entity_extraction"]
    status, response = request("POST", path + "/run-entity-extraction")
    assert status == 200
    assert response["stage"] == "entities"
    assert response["processing_status"] == "queued"
    assert response["progress"] == {"completed": 0, "total": 2}
    assert {file["status"] for file in response["documents"]} == {"entities_queued"}
    assert response["blockers"] == ["Дождитесь извлечения сущностей из всех документов."]
    assert response["available_actions"] == []
    assert pending == revisions
    assert request("POST", path + "/run-entity-extraction")[0] == 200
    assert pending == revisions  # Repeated requests must not duplicate jobs.
    for index, revision_id in enumerate(revisions):
        job.process_entities(revision_id)
        details = request("GET", path)[1]
        assert details["progress"] == {"completed": index + 1, "total": 2}
    assert details["processing_status"] == "completed"
    assert details["available_actions"] == ["generate_requirements"]
    assert not details["blockers"]
    for index, document in enumerate(details["documents"]):
        assert document["status"] == "completed"
        assert request("GET", path + f'/documents/{document["id"]}/markdown')[1]["content"] == canonical(index)
        assert request("GET", path + f'/documents/{document["id"]}')[1]["status"] == "completed"
    with database.session_scope() as session:
        for index, revision_id in enumerate(revisions):
            file = session.get(File, revision_id)
            assert storage.artifact_path(file.markdown_path).read_text(encoding="utf-8") == MARKDOWN
            assert storage.artifact_path(file.entities_path).read_text(encoding="utf-8") == canonical(index)
            assert session.get(EntityFile, (report_id, index)).file_id == file.file_id
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 2


@pytest.mark.parametrize("per_document", [False, True])
def test_extraction_failure_can_be_retried_without_repeating_successes(ready, monkeypatch, per_document):
    report_id, revisions, pending = ready
    path = f"/reports/{report_id}"
    request("POST", path + "/run-entity-extraction")
    original = job.tag_document
    job.process_entities(revisions[0])

    def fail(*args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(job, "tag_document", fail)
    with pytest.raises(RuntimeError, match="LLM unavailable"):
        job.process_entities(revisions[1])
    response = request("GET", path)[1]
    assert response["processing_status"] == "failed"
    assert response["documents"][1]["status"] == "failed"
    assert response["documents"][1]["error"]
    assert response["blockers"] == [
        "Не удалось извлечь сущности из некоторых документов. Повторите извлечение."
    ]
    retry_path = path + (f'/documents/{response["documents"][1]["id"]}' if per_document else "")
    assert request("POST", retry_path + "/run-entity-extraction")[0] == 200
    assert pending == revisions + [revisions[1]]
    monkeypatch.setattr(job, "tag_document", original)
    job.process_entities(revisions[1])
    assert request("GET", path)[1]["processing_status"] == "completed"
    monkeypatch.setattr(job, "tag_document", fail)
    job.process_entities(revisions[0])  # Completed jobs are harmless if delivered twice.
    job.process_entities(999999)


def test_document_retry_leaves_other_failed_documents_untouched(ready):
    report_id, revisions, pending = ready
    path = f"/reports/{report_id}"
    request("POST", path + "/run-entity-extraction")
    with database.session_scope() as session:
        for revision_id in revisions:
            file = session.get(File, revision_id)
            file.status = "failed"
            file.error = "Extraction failed"
        reports.sync_processing_status(session, session.get(Report, report_id))
        document_id = session.get(File, revisions[0]).file_id

    retry_path = path + f"/documents/{document_id}/run-entity-extraction"
    status, response = request("POST", retry_path)
    assert status == 200
    assert pending == revisions + [revisions[0]]
    assert response["documents"][0]["status"] == "entities_queued"
    assert response["documents"][0]["error"] is None
    assert response["documents"][1]["status"] == "failed"
    assert response["documents"][1]["error"] == "Extraction failed"
    assert request("POST", retry_path)[0] == 200
    assert pending == revisions + [revisions[0]]
    job.process_entities(revisions[0])
    assert request("POST", retry_path)[0] == 200
    assert pending == revisions + [revisions[0]]
    assert request("GET", path)[1]["documents"][0]["status"] == "completed"


def test_document_retry_requires_entities_stage_and_report_ownership(ready):
    report_id, revisions, pending = ready
    path = f"/reports/{report_id}"
    with database.session_scope() as session:
        document_id = session.get(File, revisions[0]).file_id
    assert request("POST", path + f"/documents/{document_id}/run-entity-extraction")[0] == 409
    assert pending == []
    request("POST", path + "/run-entity-extraction")
    assert request("POST", path + "/documents/999999/run-entity-extraction")[0] == 404
    _, other = request("POST", "/reports", {"name": "Other report"})
    with database.session_scope() as session:
        session.get(Report, other["id"]).stage = "entities"
    assert request("POST", f'/reports/{other["id"]}/documents/{document_id}/run-entity-extraction')[0] == 404
    assert pending == revisions


def test_date_normalization_finishes_before_extraction_is_completed(ready, monkeypatch, llm_client):
    report_id, revisions, _ = ready
    request("POST", f"/reports/{report_id}/run-entity-extraction")
    monkeypatch.setattr(job, "tag_document", lambda *args, **kwargs: (
        '<ent t="date">Январь 2026</ent>', 0
    ))

    def normalize(*args, **kwargs):
        with database.session_scope() as session:
            file = session.get(File, revisions[0])
            assert file.status == "entities_running"
            assert file.entities_path is None
        return [{"raw": "Январь 2026", "normalized": "01.01.2026"}]

    monkeypatch.setattr(llm_client, "call_llm_json", normalize)
    job.process_entities(revisions[0])
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        assert file.status == "completed"
        assert storage.artifact_path(file.entities_path).read_text(encoding="utf-8") == (
            '<ent id="0" mnemonic="abandon-abandon" t="date" value="2026-01-01">Январь 2026</ent>'
        )


def test_normalization_failure_does_not_publish_unnormalized_extraction(ready, monkeypatch):
    report_id, revisions, _ = ready
    request("POST", f"/reports/{report_id}/run-entity-extraction")

    def fail(*args, **kwargs):
        raise RuntimeError("Date normalization unavailable")

    monkeypatch.setattr(job, "normalize_document", fail)
    with pytest.raises(RuntimeError, match="Date normalization unavailable"):
        job.process_entities(revisions[0])
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        assert file.status == "failed"
        assert file.entities_path is None
        assert storage.artifact_path(file.markdown_path).read_text(encoding="utf-8") == MARKDOWN


def test_enqueue_failure_is_persisted_and_retryable(ready, monkeypatch):
    report_id, revisions, _ = ready

    def fail(_):
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(entities, "enqueue_entities", fail)
    path = f"/reports/{report_id}"
    assert request("POST", path + "/run-entity-extraction")[0] == 503
    response = request("GET", path)[1]
    assert response["processing_status"] == "failed"
    assert all(file["status"] == "failed" for file in response["documents"])
    queued = []
    monkeypatch.setattr(entities, "enqueue_entities", queued.append)
    assert request("POST", path + "/run-entity-extraction")[0] == 200
    assert queued == revisions


def test_obsolete_revision_is_not_extracted(ready, monkeypatch):
    report_id, revisions, _ = ready
    request("POST", f"/reports/{report_id}/run-entity-extraction")
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        session.add(File(file_id=file.file_id, report_id=report_id, version=2, name=file.name,
                         path=file.path, hash=file.hash, role=file.role, size=file.size,
                         content_type=file.content_type, markdown_path=file.markdown_path,
                         status="ocr_completed"))

    def unexpected(*args, **kwargs):
        pytest.fail("An obsolete revision must not invoke the model")

    monkeypatch.setattr(job, "tag_document", unexpected)
    job.process_entities(revisions[0])


def test_extraction_calls_run_outside_report_transaction(ready, monkeypatch):
    report_id, revisions, _ = ready
    request("POST", f"/reports/{report_id}/run-entity-extraction")
    barrier = Barrier(2, timeout=10)
    original = job.tag_document

    def extract(*args, **kwargs):
        barrier.wait()  # Both jobs must claim the report before either finishes.
        with database.session_scope() as session:
            files = reports.report_documents(session, report_id)
            assert sum(file.status == "entities_running" for file in files) == 2
            assert session.get(Report, report_id).processing_status == "running"
        barrier.wait()
        return original(*args, **kwargs)

    monkeypatch.setattr(job, "tag_document", extract)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(job.process_entities, revisions))
    assert request("GET", f"/reports/{report_id}")[1]["processing_status"] == "completed"
    with database.session_scope() as session:
        files = reports.report_documents(session, report_id)
        identities = [identity for file in files for identity in read_identities(
            storage.artifact_path(file.entities_path).read_text(encoding="utf-8"))]
        assert {identity.num for identity in identities} == {0, 1}
        assert len({identity.mnemonic for identity in identities}) == 2
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 2
        assert len(list(session.scalars(select(EntityFile)))) == 2


def test_real_rq_uses_separate_entities_queue(ready, monkeypatch):
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to run the Redis/RQ integration test.")
    report_id, revisions, _ = ready
    connection = Redis.from_url(url)
    test_queue = Queue("test-entities-" + uuid4().hex, connection=connection)
    monkeypatch.setattr(queue, "entities_queue", lambda: test_queue)
    monkeypatch.setattr(entities, "enqueue_entities", queue.enqueue_entities)
    job_ids = []
    try:
        assert request("POST", f"/reports/{report_id}/run-entity-extraction")[0] == 200
        job_ids = test_queue.job_ids
        assert len(job_ids) == len(revisions)
        SimpleWorker([test_queue], connection=connection).work(burst=True, logging_level="WARNING")
        assert all(Job.fetch(job_id, connection=connection).is_finished for job_id in job_ids)
        assert request("GET", f"/reports/{report_id}")[1]["progress"] == {"completed": 2, "total": 2}
    finally:
        for job_id in job_ids:
            Job.fetch(job_id, connection=connection).delete()
        test_queue.delete(delete_jobs=True)
        connection.close()


def test_review_edits_preserve_revisions_and_checkier_fields(ready):
    report_id, revisions, _ = ready
    path = f"/reports/{report_id}"
    request("POST", path + "/run-entity-extraction")
    job.process_entities(revisions[0])
    with database.session_scope() as session:
        file_id = session.get(File, revisions[0]).file_id
    markdown_path = path + f"/documents/{file_id}/markdown"
    content = ('<ent id="0" mnemonic="abandon-abandon" t="amount" '
               'u="RUB_K" n="1000" value="250000.00">100,00</ent>')
    status, result = request("POST", markdown_path, {"content": content, "base_revision": 1})
    assert status == 200
    assert result == {"content": content, "revision": 2}
    assert request("GET", markdown_path)[1] == result
    assert request("POST", markdown_path, {"content": content, "base_revision": 1})[0] == 409
    invalid = '<ent t="id">100</ent>'
    assert request("POST", markdown_path, {"content": invalid, "base_revision": 2})[0] == 422
    assert request("GET", markdown_path)[1] == result
    assert request("GET", path + f"/documents/{file_id}")[1]["user_edited"] is True
    with database.session_scope() as session:
        original = session.get(File, revisions[0])
        assert storage.artifact_path(original.markdown_path).read_text(encoding="utf-8") == MARKDOWN
        assert storage.artifact_path(original.entities_path).read_text(encoding="utf-8") == canonical()
        edited = reports.report_documents(session, report_id)[0]
        assert edited.version == 2 and edited.status == "completed"
        assert "/edited/" in edited.entities_path
        assert edited.markdown_path == original.markdown_path
        assert edited.log_path == original.log_path


def test_edit_requires_finished_processing_and_editable_stage(ready):
    report_id, revisions, _ = ready
    path = f"/reports/{report_id}"
    with database.session_scope() as session:
        file_id = session.get(File, revisions[0]).file_id
    markdown_path = path + f"/documents/{file_id}/markdown"
    request("POST", path + "/run-entity-extraction")
    payload = {"content": MARKDOWN, "base_revision": 1}
    assert request("POST", markdown_path, payload)[0] == 409
    job.process_entities(revisions[0])
    with database.session_scope() as session:
        session.get(Report, report_id).stage = "verification"
    assert request("POST", markdown_path, payload)[0] == 409


def test_edit_ocr_creates_revision_used_for_extraction(ready, monkeypatch):
    report_id, revisions, pending = ready
    path = f"/reports/{report_id}"
    with database.session_scope() as session:
        file_id = session.get(File, revisions[0]).file_id
    markdown_path = path + f"/documents/{file_id}/markdown"
    content = MARKDOWN + "\n"
    status, result = request("POST", markdown_path, {"content": content, "base_revision": 1})
    assert status == 200
    assert request("GET", markdown_path)[1] == {"content": content, "revision": 2}
    with database.session_scope() as session:
        edited = reports.report_documents(session, report_id)[0]
        assert edited.entities_path is None
        assert edited.status == "ocr_completed"
        assert edited.log_path == session.get(File, revisions[0]).log_path
        edited_id = edited.id
        assert storage.artifact_path(session.get(File, revisions[0]).markdown_path).read_text(
            encoding="utf-8") == MARKDOWN
    monkeypatch.setattr(entities, "enqueue_entities", pending.append)
    assert request("POST", path + "/run-entity-extraction")[0] == 200
    assert pending == [edited_id, revisions[1]]
    job.process_entities(revisions[0])  # The old OCR revision must stay obsolete.
    job.process_entities(edited_id)
    assert request("GET", markdown_path)[1]["content"] == canonical()


@pytest.mark.parametrize("change", ["stage", "revision", "requeued", "new_run", "deleted"])
@pytest.mark.parametrize("fails", [False, True])
def test_stale_extraction_cannot_publish_or_mark_replacement_failed(ready, monkeypatch, change, fails):
    report_id, revisions, _ = ready
    request("POST", f"/reports/{report_id}/run-entity-extraction")

    def normalize(*args, **kwargs):
        with database.session_scope() as session:
            file = session.get(File, revisions[0])
            if change == "stage":
                session.get(Report, report_id).stage = "documents"
            elif change == "revision":
                session.add(File(file_id=file.file_id, report_id=report_id, version=2, name=file.name,
                                 path=file.path, hash=file.hash, role=file.role, size=file.size,
                                 content_type=file.content_type, markdown_path=file.markdown_path,
                                 status="ocr_completed"))
            elif change == "requeued":
                file.status = "entities_queued"
            elif change == "new_run":
                file.status = "failed"
            else:
                session.delete(session.get(Report, report_id))
        if change == "new_run":
            # Another committed run has claimed the same revision. Its status
            # matches the old run, but its updated_at claim is different.
            with database.session_scope() as session:
                session.get(File, revisions[0]).status = "entities_running"
        if fails:
            raise RuntimeError("Obsolete normalization failed")
        return TAGGED

    monkeypatch.setattr(job, "normalize_document", normalize)
    if fails:
        with pytest.raises(RuntimeError, match="Obsolete normalization failed"):
            job.process_entities(revisions[0])
    else:
        job.process_entities(revisions[0])
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id) is None
        assert list(session.scalars(select(EntityFile))) == []
        file = session.get(File, revisions[0])
        if change == "deleted":
            assert file is None
        else:
            assert file.entities_path is None
            assert file.status == ("entities_queued" if change == "requeued" else "entities_running")
            assert file.error is None
    assert not list(storage.storage_root().rglob("entities/*.md"))


def test_full_reextraction_assigns_new_ids_and_preserves_previous_artifact(ready):
    report_id, revisions, _ = ready
    request("POST", f"/reports/{report_id}/run-entity-extraction")
    for revision in revisions:
        job.process_entities(revision)
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        previous_path = file.entities_path
        document_id = file.file_id
        file.status = "failed"
    assert request("POST", f"/reports/{report_id}/documents/{document_id}/run-entity-extraction")[0] == 200
    job.process_entities(revisions[0])
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        assert file.status == "completed"
        assert file.entities_path != previous_path
        assert storage.artifact_path(previous_path).read_text(encoding="utf-8") == canonical(0)
        assert storage.artifact_path(file.entities_path).read_text(encoding="utf-8") == canonical(2)
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 3
        assert session.get(EntityFile, (report_id, 0)) is None
        assert session.get(EntityFile, (report_id, 1)) is not None
        assert session.get(EntityFile, (report_id, 2)).file_id == document_id
    job.process_entities(revisions[0])  # A duplicate delivery must not consume IDs.
    assert request("GET", f"/reports/{report_id}/documents/{document_id}/markdown")[1]["content"] == canonical(2)
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 3


def test_failed_publication_rolls_back_ids_and_preserves_current_markup(ready, monkeypatch):
    report_id, revisions, _ = ready
    request("POST", f"/reports/{report_id}/run-entity-extraction")
    job.process_entities(revisions[0])
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        previous_path = file.entities_path
        document_id = file.file_id
        file.status = "failed"
    request("POST", f"/reports/{report_id}/documents/{document_id}/run-entity-extraction")

    def fail(*args, **kwargs):
        raise OSError("Storage unavailable")

    monkeypatch.setattr(job, "save_entities", fail)
    with pytest.raises(OSError, match="Storage unavailable"):
        job.process_entities(revisions[0])
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        assert file.status == "failed"
        assert file.entities_path == previous_path
        assert storage.artifact_path(previous_path).read_text(encoding="utf-8") == canonical()
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 1
        assert session.get(EntityFile, (report_id, 0)).file_id == document_id
        assert session.get(EntityFile, (report_id, 1)) is None


def test_extraction_processor_cannot_supply_its_own_identity(ready, monkeypatch):
    report_id, revisions, _ = ready
    request("POST", f"/reports/{report_id}/run-entity-extraction")
    monkeypatch.setattr(job, "normalize_document", lambda *args, **kwargs: canonical())
    with pytest.raises(HTTPException, match="идентификаторы"):
        job.process_entities(revisions[0])
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id) is None
        assert session.get(File, revisions[0]).entities_path is None
