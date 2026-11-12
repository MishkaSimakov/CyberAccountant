"""Stage 5: LLM relation extraction over the whole corpus."""

from __future__ import annotations

import json

from checkier.config import PipelineConfig
from checkier.entities import EntityRegistry
from checkier.llm import call_llm_json
from checkier.schemas import RELATIONS_SCHEMA

REL_PROMPT = """\
You are an auditor building a system of equations that pins down every value
in the summary report (the "card") using the primary documents ("data").

Below are the primary documents. Entity references look like <e id="mnemonic">raw text</e>.
The registry lists every entity: id, type, normalized value, unit, role
(data = truth / constant, card = variable to verify, request = scope definition).

=== DOCUMENTS ===
{documents}

=== ENTITY REGISTRY ===
{registry}

=== TASK ===
Write relations in this exact JSON format (array of objects):

1. Equations — every relation that must hold between entity ids:
   {{"equation": "crumble-wink + able-fog == rapid-castle", "tag": "agg"}}
   tags: contract | period | agg (aggregation) | ref (cross-doc identity)

2. Fishy data — when data looks wrong but can't be expressed as an equation:
   {{"evidence": "one short sentence describing the problem", "ids_related": ["id1"], "verdict": "false"}}

Formula language:
- ids: mnemonic-word pairs (or #N when numeric style is used)
- ops: + - * / ( ) == != < <= > >= && ||
- functions: norm(id) — value normalized to base units (same as bare id); raw(id) — unnormalized
- dates: DD.MM.YYYY literals compare with date ids
- strings only in == / != comparisons

Rules:
- PREFER ids over literal numbers: write "a + b == c", not "a + 1000 == c".
- Every CARD cell of the target report row must appear in at least one equation,
  or be explicitly marked unverifiable via a fishy entry.
- Cover ALL aggregations: card totals == sum of their components (from both card and data).
- Cross-document identity: if the same logical value appears in two docs, relate them with tag "ref".
- Output ONLY the JSON array, nothing else.
"""


def run_relations(
        cfg: PipelineConfig,
        replaced_docs: dict[str, str],
        registry: EntityRegistry,
        scope: dict,
) -> list[dict]:
    """Ask the LLM for all relations; combine with deterministic scope relations."""
    in_scope = set(scope["in_scope"])
    documents = []
    for h8, text in sorted(replaced_docs.items()):
        if h8 in in_scope or any(
                e.file == h8 and e.role == "card" for e in registry.entities
        ):
            documents.append(f"--- file {h8} ---\n{text}")
    reg_lines = []
    for e in registry.entities:
        if e.role == "card" or e.file in in_scope:
            eid = e.mnemonic if cfg.id_style == "mnemonic" else f"#{e.num}"
            reg_lines.append(
                f"{eid}\ttype={e.type}\tvalue={e.value}\tunit={e.unit}\t"
                f"role={e.role}\tfile={e.file}"
            )
    prompt = REL_PROMPT.format(
        documents="\n\n".join(documents),
        registry="\n".join(reg_lines),
    )
    relations = call_llm_json(
        prompt,
        model=cfg.rel_model,
        schema=RELATIONS_SCHEMA,
        schema_name="relations",
        timeout=cfg.request_timeout,
        max_retries=cfg.max_retries,
        reasoning=cfg.reasoning,
    )
    if isinstance(relations, dict):
        relations = [relations]
    all_relations = list(scope["scope_relations"]) + [
        dict(r, source="llm") for r in relations
    ]
    cfg.relations_path.write_text(
        json.dumps(all_relations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return all_relations
