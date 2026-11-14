"""Relation prompts group roles and honor original-filename privacy modes."""

import pytest

from checkier import PipelineConfig
from checkier.entities import ENT_RE, build_registry, parse_tag_attrs
from checkier.stage3_replace import run_replace
from checkier.stage5_relations import generate_relations


def _context(tmp_path, monkeypatch, mode, hide_values=False, id_style="numeric"):
    tagged = {
        "internal-card": ("card", (
            '# <ent t="docid" value="card.pdf">card.pdf</ent>\n'
            '<ent t="amount" u="RUB_K" n="1000" value="98765.43">98 765,43</ent>'
        )),
        "internal-data": ("data", (
            '# <ent t="docid" value="support.pdf">support.pdf</ent>\n'
            '<ent t="date" start="2026-05-01T00:00:00" end="2026-05-31T23:59:59">Май 2026</ent>'
        )),
        "internal-request": ("request", 'Проверить итог <ent t="contract" value="REQUEST-938">REQUEST-938</ent>'),
    }
    mapping = {
        "internal-card": {"role": "card", "original": "card.pdf"},
        "internal-data": {"role": "data", "original": "support.pdf"},
        "internal-request": {"role": "request", "original": "request.md"},
    }
    cfg = PipelineConfig(
        data_dir=tmp_path, out_dir=tmp_path, id_style=id_style, relation_filename_mode=mode,
        relation_hide_entity_values=hide_values,
    )
    registry = build_registry(tagged)
    documents = run_replace(cfg, tagged, registry)
    original_documents = dict(documents)
    original_entities = [entity.to_dict() for entity in registry.entities]
    prompts = []
    monkeypatch.setattr(
        "checkier.stage5_relations.call_llm_json",
        lambda prompt, **kwargs: prompts.append(prompt) or [],
    )
    assert generate_relations(cfg, documents, registry, {"target_row": [1]}, mapping) == []
    assert documents == original_documents
    assert [entity.to_dict() for entity in registry.entities] == original_entities
    for key, text in original_documents.items():
        assert (cfg.replaced_dir / f"{key}.md").read_text(encoding="utf-8") == text
    return prompts[0]


def test_tagged_mode_groups_sections_and_uses_processed_filenames(tmp_path, monkeypatch):
    prompt = _context(tmp_path, monkeypatch, "tagged")
    assert prompt.index("=== TASK ===") < prompt.index("=== CARD ===") < prompt.index("=== SUPPORTING DOCUMENTS ===")
    assert "Проверить итог" in prompt
    assert '<ent id="0" t="docid" value="card.pdf">card.pdf</ent>' in prompt
    assert prompt.count("card.pdf</ent>") == 1  # Filename header is not repeated in the body.
    assert "--- file internal-card ---" not in prompt
    assert "file=internal" not in prompt
    assert 'start="2026-05-01T00:00:00" end="2026-05-31T23:59:59"' in prompt
    assert "start(id)" in prompt and "end(id)" in prompt


def test_raw_mode_uses_original_names_without_tagged_headers(tmp_path, monkeypatch):
    prompt = _context(tmp_path, monkeypatch, "raw")
    assert "--- card.pdf ---\n<ent" in prompt
    assert "--- support.pdf ---\n<ent" in prompt
    assert '<ent id="0" t="docid" value="card.pdf">card.pdf</ent>' not in prompt


def test_none_mode_uses_neutral_labels_and_hides_all_file_ids(tmp_path, monkeypatch):
    prompt = _context(tmp_path, monkeypatch, "none")
    assert "--- Document 1 ---" in prompt
    assert "--- Document 2 ---" in prompt
    assert "--- Document 3 ---" in prompt
    assert "card.pdf" not in prompt
    assert "support.pdf" not in prompt
    assert "request.md" not in prompt
    assert "internal-card" not in prompt
    assert "file=internal" not in prompt


def test_older_artifacts_without_processed_header_fall_back_safely(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path, id_style="numeric")
    registry = build_registry({"old": ("data", "# Genuine title\n<ent t=\"amount\">5</ent>")})
    prompts = []
    monkeypatch.setattr(
        "checkier.stage5_relations.call_llm_json",
        lambda prompt, **kwargs: prompts.append(prompt) or [],
    )
    generate_relations(
        cfg, {"old": "# Genuine title\n<ent id=\"0\" t=\"amount\">5</ent>"},
        registry, {}, {"old": {"role": "data", "original": "old.pdf"}},
    )
    assert "--- old.pdf ---\n# Genuine title" in prompts[0]


def test_filename_mode_is_validated(tmp_path):
    with pytest.raises(ValueError, match="relation_filename_mode"):
        PipelineConfig(data_dir=tmp_path, out_dir=tmp_path, relation_filename_mode="hidden")


@pytest.mark.parametrize("mode", ["tagged", "raw", "none"])
@pytest.mark.parametrize("id_style", ["numeric", "mnemonic"])
def test_hidden_entity_values_in_stage5_prompt(tmp_path, monkeypatch, mode, id_style):
    prompt = _context(tmp_path, monkeypatch, mode, hide_values=True, id_style=id_style)
    assert "=== HIDDEN ENTITY VALUES ===" in prompt
    assert "not missing data" in prompt
    assert "98765.43" not in prompt
    assert "98 765,43" not in prompt
    assert "2026-05-01" not in prompt
    assert "2026-05-31" not in prompt
    assert "Май 2026" not in prompt
    assert "REQUEST-938" not in prompt
    assert "Проверить итог" in prompt
    assert 'u="RUB_K" n="1000"' in prompt
    entities = list(ENT_RE.finditer(prompt))
    assert entities
    for entity in entities:
        attrs = parse_tag_attrs(entity.group(1))
        assert "id" in attrs and "t" in attrs
        assert not {"value", "start", "end"} & attrs.keys()
        assert entity.group(2) == ""
    if mode == "tagged":
        assert "card.pdf" not in prompt
        assert "support.pdf" not in prompt
        assert prompt.count('t="docid"') == 2
    if id_style == "numeric":
        assert '<ent id="0" t="amount"></ent>' in prompt
        assert "#0 + #1 == #2" in prompt
    else:
        assert '<ent id="crumble-wink" t="amount"></ent>' in prompt
        assert "crumble-wink + able-fog == rapid-castle" in prompt


def test_entity_values_remain_visible_by_default(tmp_path, monkeypatch):
    assert PipelineConfig(data_dir=tmp_path, out_dir=tmp_path).relation_hide_entity_values is False
    prompt = _context(tmp_path, monkeypatch, "tagged")
    assert "=== HIDDEN ENTITY VALUES ===" not in prompt
    assert 'value="98765.43">98 765,43</ent>' in prompt
    assert 'start="2026-05-01T00:00:00" end="2026-05-31T23:59:59">Май 2026</ent>' in prompt
    assert 'value="REQUEST-938">REQUEST-938</ent>' in prompt
