"""Review markup carries normalized values that survive registry rebuilding."""

from datetime import date
import json
import logging

import pytest

from processing.entities.normalization import normalize_document
from processing.entities.options import EntityOptions
from processing.entities.markup import ENT_RE, parse_tag_attrs
from entity_fixtures import registry_from_drafts as build_registry


@pytest.mark.parametrize("type_,raw,attributes,value", [
    ("amount", "245 000,00", ' u="RUB"', "245000.00"),
    ("amount", "245,00", ' u="RUB_K" n="1000"', "245000.00"),
    ("amount", "0,00", ' u="RUB"', "0.00"),
    ("amount", "-1 200,50", ' u="RUB"', "-1200.50"),
    ("number", "12,5", "", "12.5"),
    ("pct", "30 %", "", "30"),
    ("ratio", "1/2", "", "0.5"),
    ("ratio", "1:4", "", "0.25"),
    ("date", "28.01.2026", "", "2026-01-28"),
    ("contract", "24/03/15-01", "", "24/03/15-01"),
    ("docid", 'Акт "А" & Б', "", 'Акт "А" & Б'),
    ("amount", "неизвестно", "", ""),
])
def test_normalized_values_preserve_source_text(tmp_path, type_, raw, attributes, value, llm_client):
    cfg = EntityOptions()
    tagged = f'До <ent t="{type_}"{attributes}>{raw}</ent> после\n'
    result = normalize_document(cfg, tagged, client=llm_client, logger=logging.getLogger(__name__))
    match = ENT_RE.search(result)
    assert match.group(2) == raw
    assert parse_tag_attrs(match.group(1))["value"] == value
    assert ENT_RE.sub(lambda match: match.group(2), result) == f"До {raw} после\n"
    assert normalize_document(cfg, result, client=llm_client, logger=logging.getLogger(__name__)) == result
    assert list(tmp_path.iterdir()) == []


def test_period_dates_are_normalized_before_review_and_reused(tmp_path, monkeypatch, llm_client):
    cfg = EntityOptions()
    calls = []

    def normalize(prompt, *args, **kwargs):
        calls.append(prompt)
        return [{"raw": "Январь 2026", "normalized": "01.01.2026"}]

    monkeypatch.setattr(llm_client, "call_llm_json", normalize)
    tagged = '<ent t="date">Январь 2026</ent>'
    result = normalize_document(cfg, tagged, client=llm_client, logger=logging.getLogger(__name__))
    assert result == '<ent t="date" value="2026-01-01">Январь 2026</ent>'
    assert len(calls) == 1
    registry = build_registry({"file": ("data", result)})
    assert registry.entities[0].value == date(2026, 1, 1).toordinal()
    assert normalize_document(cfg, result, client=llm_client, logger=logging.getLogger(__name__)) == result
    assert len(calls) == 1  # Saved dates do not need another LLM call.


def test_registry_uses_reviewed_value_without_scaling_twice(tmp_path, llm_client):
    cfg = EntityOptions()
    tagged = '<ent t="amount" u="RUB_K" n="1000">245,00</ent>'
    normalized = normalize_document(cfg, tagged, client=llm_client, logger=logging.getLogger(__name__))
    assert build_registry({"file": ("data", normalized)}).entities[0].value == "245000.00"
    reviewed = normalized.replace('value="245000.00"', 'value="250000.00"')
    assert build_registry({"file": ("data", reviewed)}).entities[0].value == "250000.00"


def test_unresolved_date_is_explicit(tmp_path, monkeypatch, llm_client):
    cfg = EntityOptions()
    monkeypatch.setattr(llm_client, "call_llm_json", lambda *args, **kwargs: [])
    assert normalize_document(cfg, '<ent t="date">неизвестно</ent>', client=llm_client, logger=logging.getLogger(__name__)) == (
        '<ent t="date" value="">неизвестно</ent>'
    )


def test_null_date_normalization_preserves_other_entities(tmp_path, monkeypatch, llm_client):
    cfg = EntityOptions()
    calls = []

    def response(*args, **kwargs):
        calls.append(args)
        return json.dumps([
            {"raw": "Январь", "normalized": None},
            {"raw": "Январь 2026", "normalized": "01.01.2026"},
        ])

    monkeypatch.setattr(llm_client, "call_llm", response)
    tagged = ('Период: <ent t="date">Январь</ent>\n'
              '<ent t="date">Январь 2026</ent>\n'
              'Итого: <ent t="amount">100,00</ent>')
    result = normalize_document(cfg, tagged, client=llm_client, logger=logging.getLogger(__name__))
    assert result == ('Период: <ent t="date" value="">Январь</ent>\n'
                      '<ent t="date" value="2026-01-01">Январь 2026</ent>\n'
                      'Итого: <ent t="amount" value="100.00">100,00</ent>')
    assert len(calls) == 1
    assert ENT_RE.sub(lambda match: match.group(2), result) == ENT_RE.sub(lambda match: match.group(2), tagged)
