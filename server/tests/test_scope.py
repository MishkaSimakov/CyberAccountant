"""The app finds exact target labels and supplies their entity IDs to scope."""

import pytest

from app.target_row import find_target_row_keys, find_target_row_spans
from checkier.config import PipelineConfig
from checkier.entities import build_registry
from checkier.stage4_scope import run_scope


def test_target_row_matches_full_label_through_entity_tags(tmp_path):
    tagged = {
        "card1": ("card", (
            '| 3 этап, 2025 г. | <ent t="amount">10</ent> |\n'
            '| 13 этап, 2026 г. | <ent t="amount">20</ent> |\n'
            '| <ent t="number">3</ent> этап, 2026 г. | <ent t="amount">30</ent> |\n'
        )),
        "data1": ("data", '| 3 этап, 2026 г. | <ent t="amount">40</ent> |\n'),
        "req1": ("request", 'Проверить строку «3 этап, 2025 г.»'),
    }
    registry = build_registry(tagged)
    keys = find_target_row_keys(tagged, registry, "3 этап, 2026 г.")
    assert keys == {2, 3}

    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path, id_style="numeric")
    mapping = {key: {"role": role} for key, (role, _) in tagged.items()}
    scope = run_scope(cfg, registry, mapping, target_row=keys, target_label="3 этап, 2026 г.")
    assert scope["target_row"] == [2, 3]
    assert scope["target_label"] == "3 этап, 2026 г."


@pytest.mark.parametrize("label", [
    "3 этап, 2025 г.",
    "13 этап, 2026 г.",
    "3 ЭТАП, 2026 г.",
    "3  этап, 2026 г.",
    "3 этап, 2026 гX",
    None,
    "",
])
def test_target_row_requires_literal_label(label):
    tagged = {"card1": ("card", '| 3 этап, 2026 г. | <ent t="amount">10</ent> |\n')}
    registry = build_registry(tagged)
    assert find_target_row_keys(tagged, registry, label) is None


def test_target_row_does_not_match_part_of_an_id():
    tagged = {"card1": ("card", '| 13 этап, 2026 г. | <ent t="amount">10</ent> |\n')}
    assert find_target_row_keys(tagged, build_registry(tagged), "3 этап, 2026 г.") is None


def test_target_row_ignores_matches_in_supporting_documents():
    tagged = {
        "card1": ("card", '| Другая строка | <ent t="amount">10</ent> |\n'),
        "data1": ("data", '| Итого | <ent t="amount">20</ent> |\n'),
    }
    assert find_target_row_keys(tagged, build_registry(tagged), "Итого") is None


def test_target_row_with_no_entities_returns_none():
    tagged = {"card1": ("card", '| Итого | 10 |\n')}
    assert find_target_row_keys(tagged, build_registry(tagged), "Итого") is None


def test_target_row_spans_work_with_ocr_text_before_entities_exist():
    text = '| 3 этап, 2025 г. | 10 |\r\n| 3 этап, 2026 г. | 20 |'
    start = text.index('| 3 этап, 2026 г.')
    assert find_target_row_spans(text, "3 этап, 2026 г.") == [(start, len(text))]
    assert find_target_row_spans(text, "3 этап, 2024 г.") == []
