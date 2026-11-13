"""Generate requirements with the LLM using the shared verification context."""

import logging

from processing.common.llm import ModelClient
from checkier.stage5_relations import generate_relations

from ..verification_context import VerificationContext


def generate_llm(context: VerificationContext, *, client: ModelClient,
                 logger: logging.Logger | logging.LoggerAdapter | None = None) -> list[dict]:
    report = context.report
    settings = dict(context.scope, contract_number=report.contract_number,
                    period_start=report.period_start.isoformat() if report.period_start else None,
                    period_end=report.period_end.isoformat() if report.period_end else None)
    return generate_relations(context.config, context.documents, context.registry, settings, client=client,
                              logger=logger)
