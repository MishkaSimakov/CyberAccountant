"""Reviewed Markdown retains document-owned identities and assigns only drafts."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select

from app import database, storage
from app.verification import prepare_verification, validation_registry
from app.models import EntityFile, File, ReportEntityCounter
from app.schemas.documents import MarkdownEdit
from app.services import documents, reports
from app.jobs.entities import process_entities
from processing.entities.identifiers import assign_identities, read_identities
from test_documents import api, request
from test_entities import canonical, ready


@pytest.fixture
def extracted(ready):
    report_id, revisions, _ = ready
    assert request("POST", f"/reports/{report_id}/run-entity-extraction")[0] == 200
    for revision in revisions:
        process_entities(revision)
    with database.session_scope() as session:
        files = reports.report_documents(session, report_id)
        return report_id, [file.file_id for file in files]


def test_multiple_additions_preserve_old_identity_and_source(extracted):
    report_id, files = extracted
    path = f"/reports/{report_id}/documents/{files[0]}/markdown"
    draft = '<ent t="number" value="10">**10** &amp; more</ent>'
    # Add before and after an existing entity; display order does not set IDs.
    content = draft + "\n" + canonical().replace('value="100.00"', 'value="250.00"') + draft
    status, result = request("POST", path, {"content": content, "base_revision": 1})
    assert status == 200
    assert result == {"content": assign_identities(content, range(2, 4)), "revision": 2}
    assert [identity.num for identity in read_identities(result["content"])] == [2, 0, 3]
    assert request("GET", path)[1] == result
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 4
        index = {entry.entity_id: entry.file_id for entry in session.scalars(select(EntityFile))}
        assert index == {0: files[0], 1: files[1], 2: files[0], 3: files[0]}
    # Saving the server's assigned tags again must not allocate more IDs.
    assert request("POST", path, {"content": result["content"], "base_revision": 2})[0] == 200
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 4


def test_reviewed_identities_survive_requirement_preparation(extracted):
    report_id, files = extracted
    path = f"/reports/{report_id}/documents/{files[0]}/markdown"
    content = '<ent t="number">10</ent>\n' + canonical()
    status, result = request("POST", path, {"content": content, "base_revision": 1})
    assert status == 200
    with database.session_scope() as session:
        documents = reports.report_documents(session, report_id)
        report = reports.find_report(session, report_id)
        registry = validation_registry(documents)
        assert [entity.num for entity in registry.entities] == [2, 0, 1]
        context = prepare_verification(report, documents)
        assert context.documents[str(files[0])] == result["content"]
        assert [entity.num for entity in context.registry.entities] == [2, 0, 1]
        assert [entity.mnemonic for entity in context.registry.entities] == [
            identity.mnemonic for document in documents for identity in read_identities(
                storage.artifact_path(document.entities_path).read_text(encoding="utf-8"))]
    assert request("GET", path)[1] == result


@pytest.mark.parametrize("before,after", [
    ('id="0"', 'id="99"'),
    ('mnemonic="abandon-abandon"', 'mnemonic="abandon-ability"'),
    ('id="0" mnemonic="abandon-abandon"', 'id="1" mnemonic="abandon-ability"'),
    ('id="0" mnemonic="abandon-abandon"', 'id="99" mnemonic="abandon-arrive"'),
    (' mnemonic="abandon-abandon"', ''),
    (' id="0"', ''),
    ('>100,00</ent>', '>different source</ent>'),
])
def test_forged_partial_or_reassigned_identities_are_rejected(extracted, before, after):
    report_id, files = extracted
    path = f"/reports/{report_id}/documents/{files[0]}/markdown"
    assert request("POST", path, {"content": canonical().replace(before, after), "base_revision": 1})[0] == 422
    assert request("GET", path)[1] == {"content": canonical(), "revision": 1}
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 2
        assert session.get(EntityFile, (report_id, 0)).file_id == files[0]


def test_duplicate_identities_are_rejected(extracted):
    report_id, files = extracted
    path = f"/reports/{report_id}/documents/{files[0]}/markdown"
    assert request("POST", path, {"content": canonical() * 2, "base_revision": 1})[0] == 422
    assert request("GET", path)[1]["revision"] == 1


def test_deletions_retire_ids_and_cannot_restore_them(extracted):
    report_id, files = extracted
    path = f"/reports/{report_id}/documents/{files[0]}/markdown"
    assert request("POST", path, {"content": "Source without entities", "base_revision": 1})[0] == 200
    with database.session_scope() as session:
        assert session.get(EntityFile, (report_id, 0)) is None
        assert session.get(EntityFile, (report_id, 1)).file_id == files[1]
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 2
    draft = '<ent t="amount">100,00</ent>'
    status, result = request("POST", path, {"content": draft, "base_revision": 2})
    assert status == 200
    assert read_identities(result["content"])[0].num == 2
    assert request("POST", path, {"content": canonical(), "base_revision": 3})[0] == 422
    assert request("GET", path)[1] == result


def test_stale_save_cannot_allocate_or_change_index(extracted):
    report_id, files = extracted
    path = f"/reports/{report_id}/documents/{files[0]}/markdown"
    status, result = request("POST", path, {"content": canonical(), "base_revision": 1})
    assert status == 200
    assert request("POST", path, {"content": '<ent t="number">1</ent>', "base_revision": 1})[0] == 409
    assert request("GET", path)[1] == result
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 2
        assert len(reports.report_documents(session, report_id)) == 2
        assert len(list(session.scalars(select(EntityFile)))) == 2


def test_concurrent_saves_of_one_revision_have_one_winner(extracted):
    report_id, files = extracted
    path = f"/reports/{report_id}/documents/{files[0]}/markdown"
    ready = Barrier(2, timeout=10)

    def save(value):
        ready.wait()
        return request("POST", path, {"content": canonical() + f'<ent t="number">{value}</ent>', "base_revision": 1})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [10, 20]))
    assert sorted(status for status, _ in results) == [200, 409]
    saved = next(result for status, result in results if status == 200)
    assert request("GET", path)[1] == saved
    assert [identity.num for identity in read_identities(saved["content"])] == [0, 2]
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 3


def test_failed_save_rolls_back_reservations_and_derived_index(extracted, monkeypatch):
    report_id, files = extracted
    original_write = Path.write_text

    def fail_write(path, *args, **kwargs):
        if "edited" in path.parts:
            raise OSError("Storage unavailable")
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_write)
    with pytest.raises(OSError, match="Storage unavailable"):
        documents.edit_markdown(report_id, files[0], MarkdownEdit(
            content=canonical() + '<ent t="number">1</ent>', base_revision=1))
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 2
        assert session.get(EntityFile, (report_id, 0)).file_id == files[0]
        assert session.get(EntityFile, (report_id, 2)) is None
        original = reports.report_documents(session, report_id)[0]
        assert original.version == 1
        assert storage.artifact_path(original.entities_path).read_text(encoding="utf-8") == canonical()


def test_manual_drafts_in_ocr_revision_receive_identities(ready):
    report_id, revisions, _ = ready
    with database.session_scope() as session:
        file_id = session.get(File, revisions[0]).file_id
    path = f"/reports/{report_id}/documents/{file_id}/markdown"
    content = 'Before <ent t="number">1</ent> after'
    status, result = request("POST", path, {"content": content, "base_revision": 1})
    assert status == 200
    assert result == {"content": assign_identities(content, range(1)), "revision": 2}
    assert request("GET", path)[1] == result
    with database.session_scope() as session:
        assert session.get(EntityFile, (report_id, 0)).file_id == file_id
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 1


def test_deleting_document_removes_manual_identity_index_and_retires_ids(ready):
    report_id, revisions, _ = ready
    with database.session_scope() as session:
        file_id = session.get(File, revisions[0]).file_id
    path = f"/reports/{report_id}/documents/{file_id}"
    assert request("POST", path + "/markdown", {
        "content": '<ent t="number">1</ent>', "base_revision": 1,
    })[0] == 200
    assert request("DELETE", path)[0] == 204
    with database.session_scope() as session:
        assert session.get(EntityFile, (report_id, 0)) is None
        assert session.get(ReportEntityCounter, report_id).next_entity_id == 1
