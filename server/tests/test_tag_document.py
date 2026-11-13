"""The single-document API preserves batch behavior and can resume a pipeline."""

import pytest

from checkier import Pipeline, PipelineConfig, tag_document
from checkier.stage1_tag import TAG_PROMPT, _tag_one


def test_prompt_wraps_quoted_dates_as_single_entities():
    assert '"10" мая 2026' in TAG_PROMPT
    assert '«10» мая 2026' in TAG_PROMPT
    assert "ONE date, never a separate" in TAG_PROMPT
    assert "2 квартал 2026" in TAG_PROMPT



def test_tag_document_needs_no_directories(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path / "missing", out_dir=tmp_path / "out")
    calls = []

    def respond(prompt, **kwargs):
        calls.append(prompt)
        return [
            {"content": "Итого: ", "wrap": "100,00", "type": "amount", "unit": "RUB"},
            {"content": " руб.\n", "wrap": "", "type": "EOF"},
        ]

    monkeypatch.setattr("checkier.stage1_tag.call_llm_json", respond)
    tagged, retries = tag_document(cfg, "акт.pdf", "Итого: 100,00 руб.\n")
    assert tagged == 'Итого: <ent t="amount" u="RUB">100,00</ent> руб.\n'
    assert retries == 0
    assert "Filename: акт.pdf" in calls[0]
    assert not cfg.out_dir.exists()

    cfg.files_dir.mkdir(parents=True)
    (cfg.files_dir / "file1.md").write_text("Итого: 100,00 руб.\n", encoding="utf-8")
    assert _tag_one(cfg, "file1", "акт.pdf") == (tagged, 0)


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
    assert pipeline.scope()["target_row"] == [registry.entities[0].key]


def test_empty_extraction_does_not_rerun_tagging(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path / "missing", out_dir=tmp_path / "out")

    def unexpected(*args, **kwargs):
        pytest.fail("An empty extraction result is still a completed result")

    monkeypatch.setattr("checkier.pipeline.run_ingest", unexpected)
    monkeypatch.setattr("checkier.pipeline.run_tag", unexpected)
    pipeline = Pipeline(cfg, mapping={}, tagged={})
    assert pipeline.tag() == {}
    assert pipeline.registry().entities == []
