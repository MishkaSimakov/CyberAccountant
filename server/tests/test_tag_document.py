"""Two-request document extraction and resumable pipeline behavior."""

import copy

import jsonschema
import pytest

from checkier import Pipeline, PipelineConfig, tag_document
from checkier.schemas import entity_metadata_schema
from checkier.stage1_tag import TAG_PROMPT, _tag_one, _validate_metadata


def metadata(**changes):
    value = {
        "e0": {
            "type": "amount", "unit": "RUB", "norm": "1",
            "value": "100.00", "start": None, "end": None,
        },
    }
    for key, item in changes.items():
        value[key] = item
    return value


def test_prompt_wraps_quoted_dates_as_single_entities():
    assert '"10" мая 2026' in TAG_PROMPT
    assert '«10» мая 2026' in TAG_PROMPT
    assert "ONE date, never a separate" in TAG_PROMPT
    assert "2 квартал 2026" in TAG_PROMPT


def test_tag_document_uses_wrapping_then_exact_id_metadata(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path / "missing", out_dir=tmp_path / "out")
    calls = []

    def respond(prompt, **kwargs):
        calls.append((prompt, kwargs))
        if kwargs["schema_name"] == "entity_wrapping":
            assert set(kwargs["schema"]["items"]["properties"]) == {"content", "wrap", "type"}
            return [
                {"content": "# акт.pdf\nИтого: ", "wrap": "100,00", "type": "entity"},
                {"content": " руб.\n", "wrap": "", "type": "EOF"},
            ]
        assert kwargs["schema_name"] == "entity_metadata"
        assert kwargs["schema"]["required"] == ["e0"]
        assert '<ent id="e0">100,00</ent>' in prompt
        return metadata()

    monkeypatch.setattr("checkier.stage1_tag.call_llm_json", respond)
    tagged, retries = tag_document(cfg, "акт.pdf", "Итого: 100,00 руб.\n")
    assert tagged == (
        '# акт.pdf\nИтого: <ent t="amount" u="RUB" value="100.00">100,00</ent> руб.\n'
    )
    assert retries == 0
    assert len(calls) == 2
    assert "# акт.pdf\nИтого: 100,00 руб." in calls[0][0]
    assert not cfg.out_dir.exists()

    cfg.files_dir.mkdir(parents=True)
    (cfg.files_dir / "file1.md").write_text("Итого: 100,00 руб.\n", encoding="utf-8")
    assert _tag_one(cfg, "file1", "акт.pdf") == (tagged, 0)


@pytest.mark.parametrize("ending", ["", "\n", "\n\n\n"])
def test_tag_document_canonicalizes_trailing_newlines(tmp_path, monkeypatch, ending):
    cfg = PipelineConfig(data_dir=tmp_path / "missing", out_dir=tmp_path / "out")

    def respond(prompt, **kwargs):
        if kwargs["schema_name"] == "entity_wrapping":
            return [
                {"content": "# акт.pdf\nИтого: ", "wrap": "100,00", "type": "entity"},
                {"content": " руб." + ending, "wrap": "", "type": "EOF"},
            ]
        return metadata()

    monkeypatch.setattr("checkier.stage1_tag.call_llm_json", respond)
    tagged, retries = tag_document(cfg, "акт.pdf", "Итого: 100,00 руб.\n")
    assert tagged == (
        '# акт.pdf\nИтого: <ent t="amount" u="RUB" value="100.00">100,00</ent> руб.\n'
    )
    assert retries == 0


def test_quoted_date_is_one_entity_with_inclusive_bounds(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path)
    schemas = []

    def respond(prompt, **kwargs):
        schemas.append(kwargs["schema"])
        if kwargs["schema_name"] == "entity_wrapping":
            return [
                {"content": '# date.md\nНачало: ', "wrap": '"10" мая 2026 г.', "type": "entity"},
                {"content": "\n", "wrap": "", "type": "EOF"},
            ]
        return {"e0": {
            "type": "date", "unit": "", "norm": "1", "value": None,
            "start": "2026-05-10T00:00:00", "end": "2026-05-10T23:59:59",
        }}

    monkeypatch.setattr("checkier.stage1_tag.call_llm_json", respond)
    tagged, _ = tag_document(cfg, "date.md", 'Начало: "10" мая 2026 г.\n')
    assert tagged.count("<ent ") == 1
    assert (
        '<ent t="date" start="2026-05-10T00:00:00" end="2026-05-10T23:59:59">'
        '"10" мая 2026 г.</ent>'
    ) in tagged
    assert schemas[1] == entity_metadata_schema(["e0"])


