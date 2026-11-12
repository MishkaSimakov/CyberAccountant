"""Prepare one corpus, collect generator outputs, and evaluate them together."""

import json
from dataclasses import dataclass

from checkier import PipelineConfig
from checkier.entities import EntityRegistry, build_registry
from checkier.formula import collect_ids
from checkier.solver import solve_system
from checkier.stage3_replace import run_replace
from checkier.stage4_scope import run_scope
from checkier.stage5_relations import generate_relations

from . import storage
from .models import File, Report
from .models.requirement import RequirementVerdict


@dataclass
class VerificationContext:
    config: PipelineConfig
    documents: dict[str, str]
    registry: EntityRegistry
    scope: dict


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
        mapping[key] = {"role": role, "original": file.name}
        (cfg.tagged_dir / f"{key}.md").write_text(text, encoding="utf-8")
    registry = build_registry(tagged, seed=cfg.seed)
    registry.save(cfg.entities_path)
    period = [report.period_start.toordinal(), report.period_end.toordinal()] if report.period_start else None
    scope = run_scope(cfg, registry, mapping, target_contract=report.contract_number,
                      target_period=period, target_label=report.target_row)
    if report.target_row and not scope["target_row"]:
        raise ValueError("Не удалось найти проверяемую строку в итоговом отчёте.")
    documents = run_replace(cfg, tagged, registry)
    return VerificationContext(cfg, documents, registry, scope)


def generate_requirements(context: VerificationContext) -> list[dict]:
    """Every generator receives the same prepared documents, IDs, and scope."""
    requirements = list(context.scope["scope_relations"])
    for name, generate in GENERATORS.items():
        requirements.extend(dict(requirement, source=name) for requirement in generate(context))
    context.config.relations_path.write_text(json.dumps(requirements, ensure_ascii=False), encoding="utf-8")
    return requirements


def evaluate_requirements(context: VerificationContext, requirements: list[dict]) -> list[dict]:
    """Make exactly one solver call with the complete collection of requirements."""
    relations, findings, verdicts, _dangling = solve_system(
        requirements, context.registry, variable_keys=context.scope["target_row"],
    )
    by_key = {key: entity.num for entity in context.registry.entities
              for key in (entity.num, str(entity.num), f"#{entity.num}", entity.mnemonic)}
    results = []
    for relation in relations:
        ids = collect_ids(relation.ast) if relation.ast is not None else []
        results.append(dict(
            verdict=RequirementVerdict(relation.status), equation=relation.equation,
            source=relation.source, tag=relation.tag, error=relation.error,
            ids_related=sorted({by_key[key] for key in ids if key in by_key}),
        ))
    raw_findings = [requirement for requirement in requirements if "equation" not in requirement]
    for raw, finding in zip(raw_findings, findings):
        unknown = [key for key in finding.ids_related if key not in by_key]
        results.append(dict(
            verdict=RequirementVerdict.ERROR if unknown else RequirementVerdict(finding.verdict),
            evidence=finding.evidence, source=raw["source"],
            error=f"Unknown entity IDs: {unknown}" if unknown else None,
            ids_related=sorted({by_key[key] for key in finding.ids_related if key in by_key}),
        ))
    for verdict in verdicts:
        if not verdict.target:
            continue
        status = {"ok": RequirementVerdict.TRUE, "mismatch": RequirementVerdict.FALSE,
                  "unverified": RequirementVerdict.UNVERIFIABLE}[verdict.status]
        evidence = (f"Значение #{verdict.num}: указано {verdict.stated}, ожидается {verdict.expected}."
                    if verdict.expected is not None else f"Недостаточно данных для проверки значения #{verdict.num}.")
        results.append(dict(verdict=status, evidence=evidence, source="evaluator",
                            ids_related=[verdict.num], stated=verdict.stated,
                            expected=verdict.expected, difference=verdict.diff))
    return results
