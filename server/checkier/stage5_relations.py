"""Stage 5: LLM relation extraction over role-grouped corpus documents."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from checkier.config import PipelineConfig
from checkier.entities import ENT_RE, EntityRegistry
from checkier.llm import call_llm_json
from checkier.schemas import RELATIONS_SCHEMA

REL_PROMPT = """\
You are an auditor building a system of equations that pins down every value
in the summary report (the "card") using the primary supporting documents.
Entity references look like <ent id="{example_id}" t="amount" value="100">raw text</ent>.
Use {example_key} in formulas to refer to that entity.

=== TASK ===
Verification scope (JSON):
{scope}

Request documents:
{task_documents}

Read the card and supporting documents below. Write relations that verify the
requested card row. The registry lists normalized entity metadata but no
internal filenames.

=== CARD ===
{card_documents}

=== SUPPORTING DOCUMENTS ===
{supporting_documents}

=== ENTITY REGISTRY ===
{registry}

=== OUTPUT ===
Return this exact JSON format (array of objects):

1. Equations — every relation that must hold between entity ids:
   {{"equation": "{example_equation}", "tag": "agg"}}
   tags: scope (contract or reporting period) | agg (aggregation) | ref (cross-doc identity)

2. Textual findings — when data looks wrong or cannot be verified with an equation:
   {{"evidence": "one short sentence describing the problem", "ids_related": ["{example_id}"], "verdict": "false"}}
   Use verdict "unverifiable" when evidence is insufficient, and "false" for an identified problem.

Formula language:
- ids: {id_description}
- ops: + - * / ( ) == != < <= > >= && ||
- functions: norm(id) — normalized value; raw(id) — unnormalized numeric value
- dates: bare date IDs and start(id) mean the inclusive start ordinal; end(id)
  means the inclusive end ordinal; DD.MM.YYYY literals compare with both
- strings only in == / != comparisons

Rules:
- PREFER entity IDs over literal numbers.
- Every CARD cell of the target report row must appear in at least one equation,
  or be explicitly marked unverifiable via a fishy entry.
- Cover ALL aggregations: card totals == sum of their components (from both card and data).
- Cross-document identity: if the same logical value appears in two docs, relate them with tag "ref".
- For period scope, use whole-interval containment: period start <= start(id)
  and end(id) <= period end. A bare date alone checks only its start.
- Documents for other contracts or outside the reporting period must not contribute to the target row's totals.
- Preserve supplied normalized values; do not replace unknown values with guesses.
- Output ONLY the JSON array, nothing else.
"""


def _render_document(
        text: str,
        entry: dict,
        mode: str,
        neutral_label: str,
) -> str:
    """Select a filename/label and remove only a recognized generated header."""
    original_value = entry.get("original")
    original = Path(str(original_value)).name if original_value else neutral_label
    first_line, separator, remainder = text.partition("\n")
    processed = None
    body = text
    if separator and first_line.startswith("# "):
        tagged_name = first_line[2:]
        plain_name = ENT_RE.sub(lambda match: match.group(2), tagged_name)
        if plain_name == original:
            processed = tagged_name
            body = remainder
    if mode == "tagged":
        label = processed or original
    elif mode == "raw":
        label = original
    else:
        label = neutral_label
    return f"--- {label} ---\n{body}"


def _registry_lines(cfg: PipelineConfig, registry: EntityRegistry) -> list[str]:
    lines = []
    for entity in registry.entities:
        # Removed filename headers must not leak back through registry values.
        if cfg.relation_filename_mode != "tagged" and entity.loc == "filename":
            continue
        entity_id = entity.mnemonic if cfg.id_style == "mnemonic" else f"#{entity.num}"
        if entity.type == "date":
            start = (
                date.fromordinal(int(entity.value)).isoformat() + "T00:00:00"
                if entity.value is not None else ""
            )
            end = (
                date.fromordinal(int(entity.end_value)).isoformat() + "T23:59:59"
                if entity.end_value is not None else ""
            )
            values = f"start={start}\tend={end}"
        else:
            values = f"value={entity.value}"
        lines.append(f"{entity_id}\ttype={entity.type}\t{values}\tunit={entity.unit}\trole={entity.role}")
    return lines


def generate_relations(
        cfg: PipelineConfig,
        replaced_docs: dict[str, str],
        registry: EntityRegistry,
        scope: dict,
        mapping: dict | None = None,
) -> list[dict]:
    """Generate relations from role-grouped documents without internal IDs."""
    mapping = mapping or {}
    roles = {entity.file: entity.role for entity in registry.entities}
    groups = {"request": [], "card": [], "data": []}
    for index, (key, text) in enumerate(sorted(replaced_docs.items()), 1):
        entry = mapping.get(key, {})
        role = entry.get("role") or roles.get(key, "data")
        group = role if role in groups else "data"
        groups[group].append(_render_document(
            text, entry, cfg.relation_filename_mode, f"Document {index}",
        ))
    prompt = REL_PROMPT.format(
        task_documents="\n\n".join(groups["request"]) or "No separate request document supplied.",
        card_documents="\n\n".join(groups["card"]) or "No card document supplied.",
        supporting_documents="\n\n".join(groups["data"]) or "No supporting documents supplied.",
        registry="\n".join(_registry_lines(cfg, registry)),
        scope=json.dumps(scope, ensure_ascii=False),
        example_id="0" if cfg.id_style == "numeric" else "crumble-wink",
        example_key="#0" if cfg.id_style == "numeric" else "crumble-wink",
        example_equation="#0 + #1 == #2" if cfg.id_style == "numeric" else "crumble-wink + able-fog == rapid-castle",
        id_description="# followed by a numeric ID, such as #0" if cfg.id_style == "numeric" else "mnemonic word pairs, such as crumble-wink",
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
    return [dict(r, source="llm") for r in relations]


def run_relations(
        cfg: PipelineConfig,
        replaced_docs: dict[str, str],
        registry: EntityRegistry,
        scope: dict,
        mapping: dict | None = None,
) -> list[dict]:
    """Notebook pipeline wrapper: generate requirements and save the result."""
    all_relations = generate_relations(cfg, replaced_docs, registry, scope, mapping=mapping)
    cfg.relations_path.write_text(
        json.dumps(all_relations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return all_relations
