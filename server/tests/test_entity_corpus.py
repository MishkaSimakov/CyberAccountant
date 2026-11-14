"""Canonical parsing reads identities and source locations without allocating."""

from datetime import date
from decimal import Decimal

import pytest

from processing.common import DocumentRevision, ReviewedCorpus, ReviewedDocument, UnknownValue
from processing.common.errors import ProcessingValidationError
from processing.entities.corpus import build_corpus, build_registry, parse_drafts
from processing.entities.identifiers import assign_identities
from processing.entities.projection import project_markdown


def tag(num, body="100", attrs='t="amount"'):
    return assign_identities(f'<ent {attrs}>{body}</ent>', range(num, num + 1))


def test_file_order_new_tags_and_rebuilding_never_change_existing_identities(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    documents = {"second": ("data", tag(3)), "first": ("card", tag(40))}
    original = {entity.file: (entity.num, entity.mnemonic) for entity in build_registry(documents).entities}
    reordered = dict(reversed(list(documents.items())))
    assert {entity.file: (entity.num, entity.mnemonic)
            for entity in build_registry(reordered).entities} == original
    reordered["first"] = "card", tag(80, "20") + "\n" + tag(40)
    result = build_registry(reordered)
    assert {entity.num: entity.mnemonic for entity in result.entities if entity.num != 80} == {
        num: mnemonic for num, mnemonic in original.values()}
    assert build_registry(reordered).to_dict() == result.to_dict()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("first,second", [
    (tag(0), tag(0)),
    (tag(0), tag(1).replace("abandon-ability", "abandon-abandon")),
    (tag(0), '<ent t="number">2</ent>'),
    (tag(0), '<ent id="2" t="number">2</ent>'),
    (tag(0), '<ent mnemonic="abandon-able" t="number">2</ent>'),
    (tag(0), tag(2).replace('t="amount"', 't="invalid"')),
])
def test_invalid_or_duplicate_canonical_entities_across_documents_fail(first, second):
    with pytest.raises(ProcessingValidationError):
        build_registry({"1": ("card", first), "2": ("data", second)})


def test_typed_corpus_retains_actual_revision_values_units_roles_and_spans():
    markdown = ('# ' + tag(17, "245,00", 't="amount" u="RUB_K" n="1000" value="250000.00"')
                + '\n' + tag(5, "Январь 2026", 't="date" value="2026-01-01"')
                + '\n' + tag(22, "100", 't="number" value=""'))
    revision = DocumentRevision(report_id=8, document_id=4, version=3)
    document = ReviewedDocument(revision, "reviewed.md", "final_report", markdown)
    entities = build_corpus(ReviewedCorpus(8, (document,)))
    assert [entity.identity.num for entity in entities] == [17, 5, 22]
    assert all(entity.revision == revision for entity in entities)
    amount, period, unresolved = entities
    assert (amount.normalized_value, amount.norm, amount.unit, amount.loc) == (
        Decimal("250000.00"), Decimal("1000"), "RUB_K", "filename")
    assert period.normalized_value == date(2026, 1, 1)
    assert unresolved.normalized_value is UnknownValue.UNKNOWN
    assert markdown[slice(*amount.span)].endswith('>245,00</ent>')
    assert amount.raw == amount.source_text == "245,00"
    registry = build_registry({"4": ("card", markdown)}, revisions={"4": revision})
    assert all(entity.role == "card" and entity.revision == revision for entity in registry.entities)


def test_draft_parsing_never_assigns_an_identity():
    drafts = parse_drafts('<ent t="amount" n="1000">2,50</ent> <ent t="date" value="">unknown</ent>')
    assert drafts[0].normalized_value == Decimal("2500.00")
    assert drafts[1].normalized_value is UnknownValue.UNKNOWN
    assert all(not hasattr(draft, "identity") for draft in drafts)


def test_prompt_projection_filters_only_attributes_and_preserves_canonical_source():
    original = '# Before\n' + tag(17, '**1** &amp; 2\n', 't="number" u="item &quot;A&quot;" n="1" value="2"')
    snapshot = original
    projection = project_markdown(original, ("id", "t", "value"))
    assert projection == '# Before\n<ent id="17" t="number" value="2">**1** &amp; 2\n</ent>'
    assert original == snapshot
    assert build_registry({"1": ("card", original)}).entities[0].num == 17
    with pytest.raises(ValueError, match="id references"):
        project_markdown(original, ("t",))
