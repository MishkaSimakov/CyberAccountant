"""Stage 5: LLM relation extraction over the whole corpus.

Stage 5b: equation repair. After solving, relations with ``status == "error"``
(parse failures, unknown ids, type clashes — e.g. ``mono-geba == 3/1`` where
the docid '3/1' must be a quoted string, or ``maniqyxo`` where the model
dropped the hyphen from ``mani-qyxo``) are fed back to the model with their
error messages; the corrected equations are re-solved. Bounded by
``cfg.max_retries`` repair rounds via :func:`solve_with_repairs`.
"""

from __future__ import annotations

import json
import re

from checkier.config import PipelineConfig
from checkier.entities import EntityRegistry
from checkier.llm import call_llm_json
from checkier.schemas import RELATIONS_SCHEMA, RELATION_REPAIR_SCHEMA
from checkier.solver import solve_system

#: placeholder substituted for values of entity types in cfg.obfuscate_types
HIDDEN = "<hidden>"

#: an entity reference in a replaced doc: <e id="mnemonic-or-num">raw</e>
_E_REF_RE = re.compile(r'<e id="([^"]+)">.*?</e>', re.DOTALL)

REL_PROMPT = """\
You are an auditor building a system of equations that pins down every value
in the summary report (the "card") using the primary documents ("data").

=== TASK ===
Write relations in this exact JSON format (array of objects):

1. Equations — every relation that must hold between entity ids:
   {{"equation": "crumble-wink + able-fog == rapid-castle", "tag": "agg"}}
   tags: contract | period | agg (aggregation) | ref (cross-doc identity)

2. Fishy data — when data looks wrong but can't be expressed as an equation:
   {{"evidence": "one short sentence describing the problem", "ids_related": ["id1"], "verdict": "false"}}

Formula language:
- ids: copy ids EXACTLY as listed in the registry. {ids_description}
- ops: + - * / ( ) == != < <= > >= && ||
- functions: norm(id) — value normalized to base units (same as bare id); raw(id) — unnormalized
- entity types and their literals — an id may only be compared with a literal
  of its own type:
  - docid | contract — strings: ONLY == / != against a QUOTED string literal,
    e.g. mono-geba == "3/1". Writing mono-geba == 3/1 is a type clash.
  - amount | number | pct | ratio — numeric: bare numbers, arithmetic allowed
  - date — DD.MM.YYYY literals compare with date ids
- dimensionality: NEVER add or subtract values of different dimensions —
  different currencies/units (RUB vs USD), amounts vs plain numbers or
  percentages, dates vs anything. Such equations are rejected.

Rules:
- PREFER ids over literal numbers: write "a + b == c", not "a + 1000 == c".
- Values of some entity types are replaced with {hidden} on purpose. You cannot
  see or compute the actual numbers — do NOT put numeric conclusions, computed
  sums or differences in "evidence" text. Express every relation you suspect
  as an equation between ids; the solver evaluates it against the real values.
- Our target is to make all possible formulas, so we can pin all relevant (respective to request) fields in card exactly, using only data from other documents.
  I.e. we will need to be able to solve F(constants, x) = x, where x is vector of those relevant fields, and F(constants,x) = x is set of all equations/inequations that we built.
- Cover ALL aggregations: card totals == sum of their components (from both card and data).
- Cross-document identity: if the same logical value appears in two docs, relate them with tag "ref".
- Output ONLY the JSON array, nothing else.

Below are the primary documents. Entity references look like <e id="id of entry">raw text</e>.
The registry lists every entity: id, type, normalized value, unit, role
(data = truth / constant, card = variable to verify, request = scope definition).

=== DOCUMENTS ===
{documents}

=== ENTITY REGISTRY ===
{registry}

"""


