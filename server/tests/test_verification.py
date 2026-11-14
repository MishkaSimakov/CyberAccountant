"""Report verification uses reviewed documents and one combined evaluator call."""

import os
import json
from datetime import date
from uuid import uuid4

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.job import Job
from sqlalchemy import func, select

from processing.common import llm
from processing.common.llm import ModelClient

from app import database, queue, storage, verification
from app.jobs.verification import process_verification
from app.jobs import requirements as requirement_job
from app.jobs.requirements import process_requirement_generation
from processing.common.requirements import FormulaDefinition, AssertionDefinition
from processing.common.errors import ProcessingValidationError
from processing.requirements.processor import RequirementsProcessor
from types import SimpleNamespace
from app.models import EntityFile, File, Report, Requirement, VerificationResult
from app.services import requirements, reports
from test_documents import api, request, upload
from entity_fixtures import finalize_drafts
from processing.common.markup import parse_entity_tags


@pytest.mark.parametrize("label, matches", [("3 этап, 2026 г.", True), ("3 этап, 2025 г.", False)])
def test_prepare_verification_uses_exact_report_target(tmp_path, monkeypatch, label, matches):
    monkeypatch.setattr(storage, "artifact_path", lambda path: tmp_path / path)
    text = finalize_drafts('| 3 этап, 2026 г. | <ent t="amount">100</ent> |\n')
    (tmp_path / "card.md").write_text(text, encoding="utf-8")
    report = Report(id=1, target_row=label)
    file = File(report_id=1, file_id=1, name="card.pdf", role="final_report", version=1, entities_path="card.md")

    if matches:
        context = verification.prepare_verification(report, [file])
        assert context.scope["target_row"] == [0]
        assert context.scope["target_label"] == label
    else:
        with pytest.raises(ValueError, match="Не удалось найти проверяемую строку"):
            verification.prepare_verification(report, [file])


@pytest.fixture
def reviewed(api, monkeypatch, llm_client):
    report_id, revisions = api
    upload(report_id, role="final_report", content=b"report")
    upload(report_id, content=b"supporting")
    with database.session_scope() as session:
        report = session.get(Report, report_id)
        report.stage = "entities"
        report.processing_status = "completed"
        report.target_row = "Итого"
        next_id = 0
        for revision_id, text in zip(revisions, [
            'Итого: <ent t="amount" u="RUB_K" n="1000" value="110">0,11</ent>',
            'Сумма: <ent t="amount" u="RUB" value="100">100</ent>',
        ]):
            file = session.get(File, revision_id)
            count = len(parse_entity_tags(text))
            text = finalize_drafts(text, next_id)
            next_id += count
            file.entities_path = storage.save_entities(report_id, file.file_id, file.version, text)
            file.markdown_path = file.entities_path
            file.status = "completed"
    pending = []
    monkeypatch.setattr(requirements, "enqueue_verification", pending.append)
    monkeypatch.setattr(requirements, "enqueue_requirement_generation", pending.append)
    monkeypatch.setattr(llm_client, "call_llm_json", lambda *args, **kwargs: [
        {"equation": "#0 == #1", "tag": "agg"},
    ])
    return report_id, revisions, pending


def generate(report_id):
    requirements.generate_requirements(report_id)
    process_requirement_generation(report_id)


