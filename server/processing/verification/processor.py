"""Independent direct checks and expected-value inference over one canonical snapshot."""

from dataclasses import asdict, replace

from processing.common.requirements import AssertionDefinition
from .constraints import Fishy, Relation
from .evaluation import evaluate_requirements
from .interface import VerificationInput, VerificationOutput, VerificationProcessor
from .results import build_report
from .solver import Solver
from .target_row import find_target_row_keys


class Verifier(VerificationProcessor):
    def __init__(self, *, timeout_ms: int = 20_000):
        self.timeout_ms = timeout_ms

    def process(self, source: VerificationInput, *, logger) -> VerificationOutput:
        registry = source.registry
        scope = source.scope
        tagged = {str(doc.revision.document_id): (
            "card" if doc.role == "final_report" else "data", doc.markdown) for doc in source.corpus.documents}
        if scope.target_row is None and scope.target_label:
            target = find_target_row_keys(tagged, registry, scope.target_label)
            if not target:
                raise ValueError("Не удалось найти проверяемую строку в итоговом отчёте.")
            scope = replace(scope, target_row=tuple(sorted(target)))
        if scope.target_row is not None:
            card_ids = {entity.num for entity in registry.entities if entity.role == "card"}
            if set(scope.target_row) - card_ids:
                raise ValueError("Target row references fields outside the final report")
        logger.info("Evaluating %d saved requirements", len(source.requirements))
        evaluations = evaluate_requirements(source.requirements, registry)
        relations, fishy, formula_ids = [], [], []
        for saved in source.requirements:
            definition = saved.definition
            if isinstance(definition, AssertionDefinition):
                fishy.append(Fishy(len(fishy), definition.text or "", list(evaluations[saved.id].ids_related),
                                   evaluations[saved.id].verdict))
            else:
                relations.append(Relation(len(relations), definition.equation, definition.tag, definition.source,
                                          requirement_id=saved.id))
                formula_ids.append(saved.id)
        solver = Solver(relations, registry, scope.target_row, timeout_ms=self.timeout_ms)
        solver.solve()
        diagnostics = tuple(dict(item, requirement_id=identity)
                            for identity, item in zip(formula_ids, solver.diagnostics, strict=True))
        mapping = {str(doc.revision.document_id): dict(
            role="card" if doc.role == "final_report" else "data", original=doc.name,
            version=doc.revision.version) for doc in source.corpus.documents}
        used = {file for direct in evaluations.values() for file in direct.files}
        dangling = sorted(mapping.keys() - used)
        verdicts = solver.verdicts()
        report = build_report(mapping, registry, relations, fishy, verdicts, dangling,
                              dict(target_row=list(scope.target_row) if scope.target_row is not None else None,
                                   target_label=scope.target_label), evaluations=evaluations)
        expected = {verdict.num: verdict.expected for verdict in verdicts if verdict.expected is not None}
        report.update(entity_documents={entity.num: int(entity.file) for entity in registry.entities},
                      entities=registry.to_dict()["entities"], documents=mapping,
                      scope=dict(target_row=scope.target_row, target_label=scope.target_label),
                      revisions=[asdict(revision) for revision in source.corpus.revisions],
                      expected_values=expected, uniqueness=solver.uniqueness, solver_diagnostics=list(diagnostics))
        logger.info("Verification completed: %d direct verdicts, %d expected values", len(evaluations), len(expected))
        return VerificationOutput(evaluations, expected, solver.uniqueness, report["files"], diagnostics, report)
