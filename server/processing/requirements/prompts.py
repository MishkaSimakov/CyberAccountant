"""Prompts for model requirement generation."""

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
   {{"equation": "{example_equation}", "tag": "agg", "text": "short explanation"}}
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