def test_verification_publishes_results_and_preserves_reviewed_markup(reviewed, monkeypatch, llm_client):
    report_id, revisions, pending = reviewed
    path = f"/reports/{report_id}"
    prompts = []

    def generate(prompt, **kwargs):
        prompts.append(prompt)
        with database.session_scope() as session:
            assert session.get(Report, report_id).processing_status == "running"
            assert session.scalar(select(func.count()).select_from(Requirement)) == 0
        return [{"equation": "#0 == #1", "tag": "agg"}]

    monkeypatch.setattr(llm_client, "call_llm_json", generate)
    assert request("GET", path)[1]["available_actions"] == ["generate_requirements"]
    assert request("GET", path + "/requirements")[1] == {"requirements": [], "entity_documents": {}}
    assert request("GET", path + "/verification-result")[0] == 409
    assert request("POST", path + "/run-verification")[0] == 409
    status, details = request("POST", path + "/generate-requirements")
    assert status == 200
    assert details["stage"] == "requirements"
    assert details["processing_status"] == "queued"
    assert request("POST", path + "/generate-requirements")[0] == 200
    process_requirement_generation(report_id)
    assert request("GET", path + "/verification-result")[0] == 409
    assert len(prompts) == 1
    status, details = request("POST", path + "/run-verification")
    assert status == 200
    assert details["processing_status"] == "queued"
    assert details["processing_mode"] == "verification"
    assert details["progress"] == {"completed": 0, "total": 1}
    assert request("POST", path + "/run-verification")[0] == 200
    assert pending == [report_id, report_id]
    process_verification(report_id)
    details = request("GET", path)[1]
    assert details["processing_status"] == "completed"
    assert details["progress"] == {"completed": 1, "total": 1}
    definitions = request("GET", path + "/requirements")[1]["requirements"]
    assert len(definitions) == 1
    assert definitions[0]["kind"] == "formula"
    assert definitions[0]["text"] is None
    assert "verdict" not in definitions[0]
    assert "error" not in definitions[0]
    status, result = request("GET", path + "/verification-result")
    assert status == 200
    assert result["requirement_results"][0]["requirement_id"] == definitions[0]["id"]
    assert result["requirement_results"][0]["verdict"] == "false"
    assert result["relations"][0]["status"] == "false"
    assert result["summary"]["relations_false_on_card"] == 1
    cell = result["card"]["cells"][0]
    assert cell["status"] == "mismatch"
    assert (cell["num"], cell["stated"], cell["expected"], cell["diff"]) == (0, "110", "100", "10")
    assert cell["unit"] == "RUB_K"
    assert len(prompts) == 1
    assert '<ent id="0" t="amount" u="RUB_K" n="1000" value="110">0,11</ent>' in prompts[0]
    assert '<e id=' not in prompts[0]
    assert "mnemonic" not in prompts[0]
    assert '"target_row": [0]' in prompts[0]
    with database.session_scope() as session:
        file = session.get(File, revisions[0])
        file_id = file.file_id
        assert 'id="0"' in storage.artifact_path(file.entities_path).read_text(encoding="utf-8")
        assert 'mnemonic="abandon-abandon"' in storage.artifact_path(file.entities_path).read_text(encoding="utf-8")
        assert session.get(EntityFile, (report_id, 0)).file_id == file_id
        record = session.get(VerificationResult, report_id)
        assert json.loads(storage.artifact_path(record.path).read_text()) == result
        assert session.scalar(select(func.count()).select_from(Requirement)) == 1
        assert session.scalar(select(Requirement)).json_data == {"kind": "formula", "equation": "#0 == #1",
                                                                 "tag": "agg"}
    assert result["entity_documents"]["0"] == file_id
    assert '<ent id="0"' in request("GET", path + f"/documents/{file_id}/markdown")[1]["content"]
    assert request("POST", path + f"/documents/{file_id}/markdown", {
        "content": "edited", "base_revision": 1,
    })[0] == 409
    process_verification(report_id)
    process_verification(999999)
    assert request("POST", path + "/run-verification")[0] == 200
    assert len(prompts) == 1 and pending == [report_id, report_id]


def test_generators_share_context_and_all_requirements_reach_one_solver(reviewed, monkeypatch):
    report_id, revisions, _ = reviewed
    with database.session_scope() as session:
        next_id = 0
        for revision_id, text in zip(revisions, [
            'Итого: <ent t="amount" value="70">70</ent> <ent t="amount" value="30">30</ent>',
            'Сумма: <ent t="amount" value="100">100</ent> Разница: <ent t="amount" value="40">40</ent>',
        ]):
            file = session.get(File, revision_id)
            count = len(parse_entity_tags(text))
            text = finalize_drafts(text, next_id)
            next_id += count
            storage.artifact_path(file.entities_path).write_text(text, encoding="utf-8")
    contexts = []

    def first(context, *, logger):
        contexts.append(context)
        return [FormulaDefinition(equation="#0 + #1 == #2", source="first")]

    def second(context, *, logger):
        contexts.append(context)
        return [FormulaDefinition(equation="#0 - #1 == #3", source="second")]

    monkeypatch.setattr(requirement_job, "create_requirement_processor", lambda: RequirementsProcessor(
        generators=[SimpleNamespace(generate=first), SimpleNamespace(generate=second)]))
    original = verification.solve_system
    calls = []

    def solve(all_requirements, registry, **kwargs):
        calls.append(all_requirements)
        assert len(contexts) == 2
        return original(all_requirements, registry, **kwargs)

    monkeypatch.setattr(verification, "solve_system", solve)
    generate(report_id)
    requirements.run_verification(report_id)
    process_verification(report_id)
    assert contexts[0] is contexts[1]
    assert len(calls) == 1
    assert {item["source"] for item in calls[0]} == {"first", "second"}
    cells = request("GET", f"/reports/{report_id}/verification-result")[1]["card"]["cells"]
    assert len(cells) == 2 and all(item["status"] == "ok" for item in cells)


