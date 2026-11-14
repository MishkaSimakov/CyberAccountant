"""Stage 5: LLM relation extraction over role-grouped corpus documents."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from checkier.config import PipelineConfig
from checkier.entities import ATTR_RE, ENT_RE, EntityRegistry
from checkier.llm import call_llm_json
from checkier.schemas import RELATIONS_SCHEMA

REL_PROMPT = """\
You are an auditor building a system of equations that pins down every value
in the summary report (the "card") using the primary supporting documents.
Entity references look like {example_entity}.
Use {example_key} in formulas to refer to that entity.

{entity_values_section}
=== TASK ===
Verification scope (JSON):
{scope}

Request documents:
{task_documents}

Read the card and supporting documents below. Write relations that verify the
requested card row.

=== CARD ===
{card_documents}

=== SUPPORTING DOCUMENTS ===
{supporting_documents}

=== OUTPUT ===
Return this exact JSON format (array of objects):

Equations — every relation that must hold between entity ids:
   {{"text": "Annual salary is sum of monthly salaries", "equation": "{example_equation}", "tag": "agg"}}
   tags: scope (contract or reporting period) | agg (aggregation) | ref (cross-doc identity)

Formula language:
- ids: {id_description}
- ops: + - * / ( ) == != < <= > >= && ||
- dates: bare date IDs and start(id) mean the inclusive start ordinal; end(id)
  means the inclusive end ordinal; DD.MM.YYYY literals compare with both
- strings only in == / != comparisons

Rules:
- Write equations without evaluating them numerically. The next step will evaluate them and report results back. 
- For values in a card try to constrain them with values from supporting docs. Double-check that you didn't miss any parts in sums.
- Add a short text description for equations. It shouldn't repeat formula for requirement.
- Cross-document identity: if the same logical value appears in card and in supporting doc, relate them with tag "ref".
- For period scope, use whole-interval containment: period start <= start(id)
  and end(id) <= period end. A bare date alone checks only its start.
- Documents for other contracts or outside the reporting period must not contribute to the target row's totals.
- Do not produce checks for supporting documents. Consider them as ground-truth. Requirements should verify values from
  card based on values from supporting documents.
- Preserve supplied normalized values; do not replace unknown values with guesses.
- Output ONLY the JSON array, nothing else.

Ответ должен быть на русском языке.
"""

HIDDEN_ENTITY_VALUES_PROMPT = """\
=== HIDDEN ENTITY VALUES ===
Entity text and normalized value, start, and end attributes are intentionally
omitted from every entity tag, including tags in document labels. Empty tags
represent entities whose values are retained by the evaluator, not missing data
or zero. IDs, types, units, and normalization multipliers remain available.
Infer relationships from the surrounding text, table structure, and entity
metadata. Do not guess hidden values, match entities by assumed value,
or evaluate equations; the next step evaluates them using the retained values.
"""


def _hide_entity_values(text: str) -> str:
    """Remove entity contents and value attributes from a prompt-only copy."""

    def redact(match: re.Match) -> str:
        attrs = " ".join(
            f'{key}="{value}"'
            for key, value in ATTR_RE.findall(match.group(1))
            if key not in {"value", "start", "end"}
        )
        return f"<ent {attrs}></ent>"

    return ENT_RE.sub(redact, text)


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
        document = _render_document(
            text, entry, cfg.relation_filename_mode, f"Document {index}",
        )
        if cfg.relation_hide_entity_values:
            document = _hide_entity_values(document)
        groups[group].append(document)
    example_id = "0" if cfg.id_style == "numeric" else "crumble-wink"
    example_entity = f'<ent id="{example_id}" t="amount" value="100">raw text</ent>'
    if cfg.relation_hide_entity_values:
        example_entity = _hide_entity_values(example_entity)
    prompt = REL_PROMPT.format(
        task_documents="\n\n".join(groups["request"]) or "No separate request document supplied.",
        card_documents="\n\n".join(groups["card"]) or "No card document supplied.",
        supporting_documents="\n\n".join(groups["data"]) or "No supporting documents supplied.",
        registry="" if cfg.relation_hide_entity_values else "\n".join(_registry_lines(cfg, registry)),
        scope=json.dumps(scope, ensure_ascii=False),
        example_entity=example_entity,
        entity_values_section=HIDDEN_ENTITY_VALUES_PROMPT if cfg.relation_hide_entity_values else "",
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
        reasoning=cfg.rel_reasoning,
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
