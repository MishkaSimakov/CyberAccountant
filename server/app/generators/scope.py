"""Generate contract and period requirements without evaluating them."""

import logging

import json

from ..verification_context import VerificationContext


def generate_scope(context: VerificationContext, *,
                   logger: logging.Logger | logging.LoggerAdapter | None = None) -> list[dict]:
    """Check each supporting document against the report's contract and period."""
    report = context.report
    requirements = []
    for file, entry in sorted(context.mapping.items()):
        if entry["role"] != "data" or entry.get("duplicate_of"):
            continue
        for entity in context.registry.entities:
            if entity.file != file:
                continue
            key = entity.mnemonic if context.config.id_style == "mnemonic" else f"#{entity.num}"
            if entity.type == "contract" and report.contract_number:
                equation = f"{key} == {json.dumps(report.contract_number.strip(), ensure_ascii=False)}"
            elif (entity.type == "date" and entity.loc == "body" and entity.value is not None
                  and report.period_start and report.period_end):
                equation = f"{report.period_start:%d.%m.%Y} <= {key} && {key} <= {report.period_end:%d.%m.%Y}"
            else:
                continue
            requirements.append({"equation": equation, "tag": "scope"})
    return requirements