def test_scope_requirements_are_published_and_failures_exclude_files(reviewed):
    report_id, revisions, _ = reviewed
    with database.session_scope() as session:
        report = session.get(Report, report_id)
        report.contract_number = "TARGET"
        report.period_start, report.period_end = date(2026, 1, 1), date(2026, 5, 31)
        file = session.get(File, revisions[1])
        document_id = str(file.file_id)
        storage.artifact_path(file.entities_path).write_text(
            finalize_drafts('Сумма: <ent t="amount" value="100">100</ent> '
                            'Договор: <ent t="contract">OTHER</ent> '
                            'Дата: <ent t="date">03.06.2026</ent>', 1), encoding="utf-8")

    generate(report_id)
    requirements.run_verification(report_id)
    process_verification(report_id)
    definitions = request("GET", f"/reports/{report_id}/requirements")[1]["requirements"]
    checks = [item for item in definitions if item["tag"] == "scope"]
    assert len(checks) == 2
    assert all(item["kind"] == "formula" and item["source"] == "scope" for item in checks)
    result = request("GET", f"/reports/{report_id}/verification-result")[1]
    outcomes = {item["requirement_id"]: item["verdict"] for item in result["requirement_results"]}
    assert all(outcomes[item["id"]] == "false" for item in checks)
    assert result["files"]["out_of_scope"] == {document_id: [item["equation"] for item in checks]}
    assert result["summary"]["out_of_scope"] == 1
    assert result["scope"] == {"target_row": [0], "target_label": "Итого"}


def test_failed_generation_is_retryable_without_partial_results(reviewed, monkeypatch):
    report_id, _, pending = reviewed
    original = requirement_job.create_requirement_processor

    def fail(_context, *, logger):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(requirement_job, "create_requirement_processor", lambda: SimpleNamespace(process=fail))
    requirements.generate_requirements(report_id)
    with pytest.raises(RuntimeError, match="LLM unavailable"):
        process_requirement_generation(report_id)
    assert reports.report_details(report_id)["processing_status"] == "failed"
    assert reports.report_details(report_id)["available_actions"] == ["generate_requirements"]
    with database.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Requirement)) == 0
        assert session.scalar(select(func.count()).select_from(EntityFile)) == 0
        assert session.get(VerificationResult, report_id) is None
    monkeypatch.setattr(requirement_job, "create_requirement_processor", original)
    generate(report_id)
    assert pending == [report_id, report_id]
    assert reports.report_details(report_id)["stage"] == "requirements"
    assert reports.report_details(report_id)["processing_status"] == "completed"


