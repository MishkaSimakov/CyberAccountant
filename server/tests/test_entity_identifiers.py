"""Pure identity assignment preserves canonical Markdown and supplied IDs."""

import pytest

from processing.common.entities import EntityIdentity
from processing.common.errors import ProcessingValidationError
from processing.common.formula import tokenize
from processing.entities.identifiers import (
    MNEMONIC_CAPACITY, WORD_COUNT, assign_identities, id_for_mnemonic,
    mnemonic_for_id, mnemonic_words, read_identities,
)
from processing.common.markup import parse_entity_tags


def tag(num, mnemonic=None, body="1"):
    mnemonic = mnemonic if mnemonic is not None else mnemonic_for_id(num)
    return f'<ent id="{num}" mnemonic="{mnemonic}" t="number">{body}</ent>'


def test_preserve_existing_identities_and_every_source_character():
    # Retained identities can come from earlier imports; never recompute them.
    existing = '<ent  mnemonic="zoo-zone" id="17" t="amount" value="2 &gt; 1">**1,00**\n</ent>'
    draft = '<ent\n t = "number" value="&quot;1&quot;" >  1 &amp; 2\n</ent>'
    markdown = f'# Заголовок\r\n{existing}\n| {draft} |\n'
    result = assign_identities(markdown, range(20, 21))
    assert result == markdown.replace('<ent\n', '<ent id="20" mnemonic="abandon-action"\n')
    assert read_identities(result) == [EntityIdentity(17, "zoo-zone"), EntityIdentity(20, "abandon-action")]
    assert assign_identities(result, range(21, 21)) == result


def test_assignment_uses_supplied_ids_in_draft_order_including_repeated_bodies():
    existing = tag(500)
    draft = '<ent t="number">1</ent>'
    markdown = f'{draft}\n{existing}\n{draft}'
    result = assign_identities(markdown, range(40, 42))
    assert [identity.num for identity in read_identities(result)] == [40, 500, 41]
    assert existing in result
    assert [result[slice(*item.body_span)] for item in parse_entity_tags(result)] == ["1", "1", "1"]


@pytest.mark.parametrize("markdown", ["", "# Plain markdown\n", tag(0)])
def test_no_drafts_preserves_markdown(markdown):
    assert assign_identities(markdown, range(0)) == markdown


@pytest.mark.parametrize("reserved", [range(0), range(0, 2), range(0, 3, 2), [0], range(-1, 0),
                                      range(MNEMONIC_CAPACITY, MNEMONIC_CAPACITY + 1), range(1, 0)])
def test_invalid_or_wrong_sized_reservations_are_rejected(reserved):
    with pytest.raises(ProcessingValidationError):
        assign_identities('<ent t="number">1</ent>', reserved)


@pytest.mark.parametrize("num", [-1, MNEMONIC_CAPACITY, True, 1.5, "1"])
def test_invalid_ids_are_rejected(num):
    with pytest.raises(ProcessingValidationError):
        mnemonic_for_id(num)


def test_wordlist_and_capacity_boundaries():
    words = mnemonic_words()
    assert len(words) == len(set(words)) == WORD_COUNT == 2048
    assert MNEMONIC_CAPACITY == 4_194_304
    assert mnemonic_for_id(0) == "abandon-abandon"
    assert mnemonic_for_id(WORD_COUNT - 1) == "abandon-zoo"
    assert mnemonic_for_id(WORD_COUNT) == "ability-abandon"
    assert mnemonic_for_id(MNEMONIC_CAPACITY - 1) == "zoo-zoo"
    result = assign_identities('<ent t="number">1</ent>', range(MNEMONIC_CAPACITY - 1, MNEMONIC_CAPACITY))
    assert read_identities(result) == [EntityIdentity(MNEMONIC_CAPACITY - 1, "zoo-zoo")]
    assert assign_identities(result, range(MNEMONIC_CAPACITY, MNEMONIC_CAPACITY)) == result


def test_mapping_is_bijective_and_formula_compatible():
    ids = set(range(WORD_COUNT + 1)) | set(range(MNEMONIC_CAPACITY - WORD_COUNT, MNEMONIC_CAPACITY))
    mnemonics = [mnemonic_for_id(num) for num in ids]
    assert len(set(mnemonics)) == len(ids)
    for num, mnemonic in zip(ids, mnemonics):
        assert id_for_mnemonic(mnemonic) == num
        token, end = tokenize(mnemonic)
        assert (token.kind, token.value, end.kind) == ("id", mnemonic, "eof")


@pytest.mark.parametrize("mnemonic", ["", "abandon", "abandon ability", "abandon-unknownword", "Abandon-ability",
                                      "abandon-ability-able"])
def test_malformed_mnemonics_are_rejected(mnemonic):
    with pytest.raises(ProcessingValidationError):
        id_for_mnemonic(mnemonic)


@pytest.mark.parametrize("markdown", [
    '<ent id="0">1</ent>', '<ent mnemonic="abandon-abandon">1</ent>',
    '<ent id="-1" mnemonic="abandon-abandon">1</ent>',
    '<ent id="01" mnemonic="abandon-abandon">1</ent>',
    '<ent id="abc" mnemonic="abandon-abandon">1</ent>',
    tag(MNEMONIC_CAPACITY, "zoo-zoo"), tag(0, "broken"),
    tag(0) + tag(0, "ability-ability"), tag(0) + tag(1, "abandon-abandon"),
    '<ent id="0" id="1" mnemonic="abandon-abandon">1</ent>',
    '<ent t="number" broken>1</ent>', '<ent t="number"t="date">1</ent>',
    '<ent t="number">1', '</ent>', '<ENT t="number">1</ENT>',
    '<ent t="number"><ent t="number">1</ent></ent>',
    '<entity t="number">1</entity>', '<ent/>',
])
def test_malformed_or_duplicate_identities_and_markup_are_rejected(markdown):
    with pytest.raises(ProcessingValidationError):
        read_identities(markdown)
    with pytest.raises(ProcessingValidationError):
        assign_identities(markdown, range(0))


@pytest.mark.parametrize("existing", [tag(10), tag(0, mnemonic_for_id(10))])
def test_reserved_numeric_or_mnemonic_collision_is_rejected(existing):
    markdown = f'<ent t="number">1</ent>\n{existing}'
    with pytest.raises(ProcessingValidationError, match="collides"):
        assign_identities(markdown, range(10, 11))


def test_markup_offsets_and_escaped_attributes():
    markdown = 'before <ent t="docid" value="&quot;A&quot; > B &amp; C">**А & Б**</ent> after'
    parsed, = parse_entity_tags(markdown)
    assert parsed.attributes == {"t": "docid", "value": '"A" > B & C'}
    assert markdown[slice(*parsed.span)] == markdown.removeprefix("before ").removesuffix(" after")
    assert markdown[slice(*parsed.body_span)] == "**А & Б**"
