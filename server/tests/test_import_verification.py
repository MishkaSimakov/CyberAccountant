"""Bundle imports preserve references and reject bad inputs before persistence."""

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import import_verification as importer
from app import database, storage, verification
from app.models import EntityFile, File, Report, Requirement
from app.services import documents, requirements, reports
from checkier.formula import FormulaError


@pytest.fixture
def bundle(tmp_path):
    entities = tmp_path / "entities"
    (tmp_path / "23.md").write_text('Итого: <ent id="700" t="amount">110</ent>', encoding="utf-8")
    write_document(entities, "1", '<ent id="alpha-beta" t="amount">100</ent>')
    path = tmp_path / "requirements.json"
    path.write_text(json.dumps({"requirements": [
        {"id": 90, "kind": "formula", "equation": '#700 == alpha-beta && "#700" == "#700"',
         "tag": "agg", "source": "friend", "notes": "Reviewed", "silenced": True},
        {"id": 91, "kind": "assertion", "text": "Signature missing", "condition": False,
         "ids_related": ["#700", "alpha-beta"]},
    ]}), encoding="utf-8")
    return path, entities


def write_document(entities, document_id, text):
    path = entities / f"{document_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_remaps_explicit_ids_without_replacing_string_literals(bundle):
    path, entities = bundle
    docs = importer.load_documents(entities, path.parent / "23.md")
    definitions, registry = importer.remap_requirements(importer.load_requirements(path), docs,
                                                        {"23.md": "9", "1.md": "10"})
    assert definitions[0]["equation"] == '#1 == #0 && "#700" == "#700"'
    assert definitions[1]["ids_related"] == [1, 0]
    assert [(entity.file, entity.raw) for entity in registry.entities] == [("10", "100"), ("9", "110")]


def test_loads_nested_markdown_and_external_final_report(bundle):
    path, entities = bundle
    write_document(entities, "nested/2", '<ent id="701" t="amount">50</ent>')
    (entities / "ignored.json").write_text("{}", encoding="utf-8")
    docs = importer.load_documents(entities, path.parent / "23.md")
    assert {doc.name for doc in docs} == {"23.md", "1.md", "nested/2.md"}
    assert [doc.name for doc in docs if doc.role == "card"] == ["23.md"]


def test_final_report_inside_entities_is_loaded_once(bundle):
    path, entities = bundle
    final_report = entities / "23.md"
    (path.parent / "23.md").replace(final_report)
    docs = importer.load_documents(entities, final_report)
    assert len(docs) == 2
    assert [doc.name for doc in docs if doc.role == "card"] == ["23.md"]


def test_missing_final_report_raises(bundle):
    path, entities = bundle
    with pytest.raises(ValueError, match="Final report must be an existing Markdown file"):
        importer.load_documents(entities, path.parent / "missing.md")


@pytest.mark.parametrize("markup, message", [
    ('<ent t="amount">100</ent>', "without an id"),
    ('<ent id="700" t="amount">100</ent>', "Duplicate entity ID"),
    ('<ent id="-1" t="amount">100</ent>', "Invalid entity ID"),
    ('<ent id="1" id="2" t="amount">100</ent>', "duplicate entity attributes"),
])
def test_bad_entity_ids_stop_before_database_access(bundle, monkeypatch, markup, message):
    path, entities = bundle
    write_document(entities, "1", markup)
    monkeypatch.setattr(importer, "session_scope", lambda: pytest.fail("Must validate before accessing database"))
    with pytest.raises(ValueError, match=message):
        importer.import_bundle(path, entities, path.parent / "23.md")


