"""Replaceable entity processors run entirely on in-memory Markdown."""

import json
import logging
from dataclasses import fields

import pytest

from processing.common.errors import ProcessingValidationError
from processing.common.llm import ModelClient
from processing.entities.corpus import parse_drafts
from processing.entities.interface import EntityInput, EntityOutput, EntityProcessor
from processing.entities.markup import parse_entity_tags
from processing.entities.normalization import normalize_document
from processing.entities.options import EntityOptions
from processing.entities.processor import LLMEntityProcessor
from processing.entities.tagging import tag_document

LOGGER = logging.getLogger(__name__)


def test_real_processor_composes_tagging_and_normalization_with_fake_adapter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    class Adapter:
        def generate(self, prompt, options, *, on_token=None):
            calls.append((prompt, options))
            if options.schema_name == "tagged_document":
                return json.dumps([
                    {"content": "Период: ", "wrap": "Январь 2026", "type": "date"},
                    {"content": "\nСумма: ", "wrap": "2,50", "type": "amount", "unit": 'RUB "A"', "norm": "1000"},
                    {"content": "\n", "wrap": "", "type": "EOF"},
                ])
            return json.dumps([{"raw": "Январь 2026", "normalized": "01.01.2026"}])

    processor = LLMEntityProcessor(ModelClient(Adapter()), EntityOptions(tag_model="test-model"))
    source = EntityInput("in-memory.md", "Период: Январь 2026\nСумма: 2,50\n")
    result = processor.process(source, logger=LOGGER)
    assert isinstance(processor, EntityProcessor)
    assert isinstance(result, EntityOutput)
    assert [options.schema_name for _, options in calls] == ["tagged_document", "date_normalizations"]
    assert all(options.model == "test-model" for _, options in calls)
    assert result.retries == 0
    assert result.drafts == parse_drafts(result.markdown)
    assert 'value="2026-01-01"' in result.markdown
    assert 'value="2500.00"' in result.markdown
    assert 'u="RUB &quot;A&quot;"' in result.markdown
    tags = parse_entity_tags(result.markdown)
    assert all("id" not in tag.attributes and "mnemonic" not in tag.attributes for tag in tags)
    unwrapped, end = [], 0
    for tag in tags:
        unwrapped.extend((result.markdown[end:tag.span[0]], result.markdown[slice(*tag.body_span)]))
        end = tag.span[1]
    unwrapped.append(result.markdown[end:])
    assert "".join(unwrapped) == source.markdown
    assert list(tmp_path.iterdir()) == []


def test_reviewed_values_and_explicit_unknowns_are_preserved_without_model_calls(llm_client):
    canonical = ('Before <ent id="17" mnemonic="zoo-zone" t="amount" n="1000" value="250">1</ent> '
                 '<ent t="date" value="">Январь 2026</ent>\n')
    assert normalize_document(EntityOptions(), canonical, client=llm_client, logger=LOGGER) == canonical


def test_tagging_retries_source_reconstruction_with_feedback(llm_client, monkeypatch):
    prompts = []

    def respond(prompt, **kwargs):
        prompts.append(prompt)
        if len(prompts) == 1:
            return [{"content": "Wrong\n", "wrap": "", "type": "EOF"}]
        return [{"content": "", "wrap": "1", "type": "number"},
                {"content": "\n", "wrap": "", "type": "EOF"}]

    monkeypatch.setattr(llm_client, "call_llm_json", respond)
    tagged, retries = tag_document(EntityOptions(max_retries=1), "test.md", "1\n", client=llm_client, logger=LOGGER)
    assert retries == 1
    assert "Validation error from previous attempt" in prompts[1]
    assert tagged == '<ent t="number">1</ent>\n'


def test_tagging_rejects_a_changed_trailing_newline(llm_client, monkeypatch):
    monkeypatch.setattr(llm_client, "call_llm_json", lambda *args, **kwargs: [
        {"content": "1", "wrap": "", "type": "EOF"}])
    with pytest.raises(RuntimeError, match="diverges"):
        tag_document(EntityOptions(max_retries=0), "test.md", "1\n", client=llm_client, logger=LOGGER)


@pytest.mark.parametrize("settings", [{"tag_model": ""}, {"max_retries": -1}, {"request_timeout": 0}])
def test_invalid_entity_options_fail(settings):
    with pytest.raises(ProcessingValidationError):
        EntityOptions(**settings)
