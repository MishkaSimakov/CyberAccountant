"""Bundle imports preserve references and reject bad inputs before persistence."""

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import import_verification as importer
from app import database, storage
from app.jobs import verification as verification_job
from app.models import EntityFile, File, Report, ReportEntityCounter, Requirement
from app.schemas.documents import MarkdownEdit
from processing.entities.identifiers import mnemonic_for_id, read_identities
from app.services import documents, requirements, reports, verification
from processing.common.formula import FormulaError


@pytest.fixture
def bundle(tmp_path):
    entities = tmp_path / "entities"
    (tmp_path / "23.md").write_text('Итого: <ent id="700" t="amount">110</ent>', encoding="utf-8")
    write_document(entities, "1", '<ent id="10" mnemonic="abandon-access" t="amount">100</ent>')
    path = tmp_path / "requirements.json"
    path.write_text(json.dumps({"requirements": [
        {"id": 90, "kind": "formula", "equation": '#700 == abandon-access && "#700" == "#700"',
         "tag": "agg", "source": "friend", "notes": "Reviewed", "silenced": True},
        {"id": 91, "kind": "assertion", "text": "Signature missing", "condition": False,
         "ids_related": ["#700", "abandon-access"]},
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
    assert definitions[0]["equation"] == '#700 == #10 && "#700" == "#700"'
    assert definitions[1]["ids_related"] == [700, 10]
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
    monkeypatch.setattr("app.jobs.requirements.create_requirement_processor",
                        lambda: pytest.fail("Must use supplied requirements"))
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
    assert verification.verification_result_file(report_id) == result_path
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 701
        canonical = storage.artifact_path(card.entities_path).read_text(encoding="utf-8")
        assert read_identities(canonical)[0].num == 700
        assert read_identities(canonical)[0].mnemonic == mnemonic_for_id(700)
        session.get(Report, report_id).stage = "entities"
    saved = documents.edit_markdown(report_id, card.file_id, MarkdownEdit(
        content=canonical + '\n<ent t="number">1</ent>', base_revision=1))
    assert [identity.num for identity in read_identities(saved.content)] == [700, 701]
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 702
        assert session.get(EntityFile, (report_id, 701)).file_id == card.file_id


def test_verification_failure_propagates_without_retry(db, bundle, tmp_path, monkeypatch):
    path, entities = bundle
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("Solver failed")

    monkeypatch.setattr(verification_job, "create_verification_processor", fail)
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


@pytest.mark.parametrize("identity", ['id="abandon-access"', 'mnemonic="abandon-access"', ''])
def test_import_allocates_missing_numeric_identities(db, bundle, tmp_path, monkeypatch, identity):
    path, entities = bundle
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    write_document(entities, "1", f'<ent {identity} t="amount">100</ent>')
    if not identity:
        path.write_text(json.dumps([{"kind": "formula", "equation": "#700 == 110"}]), encoding="utf-8")
    report_id, _ = importer.import_bundle(path, entities, path.parent / "23.md")
    with database.session_scope() as session:
        files = reports.report_documents(session, report_id)
        supporting = next(file for file in files if file.role == "supporting")
        canonical = storage.artifact_path(supporting.entities_path).read_text(encoding="utf-8")
        identity_, = read_identities(canonical)
        assert identity_.num == 701
        assert identity_.mnemonic == ("abandon-access" if identity else mnemonic_for_id(701))
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 702
        if identity:
            formula = next(session.scalars(select(Requirement))).json_data["equation"]
            assert formula == '#700 == #701 && "#700" == "#700"'


def test_import_preserves_complete_supplied_pairs_and_future_additions(db, bundle, tmp_path, monkeypatch):
    path, entities = bundle
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    final = path.parent / "23.md"
    original = 'Итого: <ent id="700" mnemonic="abandon-abandon" t="amount">110</ent>'
    final.write_text(original, encoding="utf-8")
    report_id, _ = importer.import_bundle(path, entities, final)
    with database.session_scope() as session:
        card = next(file for file in reports.report_documents(session, report_id) if file.role == "final_report")
        canonical = storage.artifact_path(card.entities_path).read_text(encoding="utf-8")
        assert read_identities(canonical)[0].num == 700
        assert read_identities(canonical)[0].mnemonic == "abandon-abandon"
        assert storage.artifact_path(card.path).read_text(encoding="utf-8") == original
        session.get(Report, report_id).stage = "entities"
    result = documents.edit_markdown(report_id, card.file_id, MarkdownEdit(
        content=canonical + '<ent t="number">2</ent>', base_revision=1))
    assert [identity.num for identity in read_identities(result.content)] == [700, 701]


@pytest.mark.parametrize("markup", [
    '<ent id="10" mnemonic="abandon-abandon" t="amount">100</ent>',
    '<ent id="10" mnemonic="not-a-word" t="amount">100</ent>',
    '<ent id="abandon-access" mnemonic="abandon-able" t="amount">100</ent>',
])
def test_conflicting_import_mnemonics_fail_before_persistence(bundle, monkeypatch, markup):
    path, entities = bundle
    final = path.parent / "23.md"
    final.write_text('Итого: <ent id="700" mnemonic="abandon-abandon" t="amount">110</ent>', encoding="utf-8")
    write_document(entities, "1", markup)
    monkeypatch.setattr(importer, "session_scope", lambda: pytest.fail("Must validate before accessing database"))
    with pytest.raises(ValueError):
        importer.import_bundle(path, entities, final)