def test_requirement_model_calls_append_to_one_report_log_across_failed_runs(reviewed, monkeypatch, llm_client):
    report_id, revisions, _ = reviewed
    path = f"/reports/{report_id}"
    prompts = []
    outputs = []
    failing = True

    def respond(prompt, options, **kwargs):
        prompts.append(prompt)
        output = ("Некорректный ответ\r\n" * 4_000 if failing
                  else '[{"equation": "#0 == #1", "tag": "agg"}]')
        outputs.append(output)
        return output

    monkeypatch.setattr(llm_client.adapter, "generate", respond)
    monkeypatch.setattr(llm_client, "call_llm_json", ModelClient.call_llm_json.__get__(llm_client))
    with database.session_scope() as session:
        log_path = session.get(Report, report_id).log_path
        assert log_path
        assert all(session.get(File, revision).log_path != log_path for revision in revisions)
    for run in range(1, 3):
        assert request("POST", path + "/generate-requirements")[0] == 200
        with pytest.raises(llm.LLMError, match="validation failed after retries"):
            process_requirement_generation(report_id)
        details = request("GET", path)[1]
        assert details["stage"] == "requirements" and details["processing_status"] == "failed"
        assert details["available_actions"] == ["generate_requirements"]
        assert "Некорректный ответ" not in json.dumps(details, ensure_ascii=False)
        status, content = request("GET", path + "/log")
        assert status == 200
        assert content.count(b"\nRUN ") == run
        assert content.count(b"--- MODEL PROMPT |") == run * 3
        assert content.count(b"--- MODEL OUTPUT |") == run * 3
        assert content.count(b"ERROR (LLMError):") == run
        assert all(prompt.encode("utf-8") in content for prompt in prompts)
        assert all(output.encode("utf-8") in content for output in outputs)
        with database.session_scope() as session:
            assert session.scalar(select(func.count()).select_from(Requirement)) == 0
            assert all(storage.artifact_path(session.get(File, revision).log_path).read_bytes() == b""
                       for revision in revisions)
    failed_log = content
    failing = False
    assert request("POST", path + "/generate-requirements")[0] == 200
    process_requirement_generation(report_id)
    assert request("GET", path)[1]["processing_status"] == "completed"
    status, content = request("GET", path + "/log")
    assert status == 200 and content.startswith(failed_log)
    assert content.count(b"\nRUN ") == 3
    assert content.count(b"--- MODEL PROMPT |") == len(prompts) == 7
    assert content.count(b"--- MODEL OUTPUT |") == len(outputs) == 7
    with database.session_scope() as session:
        assert session.get(Report, report_id).log_path == log_path
    _, other = request("POST", "/reports", {"name": "Other report"})
    assert request("GET", f'/reports/{other["id"]}/log') == (200, b"")


def test_report_log_endpoint_serves_plain_text_and_handles_missing_logs(api):
    from app.api.reports import report_log
    from app.logging import append_log

    report_id, _ = api
    path = f"/reports/{report_id}/log"
    assert request("GET", path) == (200, b"")
    with database.session_scope() as session:
        log_path = storage.artifact_path(session.get(Report, report_id).log_path)
    text = "<script>raw report log</script>\r\nЖурнал отчёта\n"
    append_log(log_path, text)
    assert request("GET", path) == (200, text.encode("utf-8"))
    response = report_log(report_id)
    assert response.media_type == "text/plain; charset=utf-8"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    log_path.unlink()
    assert request("GET", path)[0] == 404
    assert request("GET", "/reports/999999/log")[0] == 404


def test_enqueue_failure_and_incomplete_documents(reviewed, monkeypatch):
    report_id, revisions, _ = reviewed
    path = f"/reports/{report_id}"
    with database.session_scope() as session:
        session.get(File, revisions[0]).status = "entities_running"
    assert request("POST", path + "/generate-requirements")[0] == 409
    with database.session_scope() as session:
        session.get(File, revisions[0]).status = "completed"

    def fail(_report_id):
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(requirements, "enqueue_requirement_generation", fail)
    assert request("POST", path + "/generate-requirements")[0] == 503
    assert reports.report_details(report_id)["processing_status"] == "failed"


def test_requirement_annotations_are_partial_and_scoped(reviewed):
    report_id, _, _ = reviewed
    generate(report_id)
    requirements.run_verification(report_id)
    process_verification(report_id)
    item = requirements.report_requirements(report_id).requirements[-1]
    result = request("GET", f"/reports/{report_id}/verification-result")[1]
    path = f"/reports/{report_id}/requirements/{item.id}"
    assert request("PATCH", path, {"notes": "Review this"})[1]["notes"] == "Review this"
    updated = request("PATCH", path, {"silenced": True})[1]
    assert updated["silenced"] and updated["notes"] == "Review this"
    assert "verdict" not in updated
    assert request("GET", f"/reports/{report_id}/verification-result")[1] == result
    _, other = request("POST", "/reports", {"name": "Other"})
    assert request("PATCH", f'/reports/{other["id"]}/requirements/{item.id}', {"notes": "Wrong"})[0] == 404
    assert request("POST", f'/reports/{other["id"]}/run-verification')[0] == 409


