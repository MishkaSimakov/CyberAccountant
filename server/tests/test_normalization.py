"""Review markup carries normalized values and inclusive date intervals."""

from datetime import date
import json

import pytest

from checkier import PipelineConfig, normalize_document
from checkier.entities import ENT_RE, build_registry, parse_date_interval, parse_tag_attrs


@pytest.mark.parametrize("type_,raw,attributes,value", [
    ("amount", "245 000,00", ' u="RUB"', "245000.00"),
    ("amount", "245,00", ' u="RUB_K" n="1000"', "245000.00"),
    ("amount", "0,00", ' u="RUB"', "0.00"),
    ("amount", "-1 200,50", ' u="RUB"', "-1200.50"),
    ("number", "12,5", "", "12.5"),
    ("pct", "30 %", "", "30"),
    ("ratio", "1/2", "", "0.5"),
    ("ratio", "1:4", "", "0.25"),
    ("contract", "24/03/15-01", "", "24/03/15-01"),
    ("docid", 'Акт "А" & Б', "", 'Акт "А" & Б'),
    ("amount", "неизвестно", "", ""),
])
def test_normalized_values_preserve_source_text(tmp_path, type_, raw, attributes, value):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path / "unused")
    tagged = f'До <ent t="{type_}"{attributes}>{raw}</ent> после\n'
    result = normalize_document(cfg, tagged)
    match = ENT_RE.search(result)
    assert match is not None
    assert match.group(2) == raw
    assert parse_tag_attrs(match.group(1))["value"] == value
    assert ENT_RE.sub(lambda match: match.group(2), result) == f"До {raw} после\n"
    assert normalize_document(cfg, result) == result
    assert not cfg.out_dir.exists()


@pytest.mark.parametrize("raw,start,end", [
    ('"10" мая 2026 г.', date(2026, 5, 10), date(2026, 5, 10)),
    ('«10» мая 2026', date(2026, 5, 10), date(2026, 5, 10)),
    ("28.01.2026", date(2026, 1, 28), date(2026, 1, 28)),
    ("Февраль 2024", date(2024, 2, 1), date(2024, 2, 29)),
    ("2 квартал 2026", date(2026, 4, 1), date(2026, 6, 30)),
    ("2026 год", date(2026, 1, 1), date(2026, 12, 31)),
])
def test_deterministic_date_intervals(tmp_path, raw, start, end):
    assert parse_date_interval(raw) == (start, end)
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path)
    tagged = f'<ent t="date">{raw}</ent>'
    result = normalize_document(cfg, tagged)
    expected = (
        f'<ent t="date" start="{start.isoformat()}T00:00:00" '
        f'end="{end.isoformat()}T23:59:59">{raw}</ent>'
    )
    assert result == expected
    assert normalize_document(cfg, result) == result
    entity = build_registry({"file": ("data", result)}).entities[0]
    assert (entity.value, entity.end_value) == (start.toordinal(), end.toordinal())


@pytest.mark.parametrize("raw", ["Январь", "квартал 2026", "2026-02-30", "неизвестно"])
def test_incomplete_or_invalid_dates_stay_unresolved(raw):
    assert parse_date_interval(raw) is None


def test_fallback_normalization_returns_both_bounds_and_provenance(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path)
    calls = []

    def normalize(prompt, **kwargs):
        calls.append(prompt)
        return [{
            "raw": "особый период 2026", "start": "01.04.2026", "end": "30.06.2026",
        }]

    monkeypatch.setattr("checkier.stage2_dates.call_llm_json", normalize)
    result = normalize_document(cfg, '<ent t="date">особый период 2026</ent>')
    assert result == (
        '<ent t="date" start="2026-04-01T00:00:00" end="2026-06-30T23:59:59">'
        'особый период 2026</ent>'
    )
    entity = build_registry({"file": ("data", result)}).entities[0]
    assert (entity.value, entity.end_value) == (date(2026, 4, 1).toordinal(), date(2026, 6, 30).toordinal())
    assert len(calls) == 1
    assert normalize_document(cfg, result) == result
    assert len(calls) == 1


def test_registry_retains_fallback_normalization_provenance(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path)
    monkeypatch.setattr(
        "checkier.stage2_dates.call_llm_json",
        lambda *args, **kwargs: [{
            "raw": "особый период", "start": "01.01.2026", "end": "31.01.2026",
        }],
    )
    registry = build_registry({"file": ("data", '<ent t="date">особый период</ent>')})
    from checkier.stage2_dates import run_date_normalization
    changed = run_date_normalization(cfg, registry)
    assert len(changed) == 1
    assert changed[0].normalized_from == "особый период"
    assert changed[0].end_value == date(2026, 1, 31).toordinal()


@pytest.mark.parametrize("response", [
    [{"raw": "особый период", "start": "01.01.2026", "end": None}],
    [{"raw": "особый период", "start": "02.01.2026", "end": "01.01.2026"}],
])
def test_fallback_rejects_incomplete_and_reversed_intervals(tmp_path, monkeypatch, response):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path)
    monkeypatch.setattr("checkier.stage2_dates.call_llm_json", lambda *args, **kwargs: response)
    registry = build_registry({"file": ("data", '<ent t="date">особый период</ent>')})
    from checkier.stage2_dates import run_date_normalization
    with pytest.raises(ValueError):
        run_date_normalization(cfg, registry)


def test_unresolved_date_is_explicit(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path)
    monkeypatch.setattr(
        "checkier.stage2_dates.call_llm_json",
        lambda *args, **kwargs: [{"raw": "неизвестно", "start": None, "end": None}],
    )
    assert normalize_document(cfg, '<ent t="date">неизвестно</ent>') == (
        '<ent t="date" start="" end="">неизвестно</ent>'
    )


def test_null_date_normalization_preserves_other_entities(tmp_path, monkeypatch):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path)
    calls = []

    def response(*args, **kwargs):
        calls.append(args)
        return json.dumps([{"raw": "Январь", "start": None, "end": None}])

    monkeypatch.setattr("checkier.llm.call_llm", response)
    tagged = ('Период: <ent t="date">Январь</ent>\n'
              '<ent t="date">Январь 2026</ent>\n'
              'Итого: <ent t="amount">100,00</ent>')
    result = normalize_document(cfg, tagged)
    assert result == (
        'Период: <ent t="date" start="" end="">Январь</ent>\n'
        '<ent t="date" start="2026-01-01T00:00:00" end="2026-01-31T23:59:59">Январь 2026</ent>\n'
        'Итого: <ent t="amount" value="100.00">100,00</ent>'
    )
    assert len(calls) == 1
    assert ENT_RE.sub(lambda match: match.group(2), result) == ENT_RE.sub(lambda match: match.group(2), tagged)


def test_registry_uses_reviewed_value_without_scaling_twice(tmp_path):
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path)
    tagged = '<ent t="amount" u="RUB_K" n="1000">245,00</ent>'
    normalized = normalize_document(cfg, tagged)
    assert build_registry({"file": ("data", normalized)}).entities[0].value == "245000.00"
    reviewed = normalized.replace('value="245000.00"', 'value="250000.00"')
    assert build_registry({"file": ("data", reviewed)}).entities[0].value == "250000.00"