def _registry_lines(
    cfg: PipelineConfig, registry: EntityRegistry, in_scope: set
) -> str:
    """Registry lines for card entities and entities of in-scope files."""
    lines = []
    for e in registry.entities:
        if e.role == "card" or e.file in in_scope:
            eid = e.mnemonic if cfg.id_style == "mnemonic" else f"#{e.num}"
            value = HIDDEN if e.type in cfg.obfuscate_types else e.value
            lines.append(
                f"{eid}\ttype={e.type}\tvalue={value}\tunit={e.unit}\t"
                f"role={e.role}\tfile={e.file}"
            )
    return "\n".join(lines)


def _obfuscate_docs(
    cfg: PipelineConfig,
    replaced_docs: dict[str, str],
    registry: EntityRegistry,
) -> dict[str, str]:
    """Replace <e id>raw</e> inner text with <hidden> for obfuscated types.

    Prompt-only masking: the returned dict is what gets sent to the
    relation-extraction LLM; on-disk artifacts keep real values. With no
    configured types the input is returned unchanged.
    """
    if not cfg.obfuscate_types:
        return replaced_docs
    hidden = {
        e.mnemonic if cfg.id_style == "mnemonic" else str(e.num)
        for e in registry.entities
        if e.type in cfg.obfuscate_types
    }

    def repl(m: re.Match) -> str:
        eid = m.group(1)
        if eid not in hidden:
            return m.group(0)
        return f'<e id="{eid}">{HIDDEN}</e>'

    return {h8: _E_REF_RE.sub(repl, text) for h8, text in replaced_docs.items()}