@pytest.mark.parametrize("raw", [
    {"equation": "#999 == #0"}, {"equation": "#0 + #1"}, {"equation": "32.01.2026 == #0"},
    {"evidence": "Unknown", "ids_related": ["999"], "verdict": "unverifiable"},
])
def test_invalid_generated_definitions_are_rejected_before_publication(reviewed, monkeypatch, llm_client, raw):
    report_id, _, _ = reviewed
    monkeypatch.setattr(llm_client, "call_llm_json", lambda *args, **kwargs: [raw])
    with pytest.raises(ProcessingValidationError):
        generate(report_id)
    assert reports.report_details(report_id)["processing_status"] == "failed"
    assert requirements.report_requirements(report_id).requirements == []


def test_assertions_do_not_constrain_formulas_and_keep_requirement_ids(reviewed, monkeypatch):
    report_id, _, _ = reviewed

    def mixed(source, **kwargs):
        return [
            AssertionDefinition(condition=False, text="Missing signature", ids_related=(1,), source="mixed"),
            FormulaDefinition(equation="#0 == #1", text="Сумма по документам", tag="agg", source="mixed"),
            AssertionDefinition(condition=True, text="Signature present", ids_related=(0,), source="mixed"),
            AssertionDefinition(condition=None, text="Insufficient data", ids_related=(0,), source="mixed"),
        ]

    monkeypatch.setattr(requirement_job, "create_requirement_processor", lambda: RequirementsProcessor(
        generators=[SimpleNamespace(generate=mixed)]))
    generate(report_id)
    requirements.run_verification(report_id)
    process_verification(report_id)
    definitions = requirements.report_requirements(report_id).requirements
    result = request("GET", f"/reports/{report_id}/verification-result")[1]
    outcomes = result["requirement_results"]
    assert [item["requirement_id"] for item in outcomes] == [item.id for item in definitions]
    assert [item["verdict"] for item in outcomes] == ["false", "false", "true", "unverifiable"]
    assert result["card"]["cells"][0]["expected"] == "100"
    assert definitions[1].text == "Сумма по документам"


def test_result_write_failure_keeps_definitions_and_can_be_retried(reviewed, monkeypatch):
    report_id, _, _ = reviewed
    original = storage.save_verification_result

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "save_verification_result", fail)
    generate(report_id)
    requirements.run_verification(report_id)
    with pytest.raises(OSError, match="disk full"):
        process_verification(report_id)
    with database.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Requirement)) == 1
        assert session.get(VerificationResult, report_id) is None
    assert request("GET", f"/reports/{report_id}/verification-result")[0] == 409
    monkeypatch.setattr(storage, "save_verification_result", original)
    requirements.run_verification(report_id)
    process_verification(report_id)
    result = request("GET", f"/reports/{report_id}/verification-result")[1]
    definitions = requirements.report_requirements(report_id).requirements
    assert len(definitions) == 1
    assert result["requirement_results"][0]["requirement_id"] == definitions[0].id


def test_real_rq_processes_verification(reviewed, monkeypatch):
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to run the Redis/RQ integration test.")
    report_id, _, _ = reviewed
    connection = Redis.from_url(url)
    test_queue = Queue("test-verification-" + uuid4().hex, connection=connection)
    monkeypatch.setattr(queue, "verification_queue", lambda: test_queue)
    monkeypatch.setattr(requirements, "enqueue_verification", queue.enqueue_verification)
    generate(report_id)
    try:
        requirements.run_verification(report_id)
        job_id = test_queue.job_ids[0]
        SimpleWorker([test_queue], connection=connection).work(burst=True, logging_level="WARNING")
        assert Job.fetch(job_id, connection=connection).is_finished
        assert reports.report_details(report_id)["processing_status"] == "completed"
    finally:
        test_queue.delete(delete_jobs=True)
        connection.close()


