"""Utility script to fetch the BIP39 English wordlist.

Run this once to vendor the wordlist into the package:

    python scripts/fetch_bip39.py

The list is saved to ``server/checkier/data/bip39_english.txt``.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/bitcoin/bips/master/bip-0039/english.txt"
DEST = Path(__file__).parent.parent / "server" / "checkier" / "data" / "bip39_english.txt"


def main() -> None:
    DEST.parent.mkdir(parents=True, exist_ok=True)
    print(f"fetching {URL} ...")
    data = urllib.request.urlopen(URL, timeout=30).read().decode("utf-8")
    words = [w.strip() for w in data.splitlines() if w.strip()]
    if len(words) != 2048 or len(set(words)) != 2048:
        raise RuntimeError(f"expected 2048 unique words, got {len(words)}")
    DEST.write_text("\n".join(words) + "\n", encoding="utf-8")
    print(f"saved {len(words)} words to {DEST}")


if __name__ == "__main__":
    main()
