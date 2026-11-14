"""Legacy pipeline adapter for independent evaluation and expected-value solving."""

from processing.common.requirements import FormulaDefinition
from processing.verification.constraints import Fishy, Relation, Verdict
from processing.verification.evaluation import SavedRequirement, evaluate_requirements
from processing.verification.solver import Solver


def solve_system(relations_raw, registry, variable_keys=None):
    relations, fishy = [], []
    for raw in relations_raw:
        if "equation" in raw:
            relations.append(Relation(len(relations), raw["equation"], raw.get("tag"), raw.get("source", "llm")))
        else:
            fishy.append(Fishy(len(fishy), raw["evidence"], list(raw.get("ids_related", [])),
                               raw.get("verdict", "false")))
    solver = Solver(relations, registry, variable_keys)
    solver.solve()
    evaluations = evaluate_requirements([
        SavedRequirement(rel.index, FormulaDefinition(equation=rel.equation)) for rel in relations], registry)
    for rel in relations:
        direct = evaluations[rel.index]
        rel.status, rel.error = direct.verdict, direct.error
        rel.involved_files, rel.touches_card = list(direct.files), direct.touches_card
    return relations, fishy, solver.verdicts(), solver.dangling_files()
