"""Report verification uses reviewed documents and one combined evaluator call."""

import os
from uuid import uuid4

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.job import Job
from sqlalchemy import func, select

from app import database, queue, storage, verification
from app.jobs.verification import process_verification
from app.models import EntityFile, File, Report, Requirement
from app.services import requirements, reports
from test_documents import api, request, upload


@pytest.fixture
def reviewed(api, monkeypatch):
    report_id, revisions = api
    upload(report_id, role="final_report", content=b"report")
    upload(report_id, content=b"supporting")
    with database.session_scope() as session:
        report = session.get(Report, report_id)
        report.stage = "entities"
        report.processing_status = "completed"
        report.target_row = "Итого"
        for revision_id, text in zip(revisions, [
            'Итого: <ent t="amount" u="RUB_K" n="1000" value="110">0,11</ent>',
            'Сумма: <ent t="amount" u="RUB" value="100">100</ent>',
        ]):
            file = session.get(File, revision_id)
            file.entities_path = storage.save_entities(report_id, file.file_id, file.version, text)
            file.markdown_path = file.entities_path
            file.status = "completed"
    pending = []
    monkeypatch.setattr(requirements, "enqueue_verification", pending.append)
    monkeypatch.setattr("checkier.stage5_relations.call_llm_json", lambda *args, **kwargs: [
        {"equation": "#0 == #1", "tag": "agg"},
    ])
    return report_id, revisions, pending


def test_verification_publishes_results_and_preserves_reviewed_markup(reviewed, monkeypatch):
    report_id, revisions, pending = reviewed
    path = f"/reports/{report_id}"
    prompts = []

    def generate(prompt, **kwargs):
        prompts.append(prompt)
        with database.session_scope() as session:
            assert session.get(Report, report_id).processing_status == "running"
            assert session.scalar(select(func.count()).select_from(Requirement)) == 0
        return [{"equation": "#0 == #1", "tag": "agg"}]

    monkeypatch.setattr("checkier.stage5_relations.call_llm_json", generate)
    assert request("GET", path)[1]["available_actions"] == ["run_verification"]
    assert request("GET", path + "/requirements")[0] == 409
    status, details = request("POST", path + "/run-verification")
    assert status == 200
    assert details["processing_status"] == "queued"
    assert details["processing_mode"] == "verification"
    assert details["progress"] == {"completed": 0, "total": 1}
    assert request("POST", path + "/run-verification")[0] == 200
    assert pending == [report_id]
    process_verification(report_id)
    details = request("GET", path)[1]
    assert details["processing_status"] == "completed"
    assert details["progress"] == {"completed": 1, "total": 1}
    result = request("GET", path + "/requirements")[1]
    assert result["requirements"][0]["verdict"] == "true"
    cell = next(item for item in result["requirements"] if item["source"] == "evaluator")
    assert cell["verdict"] == "false"
    assert cell["ids_related"] == [0]
    assert "ожидается 100" in cell["evidence"]
    assert len(prompts) == 1
    assert '<ent id="0" t="amount" u="RUB_K" n="1000" value="110">0,11</ent>' in prompts[0]
    assert '<e id=' not in prompts[0]
    assert "mnemonic" not in prompts[0]
    assert '"target_row": [0]' in prompts[0]
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        file_id = file.file_id
        assert 'id="0"' not in storage.artifact_path(file.entities_path).read_text(encoding="utf-8")
        assert session.get(EntityFile, (report_id, 0)).file_id == file_id
    assert result["entity_documents"]["0"] == file_id
    assert '<ent id="0"' in request("GET", path + f"/documents/{file_id}/markdown")[1]["content"]
    assert request("POST", path + f"/documents/{file_id}/markdown", {
        "content": "edited", "base_revision": 1,
    })[0] == 409
    process_verification(report_id)
    process_verification(999999)
    assert request("POST", path + "/run-verification")[0] == 200
    assert len(prompts) == 1 and pending == [report_id]


def test_generators_share_context_and_all_requirements_reach_one_solver(reviewed, monkeypatch):
    report_id, revisions, _ = reviewed
    with database.session_scope() as session:
        for revision_id, text in zip(revisions, [
            'Итого: <ent t="amount" value="70">70</ent> <ent t="amount" value="30">30</ent>',
            'Сумма: <ent t="amount" value="100">100</ent> Разница: <ent t="amount" value="40">40</ent>',
        ]):
            file = session.get(File, revision_id)
            storage.artifact_path(file.entities_path).write_text(text, encoding="utf-8")
    contexts = []

    def first(context):
        contexts.append(context)
        return [{"equation": "#0 + #1 == #2"}]

    def second(context):
        contexts.append(context)
        return [{"equation": "#0 - #1 == #3"}]

    monkeypatch.setattr(verification, "GENERATORS", {"first": first, "second": second})
    original = verification.solve_system
    calls = []

    def solve(all_requirements, registry, **kwargs):
        calls.append(all_requirements)
        assert len(contexts) == 2
        return original(all_requirements, registry, **kwargs)

    monkeypatch.setattr(verification, "solve_system", solve)
    requirements.run_verification(report_id)
    process_verification(report_id)
    assert contexts[0] is contexts[1]
    assert len(calls) == 1
    assert {item["source"] for item in calls[0]} == {"first", "second"}
    cells = [item for item in requirements.requirements_result(report_id).requirements if item.source == "evaluator"]
    assert len(cells) == 2 and all(item.verdict == "true" for item in cells)


