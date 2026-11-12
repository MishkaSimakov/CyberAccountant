"""Stage 0: ingest markdown files, assign random obfuscated names, detect duplicates."""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from pathlib import Path

from cyberaccountant.config import PipelineConfig, ROLE_DIRS


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _fresh_name(taken: set[str], files_dir: Path) -> str:
    while True:
        name = secrets.token_hex(4)  # 8 hex chars, 2^32 space
        if name not in taken and not (files_dir / f"{name}.md").exists():
            return name


def run_ingest(cfg: PipelineConfig) -> dict:
    """Copy docs from data_dir to out/files/<h8>.md, write mapping.json.

    Every input file gets its own obfuscated name. Byte-identical files keep
    separate entries; the later one gets ``duplicate_of`` pointing at the
    first and tag ``dup`` (it is excluded from later stages but stays on disk
    and in the mapping — filenames carry information and we must not silently
    drop "the wrong one").

    Returns the mapping dict {h8: {original, role, sha256, duplicate_of, tag?}}.
    """
    cfg.files_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, dict] = {}
    hash_to_first: dict[str, str] = {}
    taken: set[str] = set()

    files: list[tuple[Path, str]] = []
    for sub, role in ROLE_DIRS.items():
        d = cfg.data_dir / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            files.append((p, role))

    for path, role in files:
        digest = _sha256(path)
        name = _fresh_name(taken, cfg.files_dir)
        taken.add(name)
        shutil.copy2(path, cfg.files_dir / f"{name}.md")
        entry: dict = {
            "original": str(path.relative_to(cfg.data_dir)),
            "role": role,
            "sha256": digest,
            "duplicate_of": None,
        }
        if digest in hash_to_first:
            entry["duplicate_of"] = hash_to_first[digest]
            entry["tag"] = "dup"
        else:
            hash_to_first[digest] = name
        mapping[name] = entry

    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    cfg.mapping_path.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return mapping
