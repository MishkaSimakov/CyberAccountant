"""Prepare canonical inputs and evaluate saved requirement definitions."""

import logging

from checkier import PipelineConfig
from processing.common.documents import DocumentRevision
from processing.common.registry import EntityRegistry
from processing.entities.corpus import build_registry
from processing.common.formula import collect_ids
from checkier.report import build_report
from checkier.solver import solve_system

from . import storage
from .models import File, Report
from .target_row import find_target_row_keys
from .verification_context import VerificationContext


def validation_registry(files: list[File]) -> EntityRegistry:
    return build_registry({str(file.file_id): (
        "card" if file.role == "final_report" else "data",
        storage.artifact_path(file.entities_path).read_text(encoding="utf-8"),
    ) for file in files}, revisions={str(file.file_id): DocumentRevision(file.report_id, file.file_id, file.version)
                                     for file in files})


def prepare_verification(report: Report, files: list[File]) -> VerificationContext:
    directory = storage.artifact_path(f"{report.id}/verification")
    cfg = PipelineConfig(data_dir=directory, out_dir=directory, id_style="numeric")
    tagged = {}
    mapping = {}
    for file in files:
        key = str(file.file_id)
        role = "card" if file.role == "final_report" else "data"
        text = storage.artifact_path(file.entities_path).read_text(encoding="utf-8")
        tagged[key] = (role, text)
        mapping[key] = {"role": role, "original": file.name, "version": file.version}
    registry = build_registry(tagged, revisions={str(file.file_id): DocumentRevision(
        file.report_id, file.file_id, file.version) for file in files})
    target_row = find_target_row_keys(tagged, registry, report.target_row)
    if report.target_row and not target_row:
        raise ValueError("Не удалось найти проверяемую строку в итоговом отчёте.")
    scope = {"target_row": sorted(target_row) if target_row else None, "target_label": report.target_row}
    documents = {key: text for key, (_role, text) in tagged.items()}
    return VerificationContext(cfg, documents, registry, scope, mapping, report)


def evaluate_requirements(context: VerificationContext, requirements: list[dict], *,
                          logger: logging.Logger | logging.LoggerAdapter | None = None) -> dict:
    """Evaluate saved definitions once and keep requirement and field outcomes separate."""
    logger = logger if logger is not None else logging.getLogger(__name__)
    logger.info("Evaluating %d requirements", len(requirements))
    inputs = []
    for requirement in requirements:
        if requirement["kind"] == "formula":
            inputs.append(dict(equation=requirement["equation"], tag=requirement.get("tag"),
                               source=requirement["source"]))
        else:
            condition = requirement["condition"]
            inputs.append(dict(evidence=requirement["text"] or "", ids_related=requirement["ids_related"],
                               verdict="unverifiable" if condition is None else "true" if condition else "false"))
    relations, findings, verdicts, dangling = solve_system(
        inputs, context.registry, variable_keys=context.scope["target_row"],
    )
    by_key = {key: entity.num for entity in context.registry.entities
              for key in (entity.num, str(entity.num), f"#{entity.num}", entity.mnemonic)}
    results = []
    relation_iter, finding_iter = iter(relations), iter(findings)
    for requirement in requirements:
        if requirement["kind"] == "formula":
            relation = next(relation_iter)
            ids = collect_ids(relation.ast) if relation.ast is not None else []
            status, error = relation.status, relation.error
        else:
            finding = next(finding_iter)
            ids = finding.ids_related
            unknown = [key for key in ids if key not in by_key]
            status = "error" if unknown else finding.verdict
            error = f"Unknown entity IDs: {unknown}" if unknown else None
        results.append(dict(requirement_id=requirement["id"], verdict=status, error=error,
                            ids_related=sorted({by_key[key] for key in ids if key in by_key})))

    result = build_report(context.config, context.mapping, context.registry,
                          relations, findings, verdicts, dangling, context.scope)
    result["requirement_results"] = results
    result["entity_documents"] = {entity.num: int(entity.file) for entity in context.registry.entities}
    result["entities"] = context.registry.to_dict()["entities"]
    result["documents"] = context.mapping
    result["scope"] = context.scope
    by_num = {entity.num: entity for entity in context.registry.entities}
    for cell in result["card"]["cells"] + result["constants"]:
        cell["unit"] = by_num[cell["num"]].unit
    logger.info("Verification completed: %d requirement results", len(results))
    return result
