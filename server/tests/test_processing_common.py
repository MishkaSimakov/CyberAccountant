"""Shared contracts preserve reviewed input without application dependencies."""

import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from processing.common import (
    AssertionDefinition,
    DocumentRevision,
    Entity,
    EntityDraft,
    EntityIdentity,
    FormulaDefinition,
    ProcessingError,
    ProcessingValidationError,
    ReviewedCorpus,
    ReviewedDocument,
    Scope,
    UnknownValue,
)


def document(report_id=1, document_id=2, version=1):
    return ReviewedDocument(
        revision=DocumentRevision(report_id=report_id, document_id=document_id, version=version),
        name="Расчёт.pdf",
        role="supporting",
        markdown='<ent id="0" mnemonic="abandon ability" t="amount" value="">неизвестно</ent>',
    )


def test_corpus_preserves_canonical_markdown_and_exact_revision():
    reviewed = document(version=3)
    corpus = ReviewedCorpus(report_id=1, documents=(reviewed,))
    assert corpus.documents[0].markdown == reviewed.markdown
    assert corpus.revisions == (DocumentRevision(1, 2, 3),)
    with pytest.raises(FrozenInstanceError):
        reviewed.markdown = "changed"

    edited = replace(reviewed, revision=DocumentRevision(1, 2, 4), markdown="reviewed edit")
    newer = replace(corpus, documents=(edited,))
    assert newer.revisions != corpus.revisions
    assert corpus.documents[0] == reviewed


@pytest.mark.parametrize("documents", [
    (document(report_id=2),),
    (document(), document()),
    (document(), document(version=2)),
])
def test_corpus_rejects_mixed_reports_and_duplicate_document_revisions(documents):
    with pytest.raises(ProcessingValidationError):
        ReviewedCorpus(report_id=1, documents=documents)


def test_entity_drafts_have_no_identity_and_unknown_is_explicit():
    draft = EntityDraft(type="amount", raw="неизвестно")
    assert draft.normalized_value is UnknownValue.UNKNOWN
    assert not hasattr(draft, "identity")
    for known in (Decimal("0"), "", date(2026, 1, 1)):
        assert replace(draft, normalized_value=known).normalized_value == known
        assert known != UnknownValue.UNKNOWN


def test_entity_views_retain_report_local_identity_and_reviewed_values():
    identity = EntityIdentity(num=0, mnemonic="abandon ability")
    entity = Entity(
        identity=identity,
        revision=DocumentRevision(1, 2, 3),
        type="amount",
        raw="245,00",
        normalized_value=Decimal("250000.00"),
        norm=Decimal("1000"),
        unit="RUB_K",
        span=(10, 90),
        source_text="245,00",
    )
    other_report = replace(entity, revision=DocumentRevision(2, 3, 1))
    assert other_report.identity == entity.identity
    assert other_report.revision.report_id != entity.revision.report_id
    assert entity.normalized_value == Decimal("250000.00")
    assert entity.raw == entity.source_text == "245,00"


@pytest.mark.parametrize("num,mnemonic", [(-1, "abandon ability"), (0, " ")])
def test_invalid_entity_identities_raise_shared_validation_error(num, mnemonic):
    with pytest.raises(ProcessingError):
        EntityIdentity(num=num, mnemonic=mnemonic)


@pytest.mark.parametrize("period_start,period_end", [
    (date(2026, 1, 1), None),
    (None, date(2026, 1, 31)),
    (date(2026, 2, 1), date(2026, 1, 31)),
])
def test_scope_rejects_incomplete_or_reversed_periods(period_start, period_end):
    with pytest.raises(ProcessingValidationError):
        Scope(period_start=period_start, period_end=period_end)


def test_scope_preserves_period_and_selected_report_local_ids():
    scope = Scope(
        contract_number="Contract 1",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        target_label="2026",
        target_row=(0, 4),
    )
    assert scope.target_row == (0, 4)
    assert Scope().target_row is None
    assert Scope(target_row=()).target_row == ()


def test_definitions_preserve_source_explanation_and_assertion_unknowns():
    formula = FormulaDefinition(equation="#0 == #4", source="insurance", text="Monthly total", tag="agg")
    assert formula.kind == "formula"
    assert formula.source == "insurance"
    assert formula.text == "Monthly total"
    for condition in (True, False, None):
        assertion = AssertionDefinition(condition=condition, ids_related=(0, "abandon ability"),
                                        source="scope", text="Coverage")
        assert assertion.kind == "assertion"
        assert assertion.condition is condition
        assert assertion.ids_related == (0, "abandon ability")


def test_common_imports_with_only_the_standard_library():
    server = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import processing.common; "
         "assert not any(name.split('.')[0] in "
         "{'app', 'checkier', 'sqlalchemy', 'fastapi', 'infrastructure'} for name in sys.modules)", str(server)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
