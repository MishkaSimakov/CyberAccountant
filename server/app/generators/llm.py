"""Generate requirements with the LLM using the shared verification context."""

import logging

from processing.common.llm import ModelClient
from processing.entities.projection import project_markdown
from checkier.stage5_relations import generate_relations

from ..verification_context import VerificationContext

PROMPT_ENTITY_ATTRIBUTES = ("id", "t", "u", "n", "value")


def generate_llm(context: VerificationContext, *, client: ModelClient,
                 logger: logging.Logger | logging.LoggerAdapter | None = None) -> list[dict]:
    report = context.report
    settings = dict(context.scope, contract_number=report.contract_number,
                    period_start=report.period_start.isoformat() if report.period_start else None,
                    period_end=report.period_end.isoformat() if report.period_end else None)
    documents = {key: project_markdown(markdown, PROMPT_ENTITY_ATTRIBUTES)
                 for key, markdown in context.documents.items()}
    return generate_relations(context.config, documents, context.registry, settings, client=client,
                              logger=logger)
