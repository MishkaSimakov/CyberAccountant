"""Requirement generation runs in memory and validates stored identities."""

import json
import logging
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from processing.common.corpus import ReviewedCorpus
from processing.common.documents import DocumentRevision, ReviewedDocument
from processing.common.errors import ProcessingValidationError
from processing.common.llm import ModelClient
from processing.common.requirements import AssertionDefinition, FormulaDefinition
from processing.common.scope import Scope
from processing.requirements.interface import RequirementInput
from processing.requirements.options import RequirementOptions
from processing.requirements.processor import RequirementsProcessor
from processing.requirements.validation import validate_definition

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def source():
    return RequirementInput(ReviewedCorpus(1, (
        ReviewedDocument(DocumentRevision(1, 10, 2), "card.md", "final_report",
                         '<ent id="41" mnemonic="abandon-ability" t="amount" u="RUB_K" n="1000" value="100">0.1</ent>'),
        ReviewedDocument(DocumentRevision(1, 20, 3), "evidence.md", "supporting",
                         '<ent id="8" mnemonic="abandon-able" t="amount" value="100">100</ent>'),
    )), Scope("Contract", date(2026, 1, 1), date(2026, 1, 31), "Итого", (41,)))


@pytest.mark.parametrize("style", ["numeric", "mnemonic"])
def test_fake_model_preserves_explanations_options_and_canonical_markdown(source, style, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    left, right = ("#41", "#8") if style == "numeric" else ("abandon-ability", "abandon-able")

    def generate(prompt, options, **kwargs):
        calls.append((prompt, options))
        return json.dumps([
            {"equation": f"{left} == {right}", "tag": "agg", "text": "Сумма по документам"},
            {"evidence": "Недостаточно доказательств", "ids_related": [left], "verdict": "unverifiable"},
        ])

    options = RequirementOptions("test-model", style, 0, 17, "enabled")
    client = ModelClient(SimpleNamespace(generate=generate))
    result = RequirementsProcessor(client, options).process(source, logger=LOGGER)
    assert result.definitions == (
        FormulaDefinition(equation=f"{left} == {right}", tag="agg", source="llm", text="Сумма по документам"),
        AssertionDefinition(condition=None, ids_related=(41,), source="llm", text="Недостаточно доказательств"),
    )
    prompt, request = calls[0]
    assert request.model == "test-model" and request.timeout == 17
    assert request.reasoning == "enabled" and request.max_retries == 0
    assert '<ent id="41" t="amount" u="RUB_K" n="1000" value="100">0.1</ent>' in prompt
    assert 'mnemonic=' not in prompt
    assert '"period_start": "2026-01-01"' in prompt
    assert 'mnemonic="abandon-ability"' in source.corpus.documents[0].markdown
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("definition", [
    FormulaDefinition(equation="#999 == #41"), FormulaDefinition(equation="unknown-entity == #41"),
    AssertionDefinition(condition=False, ids_related=(999,)),
    AssertionDefinition(condition=None, ids_related=("unknown-entity",)),
])
def test_unknown_references_are_rejected(source, definition):
    with pytest.raises(ProcessingValidationError, match="Неизвестные сущности"):
        validate_definition(definition, source.registry)


def test_references_resolve_supplied_mnemonics_instead_of_regenerating_them(source):
    definition = validate_definition(AssertionDefinition(condition=False,
                                                         ids_related=("abandon-ability", "8", "#41")), source.registry)
    assert definition.ids_related == (41, 8, 41)


def test_custom_generators_share_input_and_are_validated_before_output(source):
    inputs = []

    def first(value, **kwargs):
        inputs.append(value)
        return [FormulaDefinition(equation="#41 == #8", source="custom")]

    def second(value, **kwargs):
        inputs.append(value)
        return [AssertionDefinition(condition=True, ids_related=("abandon-able",), source="custom")]

    result = RequirementsProcessor(
        generators=[SimpleNamespace(generate=first), SimpleNamespace(generate=second)]).process(
        source, logger=LOGGER)
    assert inputs[0] is inputs[1] is source
    assert result.definitions[1].ids_related == (8,)
    bad_scope = replace(source.scope, target_row=(8,))
    with pytest.raises(ProcessingValidationError, match="Target row"):
        RequirementsProcessor(generators=[]).process(replace(source, scope=bad_scope), logger=LOGGER)
