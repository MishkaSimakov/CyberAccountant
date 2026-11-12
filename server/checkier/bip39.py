"""Mnemonic ids: two-word combinations from a 1024x1024 word space.

The canonical wordlist is BIP39 English, vendored in
``data/bip39_english.txt`` and fetched by ``scripts/fetch_bip39.py``.

When the vendored file is absent the package falls back to a *deterministic
generated* list of pronounceable pseudo-words (CVC syllable combinations).
The fallback is reproducible, collision-free and human-readable enough for
debugging, but is NOT the BIP39 list — run ``scripts/fetch_bip39.py`` to get
the real one.
"""

from __future__ import annotations

import random
import warnings
from importlib import resources

WORDLIST_RESOURCE = "bip39_english.txt"
HALF = 1024

# CVC generator tables (deterministic fallback)
_CONSONANTS = "bcdfghjklmnpqrstvwxz"
_VOWELS = "aeiouy"


def _generate_fallback(seed: int = 20261112) -> tuple[list[str], list[str]]:
    """Generate 2x1024 unique single-token pseudo-words deterministically.

    Each word is 4-5 letters, consonant-vowel-consonant-vowel, e.g. 'bako',
    'temi'. No hyphens inside words so the mnemonic ``wordA-wordB`` has
    exactly one hyphen and round-trips through the formula tokenizer.
    Uniqueness is guaranteed by construction.
    """
    rng = random.Random(seed)
    cvcv: list[str] = []
    for c1 in _CONSONANTS:
        for v1 in _VOWELS:
            for c2 in _CONSONANTS:
                for v2 in _VOWELS:
                    cvcv.append(f"{c1}{v1}{c2}{v2}")
    rng.shuffle(cvcv)
    words: list[str] = []
    seen: set[str] = set()
    i = 0
    while len(words) < 2 * HALF:
        w = cvcv[i % len(cvcv)]
        i += 1
        if w not in seen:
            seen.add(w)
            words.append(w)
    return words[:HALF], words[HALF:]


def load_words() -> tuple[list[str], list[str]]:
    """Return the two 1024-word halves (A, B) of the mnemonic wordlist.

    Prefers the vendored BIP39 English list. Falls back to a deterministic
    generated list (see module docstring) and emits a RuntimeWarning.
    """
    try:
        text = (
            resources.files("checkier.data")
            .joinpath(WORDLIST_RESOURCE)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError):
        warnings.warn(
            "BIP39 wordlist not found; using deterministic generated fallback. "
            "For the canonical list run: python scripts/fetch_bip39.py",
            RuntimeWarning,
            stacklevel=2,
        )
        return _generate_fallback()
    words = [w.strip() for w in text.splitlines() if w.strip()]
    if len(words) != 2 * HALF or len(set(words)) != 2 * HALF:
        raise RuntimeError(
            f"bip39 wordlist must contain 2048 unique words, got {len(words)} "
            f"({len(set(words))} unique)"
        )
    return words[:HALF], words[HALF:]


class MnemonicSampler:
    """Samples unique 'wordA-wordB' mnemonics from the 1024x1024 space."""

    SPACE = HALF * HALF

    def __init__(self, seed: int = 0) -> None:
        self._a, self._b = load_words()
        self._rng = random.Random(seed)
        self._used: set[int] = set()

    def next(self) -> str:
        if len(self._used) >= self.SPACE:
            raise RuntimeError("mnemonic space exhausted")
        while True:
            i = self._rng.randrange(self.SPACE)
            if i not in self._used:
                self._used.add(i)
                return f"{self._a[i // HALF]}-{self._b[i % HALF]}"
