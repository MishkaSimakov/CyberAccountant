"""Canonical mnemonic wordlist and formula compatibility."""

import warnings

from checkier.bip39 import MnemonicSampler, load_words
from processing.common.formula import tokenize


def test_canonical_wordlist_is_bundled():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        a, b = load_words()

    words = a + b
    assert len(a) == len(b) == 1024
    assert len(set(words)) == 2048
    assert words == sorted(words)
    assert words[:3] == ["abandon", "ability", "able"]
    assert words[-3:] == ["zero", "zone", "zoo"]


def test_mnemonics_are_unique_reproducible_formula_ids():
    a, b = load_words()
    sampler = MnemonicSampler(seed=42)
    repeat = MnemonicSampler(seed=42)
    mnemonics = [sampler.next() for _ in range(128)]

    assert len(set(mnemonics)) == 128
    assert mnemonics == [repeat.next() for _ in range(128)]
    for mnemonic in mnemonics:
        first, second = mnemonic.split("-")
        assert first in a and second in b
        token, end = tokenize(mnemonic)
        assert token.kind == "id"
        assert token.value == mnemonic
        assert end.kind == "eof"