def test_requirement_editor_crud_validation_and_rerun_publish_new_path(reviewed, monkeypatch):
    report_id, _, _ = reviewed
    generate(report_id)
    path = f"/reports/{report_id}"
    definitions = request("GET", path + "/requirements")[1]
    assert len(definitions["entity_documents"]) == 2
    item = definitions["requirements"][0]
    formula_path = path + f'/requirements/{item["id"]}'

    status, validation = request("POST", path + "/requirements/validate-formula", {"equation": "#0 = #1"})
    assert status == 200 and not validation["valid"] and validation["error"]
    assert request("POST", path + "/requirements/validate-formula", {"equation": "#0 == #1"})[1] == {
        "valid": True, "error": None,
    }
    assert request("PUT", formula_path, {"kind": "formula", "equation": "#999 == 1"})[0] == 422
    assert request("PUT", formula_path, {"kind": "formula", "equation": "#0 == #1 + 10", "text": "Updated"})[0] == 200
    assert request("PATCH", formula_path, {"notes": "Keep this", "silenced": True})[0] == 200
    assert request("POST", path + "/requirements", {
        "kind": "assertion", "text": "Unknown reference", "condition": None, "ids_related": [999],
    })[0] == 422
    status, assertion = request("POST", path + "/requirements", {
        "kind": "assertion", "text": "Signature is present", "condition": False,
        "ids_related": ["#1", "abandon-abandon"],
    })
    assert status == 201 and assertion["source"] == "manual"
    assert assertion["ids_related"] == [1, 0]
    assertion_path = path + f'/requirements/{assertion["id"]}'
    assert request("PUT", assertion_path, {
        "kind": "assertion", "text": "Signature was reviewed", "condition": True,
    })[1]["condition"] is True
    status, temporary = request("POST", path + "/requirements", {"kind": "formula", "equation": "#0 >= 0"})
    assert status == 201
    assert request("DELETE", path + f'/requirements/{temporary["id"]}')[0] == 204
    assert request("POST", path + "/requirements", {"kind": "assertion", "text": "  ", "condition": None})[0] == 422
    assert request("POST", path + "/requirements", {"kind": "assertion", "text": "Text", "condition": "false"})[
               0] == 422

    def forbidden(_context):
        pytest.fail("Verification must not regenerate requirements")

    monkeypatch.setattr(requirement_job, "create_requirement_processor", forbidden)
    assert request("POST", path + "/run-verification")[0] == 200
    assert request("PUT", formula_path, {"kind": "formula", "equation": "#0 == 10"})[0] == 409
    assert request("DELETE", assertion_path)[0] == 409
    assert request("POST", path + "/requirements", {"kind": "formula", "equation": "#0 == 10"})[0] == 409
    assert request("POST", path + "/edit-requirements")[0] == 409
    assert request("GET", path + "/verification-result")[0] == 409
    process_verification(report_id)
    first = request("GET", path + "/verification-result")[1]
    with database.session_scope() as session:
        first_path = session.get(VerificationResult, report_id).path
    assert first["card"]["cells"][0]["expected"] == "110"

    status, report = request("POST", path + "/edit-requirements")
    assert status == 200 and report["stage"] == "requirements"
    assert report["processing_status"] == "completed"
    assert request("GET", path + "/verification-result")[0] == 409
    updated = request("PUT", formula_path, {"kind": "formula", "equation": "#0 == #1"})[1]
    assert updated["id"] == item["id"] and updated["notes"] == "Keep this" and updated["silenced"]
    assert request("POST", path + "/run-verification")[0] == 200
    process_verification(report_id)
    second = request("GET", path + "/verification-result")[1]
    assert second["card"]["cells"][0]["expected"] == "100"
    with database.session_scope() as session:
        second_path = session.get(VerificationResult, report_id).path
        assert first_path != second_path
        assert json.loads(storage.artifact_path(first_path).read_text()) == first
        assert json.loads(storage.artifact_path(second_path).read_text()) == second
    assert [outcome["requirement_id"] for outcome in second["requirement_results"]] == [item["id"], assertion["id"]]


def test_failed_rerun_keeps_previous_published_path_and_saved_edits(reviewed, monkeypatch):
    report_id, _, _ = reviewed
    generate(report_id)
    requirements.run_verification(report_id)
    process_verification(report_id)
    with database.session_scope() as session:
        first_path = session.get(VerificationResult, report_id).path
    original = storage.save_verification_result
    requirements.edit_requirements(report_id)
    item = requirements.report_requirements(report_id).requirements[0]
    path = f"/reports/{report_id}"
    assert request("PUT", path + f"/requirements/{item.id}", {"kind": "formula", "equation": "#0 == #1 + 1"})[0] == 200

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "save_verification_result", fail)
    requirements.run_verification(report_id)
    with pytest.raises(OSError, match="disk full"):
        process_verification(report_id)
    with database.session_scope() as session:
        assert session.get(VerificationResult, report_id).path == first_path
        assert storage.artifact_path(first_path).is_file()
    assert request("GET", path + "/verification-result")[0] == 409
    monkeypatch.setattr(storage, "save_verification_result", original)
    requirements.run_verification(report_id)
    process_verification(report_id)
    assert request("GET", path + "/verification-result")[1]["card"]["cells"][0]["expected"] == "101"