@pytest.mark.parametrize("requirement, error", [
    ({"kind": "formula", "equation": "#999 == 10"}, ValueError),
    ({"kind": "assertion", "condition": None, "ids_related": [999]}, ValueError),
    ({"kind": "formula", "equation": "#700 = 10"}, FormulaError),
    ({"kind": "formula", "equation": "#700 + 10"}, ValueError),
    ({"kind": "formula", "equation": "#700 == 10", "verdict": "true"}, ValueError),
])
def test_bad_requirements_stop_before_database_access(bundle, monkeypatch, requirement, error):
    path, entities = bundle
    path.write_text(json.dumps([requirement]), encoding="utf-8")
    monkeypatch.setattr(importer, "session_scope", lambda: pytest.fail("Must validate before accessing database"))
    with pytest.raises(error):
        importer.import_bundle(path, entities, path.parent / "23.md")


def test_missing_target_row_stops_before_database_access(bundle, monkeypatch):
    path, entities = bundle
    monkeypatch.setattr(importer, "session_scope", lambda: pytest.fail("Must validate before accessing database"))
    with pytest.raises(ValueError, match="Target row not found"):
        importer.import_bundle(path, entities, path.parent / "23.md", target_row="Missing row")


def test_import_publishes_report_and_preserves_inputs(db, bundle, tmp_path, monkeypatch):
    path, entities = bundle
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    # Cross the 9/10 boundary: registry ordering sorts database file IDs as strings.
    for index in range(10):
        write_document(entities, f"extra-{index}", f'<ent id="{index}" t="number">{index}</ent>')
    monkeypatch.setattr(verification, "generate_requirements", lambda _: pytest.fail("Must use supplied requirements"))
    monkeypatch.setattr(verification, "generate_llm", lambda _: pytest.fail("Must not call LLM"))
    report_id, result_path = importer.import_bundle(path, entities, path.parent / "23.md",
                                                    name="Friend's bundle", target_row="Итого")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["summary"]["mismatch"] == 1
    assert result["card"]["cells"][0]["stated"] == "110"
    assert result["card"]["cells"][0]["expected"] == "100"
    assert [outcome["verdict"] for outcome in result["requirement_results"]] == ["false", "false"]
    assert len(result["entity_documents"]) == 12
    details = reports.report_details(report_id)
    assert details["stage"] == "verification"
    assert details["processing_status"] == "completed"
    assert details["blockers"] == []
    saved = requirements.report_requirements(report_id).requirements
    assert [requirement.id for requirement in saved] == [1, 2]
    assert saved[0].source == "friend"
    assert saved[0].notes == "Reviewed"
    assert saved[0].silenced is True
    assert storage.artifact_path(f"{report_id}/imported-requirements.json").read_bytes() == path.read_bytes()
    with database.session_scope() as session:
        files = reports.report_documents(session, report_id)
        assert session.scalar(select(func.count()).select_from(EntityFile)) == 12
        assert session.scalar(select(func.count()).select_from(Requirement)) == 2
    for file in files:
        source = path.parent / "23.md" if file.role == "final_report" else entities / file.name
        assert storage.artifact_path(file.path).read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    card = next(file for file in files if file.role == "final_report")
    assert card.version == 1
    assert '<ent id="' in documents.document_markdown(report_id, card.file_id).content
    assert requirements.verification_result_file(report_id) == result_path


def test_verification_failure_propagates_without_retry(db, bundle, tmp_path, monkeypatch):
    path, entities = bundle
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("Solver failed")

    monkeypatch.setattr(verification, "solve_system", fail)
    with pytest.raises(RuntimeError, match="Solver failed"):
        importer.import_bundle(path, entities, path.parent / "23.md")
    assert len(calls) == 1
    with database.session_scope() as session:
        report = session.scalar(select(Report))
        assert report.processing_status == "failed"
        assert session.scalar(select(func.count()).select_from(File)) == 2


def test_cli_prints_result_url(db, bundle, tmp_path, monkeypatch, capsys):
    path, entities = bundle
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    importer.main(["--entities", str(entities), "--final-report", str(path.parent / "23.md"),
                   "--base-url", "http://localhost:8000/"])
    with database.session_scope() as session:
        report_id = session.scalar(select(Report.id))
    output = capsys.readouterr().out
    assert f"http://localhost:8000/reports/{report_id}/requirements" in output
    assert '"mismatch": 1' in output
    assert "JSON: " in output