def test_failed_generation_is_retryable_without_partial_results(reviewed, monkeypatch):
    report_id, _, pending = reviewed
    original = verification.GENERATORS

    def fail(_context):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(verification, "GENERATORS", {"llm": fail})
    requirements.run_verification(report_id)
    with pytest.raises(RuntimeError, match="LLM unavailable"):
        process_verification(report_id)
    assert reports.report_details(report_id)["processing_status"] == "failed"
    assert reports.report_details(report_id)["available_actions"] == ["run_verification"]
    with database.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Requirement)) == 0
        assert session.scalar(select(func.count()).select_from(EntityFile)) == 0
    monkeypatch.setattr(verification, "GENERATORS", original)
    requirements.run_verification(report_id)
    process_verification(report_id)
    assert pending == [report_id, report_id]
    assert reports.report_details(report_id)["processing_status"] == "completed"


def test_enqueue_failure_and_incomplete_documents(reviewed, monkeypatch):
    report_id, revisions, _ = reviewed
    path = f"/reports/{report_id}"
    with database.session_scope() as session:
        session.get(File, revisions[0]).status = "entities_running"
    assert request("POST", path + "/run-verification")[0] == 409
    with database.session_scope() as session:
        session.get(File, revisions[0]).status = "completed"

    def fail(_report_id):
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(requirements, "enqueue_verification", fail)
    assert request("POST", path + "/run-verification")[0] == 503
    assert reports.report_details(report_id)["processing_status"] == "failed"


def test_requirement_annotations_are_partial_and_scoped(reviewed):
    report_id, _, _ = reviewed
    requirements.run_verification(report_id)
    process_verification(report_id)
    item = requirements.requirements_result(report_id).requirements[-1]
    path = f"/reports/{report_id}/requirements/{item.id}"
    assert request("PATCH", path, {"notes": "Review this"})[1]["notes"] == "Review this"
    updated = request("PATCH", path, {"silenced": True})[1]
    assert updated["silenced"] and updated["notes"] == "Review this"
    _, other = request("POST", "/reports", {"name": "Other"})
    assert request("PATCH", f'/reports/{other["id"]}/requirements/{item.id}', {"notes": "Wrong"})[0] == 404
    assert request("POST", f'/reports/{other["id"]}/run-verification')[0] == 409


def test_invalid_and_unverifiable_requirements_are_saved(reviewed, monkeypatch):
    report_id, _, _ = reviewed
    monkeypatch.setattr("checkier.stage5_relations.call_llm_json", lambda *args, **kwargs: [
        {"equation": "#999 == #0"},
        {"equation": "#0 + #1"},
        {"equation": "32.01.2026 == #0"},
        {"evidence": "Missing evidence", "ids_related": ["0"], "verdict": "unverifiable"},
    ])
    requirements.run_verification(report_id)
    process_verification(report_id)
    result = requirements.requirements_result(report_id).requirements
    assert [item.verdict for item in result[:3]] == ["error"] * 3
    assert all(item.error for item in result[:3])
    assert result[3].verdict == "unverifiable"
    assert result[-1].verdict == "unverifiable"


def test_real_rq_processes_verification(reviewed, monkeypatch):
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to run the Redis/RQ integration test.")
    report_id, _, _ = reviewed
    connection = Redis.from_url(url)
    test_queue = Queue("test-verification-" + uuid4().hex, connection=connection)
    monkeypatch.setattr(queue, "verification_queue", lambda: test_queue)
    monkeypatch.setattr(requirements, "enqueue_verification", queue.enqueue_verification)
    try:
        requirements.run_verification(report_id)
        job_id = test_queue.job_ids[0]
        SimpleWorker([test_queue], connection=connection).work(burst=True, logging_level="WARNING")
        assert Job.fetch(job_id, connection=connection).is_finished
        assert reports.report_details(report_id)["processing_status"] == "completed"
    finally:
        test_queue.delete(delete_jobs=True)
        connection.close()