def test_requirement_crud_is_scoped_to_report(reviewed):
    report_id, _, _ = reviewed
    generate(report_id)
    item = requirements.report_requirements(report_id).requirements[0]
    _, other = request("POST", "/reports", {"name": "Other report"})
    with database.session_scope() as session:
        report = session.get(Report, other["id"])
        report.stage, report.processing_status = "requirements", "completed"
    path = f'/reports/{other["id"]}/requirements/{item.id}'
    assert request("PUT", path, {"kind": "assertion", "text": "Other", "condition": False})[0] == 404
    assert request("DELETE", path)[0] == 404
    assert len(requirements.report_requirements(report_id).requirements) == 1


def test_empty_edited_requirements_are_not_regenerated(reviewed, monkeypatch):
    report_id, _, _ = reviewed
    generate(report_id)
    item = requirements.report_requirements(report_id).requirements[0]
    requirements.delete_requirement(report_id, item.id)
    monkeypatch.setattr(requirement_job, "create_requirement_processor",
                        lambda: pytest.fail("Regenerated requirements"))
    requirements.run_verification(report_id)
    process_verification(report_id)
    assert request("GET", f"/reports/{report_id}/verification-result")[1]["requirement_results"] == []


@pytest.mark.parametrize("change", ["stage", "revision", "scope", "artifact", "requeued", "new_run", "deleted"])
@pytest.mark.parametrize("fails", [False, True])
def test_stale_generation_cannot_publish_or_fail_a_replacement(reviewed, monkeypatch, change, fails):
    report_id, revisions, _ = reviewed
    requirements.generate_requirements(report_id)

    def process(source, *, logger):
        with database.session_scope() as session:
            report = session.get(Report, report_id)
            file = session.get(File, revisions[0])
            if change == "stage":
                report.stage = "entities"
            elif change == "revision":
                session.add(File(file_id=file.file_id, report_id=report_id, version=2, name=file.name,
                                 path=file.path, hash=file.hash, role=file.role, size=file.size,
                                 content_type=file.content_type, entities_path=file.entities_path, status="completed"))
            elif change == "scope":
                report.contract_number = "Changed contract"
            elif change == "artifact":
                file.entities_path = storage.save_entities(report_id, file.file_id, 2,
                                                           source.corpus.documents[0].markdown)
            elif change == "requeued":
                report.processing_status = "queued"
            elif change == "new_run":
                report.processing_status = "failed"
            else:
                session.delete(report)
        if change == "new_run":
            with database.session_scope() as session:
                session.get(Report, report_id).processing_status = "running"
        if fails:
            raise RuntimeError("Obsolete generation failed")
        from processing.requirements.interface import RequirementOutput
        return RequirementOutput((FormulaDefinition(equation="#0 == #1", source="mock"),))

    monkeypatch.setattr(requirement_job, "create_requirement_processor", lambda: SimpleNamespace(process=process))
    if fails:
        with pytest.raises(RuntimeError, match="Obsolete generation failed"):
            process_requirement_generation(report_id)
    else:
        process_requirement_generation(report_id)
    with database.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Requirement)) == 0
        assert session.scalar(select(func.count()).select_from(EntityFile)) == 0
        current = session.get(Report, report_id)
        if change == "deleted":
            assert current is None
        else:
            assert current.processing_status == ("queued" if change == "requeued" else "running")


def test_generation_queue_uses_dedicated_job(monkeypatch):
    calls = []
    monkeypatch.setattr(queue, "verification_queue", lambda: SimpleNamespace(enqueue=lambda *args: calls.append(args)))
    queue.enqueue_requirement_generation(12)
    assert calls == [("app.jobs.requirements.process_requirement_generation", 12)]
