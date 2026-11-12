"""Stage 3: replace <ent> tags with <e id="..."> using registry ids."""

from __future__ import annotations

import re

from cyberaccountant.config import PipelineConfig
from cyberaccountant.entities import ENT_RE, EntityRegistry

# matches the opening tag of an <ent> we are about to replace, capturing attrs
_ENT_OPEN_RE = re.compile(r"<ent\s+([^>]*)>")


def run_replace(
    cfg: PipelineConfig,
    tagged_docs: dict[str, tuple[str, str]],
    registry: EntityRegistry,
) -> dict[str, str]:
    """Rewrite tagged docs: <ent t="amount">245 000</ent> -> <e id="crumble-wink">245 000</e>.

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
            inner = m.group(0)
            body = inner[inner.index(">") + 1 : inner.rindex("</ent>")]
            if ent is None:
                return body
            eid = ent.mnemonic if cfg.id_style == "mnemonic" else str(ent.num)
            return f'<e id="{eid}">{body}</e>'

        replaced = ENT_RE.sub(repl, text)
        (cfg.replaced_dir / f"{h8}.md").write_text(replaced, encoding="utf-8")
        out[h8] = replaced
    return out
