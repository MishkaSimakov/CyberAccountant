"""Two-request document extraction and resumable pipeline behavior."""

import copy

import jsonschema
import pytest

from checkier import Pipeline, PipelineConfig, tag_document
from checkier.schemas import entity_metadata_schema
from checkier.stage1_tag import (
    TAG_PROMPT,
    _bounded_edit_distance,
    _repair_segments,
    _tag_one,
    _validate_metadata,
    _validate_segments,
)


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


@pytest.mark.parametrize(("left", "right", "distance"), [
    ("", "", 0),
    ("", "abc", 3),
    ("abc", "", 3),
    ("abc", "abc", 0),
    ("kitten", "sitting", 3),
    ("abc", "xbc", 1),
    ("abc", "abx", 1),
    ("abc", "bcx", 2),
    ("г.", "т.", 1),
    (" |  | ", " | ", 3),
])
def test_bounded_edit_distance(left, right, distance):
    for limit in range(5):
        assert _bounded_edit_distance(left, right, limit) == min(distance, limit + 1)


@pytest.mark.parametrize("copied", ["Итого ", "Иттого: ", "Итого; "])
def test_repair_restores_source_gaps_without_changing_wraps(copied):
    segments = [
        {"content": copied, "wrap": "100,00", "type": "entity"},
        {"content": " руб!\n\n", "wrap": "", "type": "EOF"},
    ]
    original = copy.deepcopy(segments)
    source = "Итого: 100,00 руб.\n"
    repaired = _repair_segments(source, segments)
    assert repaired is not None
    assert "".join(seg["content"] + seg["wrap"] for seg in repaired) == source
    assert [seg["wrap"] for seg in repaired] == [seg["wrap"] for seg in segments]
    assert segments == original


def test_repair_removes_two_surplus_table_separators():
    segments = [
        {"content": "| ", "wrap": "1", "type": "entity"},
        {"content": " | label |  |  | ", "wrap": "2", "type": "entity"},
        {"content": " | other |  |  | ", "wrap": "3", "type": "entity"},
        {"content": " |\n", "wrap": "", "type": "EOF"},
    ]
    source = "| 1 | label |  | 2 | other |  | 3 |\n"
    repaired = _repair_segments(source, segments)
    assert repaired is not None
    assert _validate_segments(source, repaired) == []


@pytest.mark.parametrize(("source", "segments"), [
    ("Май 2026 т.", [
        {"content": "", "wrap": "Май 2026 г.", "type": "entity"},
        {"content": "", "wrap": "", "type": "EOF"},
    ]),
    ("100", [
        {"content": "xxxx", "wrap": "100", "type": "entity"},
        {"content": "", "wrap": "", "type": "EOF"},
    ]),
    ("100", [
        {"content": "", "wrap": "100", "type": "entity"},
        {"content": "xxxx", "wrap": "", "type": "EOF"},
    ]),
    ("100 200", [
        {"content": "xxx", "wrap": "100", "type": "entity"},
        {"content": " xxx", "wrap": "200", "type": "entity"},
        {"content": "x", "wrap": "", "type": "EOF"},
    ]),
    ("1a1", [
        {"content": "", "wrap": "1", "type": "entity"},
        {"content": "a1x", "wrap": "", "type": "EOF"},
    ]),
    ("1a1b2", [
        {"content": "", "wrap": "1", "type": "entity"},
        {"content": "b", "wrap": "2", "type": "entity"},
        {"content": "", "wrap": "", "type": "EOF"},
    ]),
    ("200 100", [
        {"content": "", "wrap": "100", "type": "entity"},
        {"content": " ", "wrap": "200", "type": "entity"},
        {"content": "", "wrap": "", "type": "EOF"},
    ]),
])
def test_repair_rejects_changed_wraps_excess_edits_and_ambiguous_placements(source, segments):
    assert _repair_segments(source, segments) is None


@pytest.mark.parametrize("distance", [1, 5, 20, 200])
def test_repair_uses_later_identical_wrap_to_disambiguate_earlier_occurrence(distance):
    padding = "long " * distance
    segments = [
        {"content": "", "wrap": "1", "type": "entity"},
        {"content": "xxa1" + padding, "wrap": "1", "type": "entity"},
        {"content": "", "wrap": "", "type": "EOF"},
    ]
    repaired = _repair_segments("1a1" + padding + "1", segments)
    assert repaired is not None
    # The first wrap has two locally feasible matches. The second wrap rules
    # out the later one, whose following gap would need four deletions.
    assert [seg["content"] for seg in repaired] == ["", "a1" + padding, ""]


