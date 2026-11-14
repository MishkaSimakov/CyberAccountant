"""Stage 3: add global registry IDs to the existing <ent> tags."""

from __future__ import annotations

import re

from checkier.config import PipelineConfig
from processing.entities.markup import ENT_RE, parse_tag_attrs
from processing.common.registry import EntityRegistry


def run_replace(
        cfg: PipelineConfig,
        tagged_docs: dict[str, tuple[str, str]],
        registry: EntityRegistry,
) -> dict[str, str]:
    """Add IDs while preserving <ent> tags, their attributes, and source text.

    Original text is kept inside the tag; `id` is authoritative. Returns
    {h8: replaced_text} and writes out/replaced/<h8>.md.
    """
    cfg.replaced_dir.mkdir(parents=True, exist_ok=True)
    # registry order == occurrence order per file; walk each doc's tags and
    # consume entities in order
    out: dict[str, str] = {}
    by_file: dict[str, list] = {}
    for e in registry.entities:
        by_file.setdefault(e.file, []).append(e)

    for h8, (_role, text) in sorted(tagged_docs.items()):
        entities = by_file.get(h8, [])
        idx = 0

        def repl(m: re.Match) -> str:
            nonlocal idx
            ent = entities[idx] if idx < len(entities) else None
            idx += 1
            if ent is None:
                return m.group(0)
            if "id" in parse_tag_attrs(m.group(1)):
                return m.group(0)
            eid = ent.mnemonic if cfg.id_style == "mnemonic" else str(ent.num)
            return f'<ent id="{eid}" {m.group(1)}>{m.group(2)}</ent>'

        replaced = ENT_RE.sub(repl, text)
        (cfg.replaced_dir / f"{h8}.md").write_text(replaced, encoding="utf-8")
        out[h8] = replaced
    return out