def run_relations(
    cfg: PipelineConfig,
    replaced_docs: dict[str, str],
    registry: EntityRegistry,
    scope: dict,
) -> list[dict]:
    """Ask the LLM for all relations; combine with deterministic scope relations."""
    in_scope = set(scope["in_scope"])
    documents = []
    prompt_docs = _obfuscate_docs(cfg, replaced_docs, registry)
    for h8, text in sorted(prompt_docs.items()):
        if h8 in in_scope or any(
            e.file == h8 and e.role == "card" for e in registry.entities
        ):
            documents.append(f"--- file {h8} ---\n{text}")
    prompt = REL_PROMPT.format(
        hidden=HIDDEN,
        ids_description=(" Mnemonic ids are two lowercase words joined by one hyphen (\"mani-qyxo\"); the hyphen is part of the id — \"maniqyxo\" is INVALID." if cfg.id_style=="mnemonic" else "Ids are numbers, predicated with #, example: #123. Treat all examples with mnemonic-based ids, as ones with numeric ids."),
        documents="\n\n".join(documents),
        registry=_registry_lines(cfg, registry, in_scope),
    )
    relations = call_llm_json(
        prompt,
        model=cfg.rel_model,
        schema=RELATIONS_SCHEMA,
        schema_name="relations",
        timeout=cfg.request_timeout,
        max_retries=cfg.max_retries,
        reasoning="enabled"
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


# ---------------------------------------------------------------------------
# Stage 5b: equation repair — feed solver errors back to the model
# ---------------------------------------------------------------------------

REPAIR_PROMPT = """\
You are an auditor building a system of equations that pins down every value
in the summary report (the "card") using the primary documents ("data").

Some equations you wrote earlier failed to parse or type-check. Fix each one.

=== ENTITY REGISTRY ===
%%REGISTRY%%

=== FORMULA LANGUAGE ===
- ids: copy ids EXACTLY as listed in the registry. Mnemonic ids are two
  lowercase words joined by one hyphen ("mani-qyxo"); the hyphen is part of
  the id — "maniqyxo" is INVALID. (With numeric id style, ids are #N.)
- ops: + - * / ( ) == != < <= > >= && ||
- functions: norm(id), raw(id)
- entity types and their literals — an id may only be compared with a literal
  of its own type:
  - docid | contract — strings: ONLY == / != against a QUOTED string literal,
    e.g. mono-geba == "3/1". Writing mono-geba == 3/1 is a type clash.
  - amount | number | pct | ratio — numeric: bare numbers, arithmetic allowed
  - date — DD.MM.YYYY literals compare with date ids
- dimensionality: NEVER add or subtract values of different dimensions —
  different currencies/units (RUB vs USD), amounts vs plain numbers or
  percentages, dates vs anything. Such equations are rejected. (RUB_K amounts
  are normalized to base RUB, so RUB and RUB_K CAN be mixed.)

=== BROKEN EQUATIONS ===
%%BROKEN%%

TASK
Return a JSON array with ONE entry per broken equation:
- {"index": N, "equation": "the corrected equation"} — or
- {"index": N, "drop": true} if the equation does not correspond to any real
  relation in the documents and should be removed.

Return ONLY the JSON array.
"""


def repair_relations(
    cfg: PipelineConfig,
    broken: list[tuple[int, str | None, str, str]],
    reg_text: str,
    on_token=None,
) -> dict[int, str | None]:
    """Ask the model to fix erroring equations.

    *broken*: (relation_index, tag, equation, error) tuples.
    Returns {relation_index: corrected equation}, or None values for
    equations the model says to drop.
    """
    items = "\n".join(
        f"#{idx} [{tag or ''}] {eq}\n  error: {err}"
        for idx, tag, eq, err in broken
    )
    prompt = REPAIR_PROMPT.replace("%%REGISTRY%%", reg_text).replace(
        "%%BROKEN%%", items
    )
    data = call_llm_json(
        prompt,
        model=cfg.rel_model,
        schema=RELATION_REPAIR_SCHEMA,
        schema_name="relation_repairs",
        timeout=cfg.request_timeout,
        max_retries=cfg.max_retries,
        on_token=on_token,
        reasoning=cfg.reasoning,
    )
    entries = data if isinstance(data, list) else data.get("repairs", [])
    fixes: dict[int, str | None] = {}
    for item in entries:
        if not isinstance(item, dict) or "index" not in item:
            continue
        if item.get("drop"):
            fixes[int(item["index"])] = None
        elif item.get("equation"):
            fixes[int(item["index"])] = str(item["equation"])
    return fixes


def solve_with_repairs(
    cfg: PipelineConfig,
    relations_raw: list[dict],
    registry: EntityRegistry,
    scope: dict,
    variable_keys: set | list | None = None,
    on_token=None,
):
    """Solve the system; feed erroring equations back to the model and
    re-solve — at most ``cfg.max_retries`` repair rounds.

    Mutates *relations_raw* in place with the corrected equations and
    rewrites relations.json when anything changed. Returns the same
    (relations, fishy, verdicts, dangling) tuple as ``solve_system``.
    """
    in_scope = set(scope.get("in_scope") or [])
    rels, fishy, verdicts, dangling = [], [], [], []
    changed = False
    for attempt in range(cfg.max_retries + 1):
        rels, fishy, verdicts, dangling = solve_system(
            relations_raw, registry, variable_keys=variable_keys
        )
        broken = [
            (r.index, r.tag, r.equation, r.error or "")
            for r in rels
            if r.status == "error" and r.source == "llm"
        ]
        if not broken or attempt == cfg.max_retries:
            break
        fixes = repair_relations(
            cfg, broken, _registry_lines(cfg, registry, in_scope),
            on_token=on_token,
        )
        if not fixes:
            break
        # Relation.index counts equation-relations only; map back to
        # relations_raw positions (fishy entries are interleaved).
        eq_pos = [i for i, r in enumerate(relations_raw) if "equation" in r]
        drops: set[int] = set()
        applied = False
        for rel_idx, new_eq in fixes.items():
            if rel_idx >= len(eq_pos):
                continue
            raw_idx = eq_pos[rel_idx]
            if new_eq is None:
                drops.add(raw_idx)
                applied = True
            elif new_eq != relations_raw[raw_idx]["equation"]:
                relations_raw[raw_idx]["equation"] = new_eq
                applied = True
        if drops:
            relations_raw[:] = [
                r for i, r in enumerate(relations_raw) if i not in drops
            ]
        if not applied:
            break
        changed = True
    if changed:
        cfg.relations_path.write_text(
            json.dumps(relations_raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return rels, fishy, verdicts, dangling
