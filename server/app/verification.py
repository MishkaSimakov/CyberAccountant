"""Prepare one corpus, collect generator outputs, and evaluate them together."""

import json
from dataclasses import dataclass

from checkier import PipelineConfig
from checkier.entities import EntityRegistry, build_registry
from checkier.formula import collect_ids
from checkier.report import build_report
from checkier.solver import solve_system
from checkier.stage3_replace import run_replace
from checkier.stage4_scope import run_scope
from checkier.stage5_relations import generate_relations

from . import storage
from .models import File, Report


@dataclass
class VerificationContext:
    config: PipelineConfig
    documents: dict[str, str]
    registry: EntityRegistry
    scope: dict
    mapping: dict


def generate_llm(context: VerificationContext) -> list[dict]:
    return generate_relations(context.config, context.documents, context.registry, context.scope)


GENERATORS = {"llm": generate_llm}


def prepare_verification(report: Report, files: list[File]) -> VerificationContext:
    directory = storage.artifact_path(f"{report.id}/verification")
    cfg = PipelineConfig(data_dir=directory, out_dir=directory, id_style="numeric")
    cfg.tagged_dir.mkdir(parents=True, exist_ok=True)
    tagged = {}
    mapping = {}
    for file in files:
        key = str(file.file_id)
        role = "card" if file.role == "final_report" else "data"
        text = storage.artifact_path(file.entities_path).read_text(encoding="utf-8")
        tagged[key] = (role, text)
        mapping[key] = {"role": role, "original": file.name, "version": file.version}
        (cfg.tagged_dir / f"{key}.md").write_text(text, encoding="utf-8")
    registry = build_registry(tagged, seed=cfg.seed)
    registry.save(cfg.entities_path)
    period = [report.period_start.toordinal(), report.period_end.toordinal()] if report.period_start else None
    scope = run_scope(cfg, registry, mapping, target_contract=report.contract_number,
                      target_period=period, target_label=report.target_row)
    if report.target_row and not scope["target_row"]:
        raise ValueError("Не удалось найти проверяемую строку в итоговом отчёте.")
    documents = run_replace(cfg, tagged, registry)
    return VerificationContext(cfg, documents, registry, scope, mapping)


def generate_requirements(context: VerificationContext) -> list[dict]:
    """Every generator receives the same prepared documents, IDs, and scope."""
    generated = list(context.scope["scope_relations"])
    for name, generate in GENERATORS.items():
        generated.extend(dict(requirement, source=name) for requirement in generate(context))
    by_key = {key: entity.num for entity in context.registry.entities
              for key in (entity.num, str(entity.num), f"#{entity.num}", entity.mnemonic)}
    requirements = []
    for raw in generated:
        if "equation" in raw:
            definition = dict(kind="formula", equation=raw["equation"], tag=raw.get("tag"), text=raw.get("text"))
        else:
            condition = (raw["condition"] if raw.get("kind") == "assertion"
                         else {"true": True, "false": False, "unverifiable": None}[raw["verdict"]])
            definition = dict(kind="assertion", condition=condition, text=raw.get("text", raw.get("evidence")),
                              ids_related=[by_key.get(key, key) for key in raw["ids_related"]])
        requirements.append(dict(definition, source=raw["source"]))
    context.config.relations_path.write_text(json.dumps(requirements, ensure_ascii=False), encoding="utf-8")
    return requirements


def evaluate_requirements(context: VerificationContext, requirements: list[dict]) -> dict:
    """Evaluate saved definitions once and keep requirement and field outcomes separate."""
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
    return result