@pytest.mark.parametrize("distance", [0, 1, 3, 20, 200])
@pytest.mark.parametrize("entity_count", [2, 5, 20])
def test_repair_matches_identical_amounts_across_cells_and_distant_rows(distance, entity_count):
    gaps = [
        f"| row {i}: " + "context " * distance + "0,00 (reference) | amount: "
        for i in range(entity_count)
    ]
    segments = [
        {"content": gap + (" | " if i < 2 else ""), "wrap": "0,00", "type": "entity"}
        for i, gap in enumerate(gaps)
    ]
    segments.append({"content": " | 0,00 (reference)\n", "wrap": "", "type": "EOF"})
    source = "".join(gap + "0,00" for gap in gaps) + segments[-1]["content"]

    repaired = _repair_segments(source, segments)

    assert repaired is not None
    assert [seg["content"] for seg in repaired[:-1]] == gaps
    assert [seg["wrap"] for seg in repaired[:-1]] == ["0,00"] * entity_count
    assert _validate_segments(source, repaired) == []


@pytest.mark.parametrize("separator", ["", " ", " | ", "context " * 200])
def test_repair_matches_consecutive_identical_amounts_without_skipping_occurrences(separator):
    segments = [
        {"content": "", "wrap": "0,00", "type": "entity"},
        {"content": separator + "|", "wrap": "0,00", "type": "entity"},
        {"content": separator + "|", "wrap": "0,00", "type": "entity"},
        {"content": "", "wrap": "", "type": "EOF"},
    ]
    source = separator.join(["0,00"] * 3)
    repaired = _repair_segments(source, segments)
    assert repaired is not None
    assert [seg["content"] for seg in repaired] == ["", separator, separator, ""]
    assert _validate_segments(source, repaired) == []


def test_repair_rejects_ambiguous_placements_of_multiple_identical_wraps():
    segments = [
        {"content": "", "wrap": "1", "type": "entity"},
        {"content": "b", "wrap": "1", "type": "entity"},
        {"content": "", "wrap": "", "type": "EOF"},
    ]
    # More than one pair of occurrences fits; picking the cheapest is unsafe.
    assert _repair_segments("1a1b1", segments) is None


@pytest.mark.parametrize("segments", [
    [],
    [None],
    [{"content": "", "wrap": "", "type": "entity"}],
    [{"content": 1, "wrap": "", "type": "EOF"}],
    [{"wrap": "", "type": "EOF"}],
    [
        {"content": "", "wrap": "", "type": "entity"},
        {"content": "", "wrap": "", "type": "EOF"},
    ],
])
def test_repair_rejects_invalid_segment_structure(segments):
    assert _repair_segments("", segments) is None


def test_tag_document_repairs_gaps_without_repeating_wrapping(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path, max_retries=0)
    calls = []

    def respond(prompt, **kwargs):
        calls.append(kwargs["schema_name"])
        if kwargs["schema_name"] == "entity_wrapping":
            return [
                {"content": "# акт.pdf\nИтого: | ", "wrap": "100,00", "type": "entity"},
                {"content": " руб.\n", "wrap": "", "type": "EOF"},
            ]
        assert '<ent id="e0">100,00</ent>' in prompt
        assert "Итого: | " not in prompt
        return metadata()

    monkeypatch.setattr("checkier.stage1_tag.call_llm_json", respond)
    tagged, retries = tag_document(cfg, "акт.pdf", "Итого: 100,00 руб.\n")
    assert tagged == (
        '# акт.pdf\nИтого: <ent t="amount" u="RUB" value="100.00">100,00</ent> руб.\n'
    )
    assert retries == 0
    assert calls == ["entity_wrapping", "entity_metadata"]


def test_tag_document_retries_when_gap_repair_is_ambiguous(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path, max_retries=1)
    calls = []

    def respond(prompt, **kwargs):
        calls.append(kwargs["schema_name"])
        if kwargs["schema_name"] == "entity_metadata":
            return metadata()
        return [
            {"content": "# акт.pdf\n", "wrap": "1", "type": "entity"},
            {"content": "a1x" if len(calls) == 1 else "a1", "wrap": "", "type": "EOF"},
        ]

    monkeypatch.setattr("checkier.stage1_tag.call_llm_json", respond)
    _, retries = tag_document(cfg, "акт.pdf", "1a1")
    assert retries == 1
    assert calls == ["entity_wrapping", "entity_wrapping", "entity_metadata"]


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