def test_dynamic_metadata_schema_is_closed_and_id_keyed():
    schema = entity_metadata_schema(["e0", "e1"])
    valid = {
        "e0": metadata()["e0"],
        "e1": {
            "type": "date", "unit": "", "norm": "1", "value": None,
            "start": "2026-05-01T00:00:00", "end": "2026-05-31T23:59:59",
        },
    }
    jsonschema.validate(valid, schema)
    for invalid in (
        {"e0": valid["e0"]},
        dict(valid, e2=valid["e0"]),
        {"e0": dict(valid["e0"], extra="bad"), "e1": valid["e1"]},
        {"e0": dict(valid["e0"], value=42), "e1": valid["e1"]},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)


@pytest.mark.parametrize("invalid", [
    {},
    {"e0": metadata()["e0"], "e1": metadata()["e0"]},
    {"e0": dict(metadata()["e0"], extra="bad")},
    {"e0": dict(metadata()["e0"], type="unknown")},
    {"e0": dict(metadata()["e0"], value=42)},
    {"e0": {
        "type": "date", "unit": "", "norm": "1", "value": None,
        "start": "2026-05-01T00:00:00", "end": None,
    }},
    {"e0": {
        "type": "date", "unit": "", "norm": "1", "value": None,
        "start": "2026-06-01T00:00:00", "end": "2026-05-31T23:59:59",
    }},
])
def test_semantic_metadata_validation_rejects_bad_ids_and_shapes(invalid):
    with pytest.raises(ValueError):
        _validate_metadata(copy.deepcopy(invalid), ["e0"])


def test_tag_document_raises_on_reconstruction_failure(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path, max_retries=0)
    monkeypatch.setattr("checkier.stage1_tag.call_llm_json", lambda *args, **kwargs: [
        {"content": "different document", "wrap": "", "type": "EOF"},
    ])
    with pytest.raises(RuntimeError, match="tagging failed for акт.pdf"):
        tag_document(cfg, "акт.pdf", "original document")


def test_pipeline_resumes_from_extracted_documents(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path / "missing", out_dir=tmp_path / "out")
    mapping = {
        "card1": {"original": "card.pdf", "role": "card", "duplicate_of": None},
        "request1": {"original": "request.md", "role": "request", "duplicate_of": None},
    }
    tagged = {
        "card1": ("card", '| 3 этап, 2026 г. | <ent t="amount" u="RUB">100,00</ent> |\n'),
        "request1": ("request", "Проверить строку «3 этап, 2026 г.»"),
    }

    def unexpected(*args, **kwargs):
        pytest.fail("Completed extraction must not invoke ingest or tagging")

    monkeypatch.setattr("checkier.pipeline.run_ingest", unexpected)
    monkeypatch.setattr("checkier.pipeline.run_tag", unexpected)
    pipeline = Pipeline(cfg, mapping=mapping, tagged=tagged)
    assert pipeline.tag() == tagged
    registry = pipeline.registry()
    assert len(registry.entities) == 1
    assert registry.entities[0].value == "100.00"
    assert '<ent id="' in pipeline.replace()["card1"]
    assert pipeline.scope()["target_row"] is None


def test_empty_extraction_does_not_rerun_tagging(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path / "missing", out_dir=tmp_path / "out")

    def unexpected(*args, **kwargs):
        pytest.fail("An empty extraction result is still a completed result")

    monkeypatch.setattr("checkier.pipeline.run_ingest", unexpected)
    monkeypatch.setattr("checkier.pipeline.run_tag", unexpected)
    pipeline = Pipeline(cfg, mapping={}, tagged={})
    assert pipeline.tag() == {}
    assert pipeline.registry().entities == []
