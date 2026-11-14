"""Stage 5: LLM relation extraction over the whole corpus."""

from __future__ import annotations

import logging

import json

from checkier.config import PipelineConfig
from processing.common.registry import EntityRegistry
from processing.common.llm import ModelClient, ModelRequestOptions
from checkier.schemas import RELATIONS_SCHEMA

REL_PROMPT = """\
You are an auditor building a system of equations that pins down every value
in the summary report (the "card") using the primary documents ("data").

Below are the documents. Entity references look like <ent id="{example_id}" t="amount" value="100">raw text</ent>.
Use {example_key} in formulas to refer to that entity.
The registry lists every entity: id, type, normalized value, unit, role
(data = truth / constant, card = variable to verify, request = scope definition).

=== DOCUMENTS ===
{documents}

=== ENTITY REGISTRY ===
{registry}

=== VERIFICATION SCOPE ===
{scope}

=== TASK ===
Write relations in this exact JSON format (array of objects):

1. Equations — every relation that must hold between entity ids:
   {{"equation": "{example_equation}", "tag": "agg"}}
   tags: scope (contract or reporting period) | agg (aggregation) | ref (cross-doc identity)

2. Textual findings — when data looks wrong or cannot be verified with an equation:
   {{"evidence": "one short sentence describing the problem", "ids_related": ["{example_id}"], "verdict": "false"}}
   Use verdict "unverifiable" when evidence is insufficient, and "false" for an identified problem.

Formula language:
- ids: {id_description}
- ops: + - * / ( ) == != < <= > >= && ||
- functions: norm(id) — value normalized to base units (same as bare id); raw(id) — unnormalized
- dates: DD.MM.YYYY literals compare with date ids
- strings only in == / != comparisons

Rules:
- PREFER entity IDs over literal numbers.
- Every CARD cell of the target report row must appear in at least one equation,
  or be explicitly marked unverifiable via a fishy entry.
- Cover ALL aggregations: card totals == sum of their components (from both card and data).
- Cross-document identity: if the same logical value appears in two docs, relate them with tag "ref".
- Documents for other contracts or outside the reporting period must not contribute to the target row's totals.
- Preserve the supplied normalized values; do not replace unknown values with guesses.
- Output ONLY the JSON array, nothing else.
"""


def generate_relations(
        cfg: PipelineConfig,
        replaced_docs: dict[str, str],
        registry: EntityRegistry,
        scope: dict,
        *, client: ModelClient,
        logger: logging.Logger | logging.LoggerAdapter | None = None,
) -> list[dict]:
    """Generate relations from the supplied corpus without filtering or saving it."""
    logger = logger if logger is not None else logging.getLogger(__name__)
    documents = [f"--- file {key} ---\n{text}" for key, text in sorted(replaced_docs.items())]
    reg_lines = []
    for e in registry.entities:
        eid = e.mnemonic if cfg.id_style == "mnemonic" else f"#{e.num}"
        reg_lines.append(
            f"{eid}\ttype={e.type}\tvalue={e.value}\tunit={e.unit}\t"
            f"role={e.role}\tfile={e.file}"
        )
    prompt = REL_PROMPT.format(
        documents="\n\n".join(documents),
        registry="\n".join(reg_lines),
        scope=json.dumps(scope, ensure_ascii=False),
        example_id="0" if cfg.id_style == "numeric" else "crumble-wink",
        example_key="#0" if cfg.id_style == "numeric" else "crumble-wink",
        example_equation="#0 + #1 == #2" if cfg.id_style == "numeric" else "crumble-wink + able-fog == rapid-castle",
        id_description="# followed by a numeric ID, such as #0" if cfg.id_style == "numeric" else "mnemonic word pairs, such as crumble-wink",
    )
    relations = client.call_llm_json(
        prompt,
        options=ModelRequestOptions(
            model=cfg.rel_model,
            schema=RELATIONS_SCHEMA,
            schema_name="relations",
            timeout=cfg.request_timeout,
            max_retries=cfg.max_retries,
            reasoning=cfg.reasoning,
        ),
        logger=logger,
    )
    if isinstance(relations, dict):
        relations = [relations]
    return [dict(r, source="llm") for r in relations]


def run_relations(
        cfg: PipelineConfig,
        replaced_docs: dict[str, str],
        registry: EntityRegistry,
        scope: dict,
        *, client: ModelClient,
        logger: logging.Logger | logging.LoggerAdapter | None = None,
) -> list[dict]:
    """Notebook pipeline wrapper: generate requirements and save the result."""
    all_relations = generate_relations(cfg, replaced_docs, registry, scope, client=client, logger=logger)
    cfg.relations_path.write_text(
        json.dumps(all_relations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return all_relations
