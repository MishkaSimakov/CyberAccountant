"""Saved review markup uses the same fields as Checkier's registry."""

from datetime import date

import pytest
from fastapi import HTTPException

from app.entities import prepare_entities
from checkier.entities import build_registry


@pytest.mark.parametrize("type_,raw,attrs,expected", [
    ("amount", "245,00", 'u="RUB_K" n="1000" value="250000.00"', "250000.00"),
    ("amount", "245,00", 'u="RUB"', "245.00"),
    ("date", "Январь 2026", 'value="2026-01-01"', date(2026, 1, 1).toordinal()),
    ("date", "неизвестно", 'value=""', None),
    ("docid", 'Акт "А" & Б', 'value="Акт &quot;А&quot; &amp; Б"', 'Акт "А" & Б'),
    ("docid", "А > Б", 'value="А &gt; Б"', "А > Б"),
    ("contract", "24/03/15-01", 'value="24/03/15-01"', "24/03/15-01"),
    ("number", "12,5", 'u="person_month" value="12.5"', "12.5"),
    ("pct", "30%", 'value="30"', "30"),
    ("ratio", "1:4", 'value="0.25"', "0.25"),
])
def test_saved_markup_round_trips_through_checkier(type_, raw, attrs, expected):
    content = f'До <ent t="{type_}" {attrs}>{raw}</ent> после'
    saved = prepare_entities(content)
    assert saved == content
    entry = build_registry({"document": ("data", saved)}).entities[0]
    assert entry.value == expected
    assert entry.raw == raw


@pytest.mark.parametrize("content", [
    '<entity type="number" value="10" unit="rubles">10</entity>',
    '<ent t="id">10</ent>',
    '<ent t="amount" type="number">10</ent>',
    '<ent t="amount" id="1">10</ent>',
    '<ent t="number" t="amount">10</ent>',
    '<ent t="number" broken>10</ent>',
    '<ent t="number" n="NaN">10</ent>',
    '<ent t="number" n="Infinity">10</ent>',
    '<ent t="number" n="">10</ent>',
    '<ent t="date" value="2026-02-30">дата</ent>',
    '<ent t="date" value="2026-01">дата</ent>',
    '<ent t="number">10',
    '</ent>',
    '<ent t="number"><ent t="number">10</ent></ent>',
])
def test_invalid_markup_is_rejected(content):
    with pytest.raises(HTTPException) as error:
        prepare_entities(content)
    assert error.value.status_code == 422


def test_plain_markdown_and_repeated_entries_are_preserved():
    content = '# Title\n<ent t="number">1</ent> <ent t="number">1</ent>\n'
    assert prepare_entities(content) == content
    assert prepare_entities('# Plain markdown\n') == '# Plain markdown\n'


def test_complete_identities_and_original_attribute_formatting_are_preserved():
    content = ('Before <ent\n mnemonic="abandon-abandon"  id="0" t = "number" '
               'value="1 &gt; 0">**1**</ent> <ent t="number">2</ent>\n')
    assert prepare_entities(content) == content


@pytest.mark.parametrize("content", [
    '<ent id="0" mnemonic="broken" t="number">1</ent>',
    '<ent id="0" mnemonic="abandon-abandon" t="number">1</ent>' * 2,
    '<ent id="-1" mnemonic="abandon-abandon" t="number">1</ent>',
    '<ent mnemonic="abandon-abandon" t="number">1</ent>',
])
def test_invalid_complete_or_partial_identities_are_rejected(content):
    with pytest.raises(HTTPException) as error:
        prepare_entities(content)
    assert error.value.status_code == 422
