"""Model requirement generation with an injected client and no artifact writes."""

import json
import logging
from dataclasses import asdict

from processing.common.llm import ModelClient, ModelRequestOptions, LoggerLike
from processing.common.registry import EntityRegistry
from processing.common.requirements import FormulaDefinition, AssertionDefinition, RequirementDefinition
from processing.common.projection import project_markdown
from ..interface import RequirementGenerator, RequirementInput
from ..options import RequirementOptions
from ..prompts import REL_PROMPT
from ..schemas import RELATIONS_SCHEMA
from ..validation import validate_definition

PROMPT_ENTITY_ATTRIBUTES = ("id", "t", "u", "n", "value")


class LLMRequirementGenerator(RequirementGenerator):
    def __init__(self, client: ModelClient, options: RequirementOptions = RequirementOptions()):
        self.client, self.options = client, options

    def generate(self, source: RequirementInput, *, logger: LoggerLike) -> list[RequirementDefinition]:
        scope = asdict(source.scope)
        for name in ("period_start", "period_end"):
            scope[name] = scope[name].isoformat() if scope[name] else None
        documents = {str(doc.revision.document_id): project_markdown(doc.markdown, PROMPT_ENTITY_ATTRIBUTES)
                     for doc in source.corpus.documents}
        return generate_relations(self.options, documents, source.registry, scope, client=self.client, logger=logger)


def generate_relations(
        options: RequirementOptions,
        replaced_docs: dict[str, str],
        registry: EntityRegistry,
        scope: dict,
        *, client: ModelClient,
        logger: logging.Logger | logging.LoggerAdapter | None = None,
) -> list[RequirementDefinition]:
    """Generate relations from the supplied corpus without filtering or saving it."""
    logger = logger if logger is not None else logging.getLogger(__name__)
    documents = [f"--- file {key} ---\n{text}" for key, text in sorted(replaced_docs.items())]
    reg_lines = []
    for e in registry.entities:
        eid = e.mnemonic if options.id_style == "mnemonic" else f"#{e.num}"
        reg_lines.append(
            f"{eid}\ttype={e.type}\tvalue={e.value}\tunit={e.unit}\t"
            f"role={e.role}\tfile={e.file}"
        )
    prompt = REL_PROMPT.format(
        documents="\n\n".join(documents),
        registry="\n".join(reg_lines),
        scope=json.dumps(scope, ensure_ascii=False),
        example_id="0" if options.id_style == "numeric" else "crumble-wink",
        example_key="#0" if options.id_style == "numeric" else "crumble-wink",
        example_equation="#0 + #1 == #2" if options.id_style == "numeric" else "crumble-wink + able-fog == rapid-castle",
        id_description="# followed by a numeric ID, such as #0" if options.id_style == "numeric" else "mnemonic word pairs, such as crumble-wink",
    )
    relations = client.call_llm_json(
        prompt,
        options=ModelRequestOptions(
            model=options.rel_model,
            schema=RELATIONS_SCHEMA,
            schema_name="relations",
            timeout=options.request_timeout,
            max_retries=options.max_retries,
            reasoning=options.reasoning,
        ),
        logger=logger,
    )
    if isinstance(relations, dict):
        relations = [relations]
    definitions = []
    for raw in relations:
        if "equation" in raw:
            definition = FormulaDefinition(equation=raw["equation"], tag=raw.get("tag"),
                                           text=raw.get("text"), source="llm")
        else:
            definition = AssertionDefinition(condition={"false": False, "unverifiable": None}[raw["verdict"]],
                                             ids_related=tuple(raw["ids_related"]), text=raw["evidence"], source="llm")
        definitions.append(validate_definition(definition, registry))
    return definitions
