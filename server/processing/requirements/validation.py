"""Formula and reference checks shared by generation and application editing."""

from dataclasses import replace

from processing.common.errors import ProcessingValidationError
from processing.common.formula import FormulaError, collect_ids, parse, is_boolean_expression
from processing.common.registry import EntityRegistry
from processing.common.requirements import FormulaDefinition, RequirementDefinition


def validate_formula(equation: str, registry: EntityRegistry) -> str | None:
    """Check syntax and references without running the solver or any LLM calls."""

    try:
        ast = parse(equation)
        if not is_boolean_expression(ast):
            return "Формула должна содержать сравнение, например #0 == #1 + #2."
        known = {key for entity in registry.entities for key in (entity.num, entity.mnemonic)}
        unknown = collect_ids(ast) - known
        if unknown:
            return "Неизвестные сущности: " + ", ".join(f"#{key}" if isinstance(key, int) else key
                                                        for key in sorted(unknown, key=str))
    except (FormulaError, ValueError, RecursionError) as exc:
        return str(exc)
    return None


def validate_definition(definition: RequirementDefinition, registry: EntityRegistry) -> RequirementDefinition:
    if isinstance(definition, FormulaDefinition):
        error = validate_formula(definition.equation, registry)
        if error:
            raise ProcessingValidationError(error)
        return definition
    by_key = {key: entity.num for entity in registry.entities
              for key in (entity.num, str(entity.num), f"#{entity.num}", entity.mnemonic)}
    unknown = [key for key in definition.ids_related if key not in by_key]
    if unknown:
        raise ProcessingValidationError("Неизвестные сущности: " + ", ".join(map(str, unknown)))
    return replace(definition, ids_related=tuple(by_key[key] for key in definition.ids_related))
