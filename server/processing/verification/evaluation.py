"""Direct, Decimal-based evaluation of saved requirements against stated values."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from processing.common.formula import BinOp, DateLit, FormulaError, Id, Node, Num, Str, collect_ids, evaluate, parse
from processing.common.registry import EntityRegistry
from processing.common.requirements import AssertionDefinition, RequirementDefinition
from processing.entities.values import parse_amount


@dataclass(frozen=True)
class SavedRequirement:
    id: int
    definition: RequirementDefinition


@dataclass(frozen=True)
class EvaluationResult:
    requirement_id: int
    verdict: Literal["true", "false", "unverifiable", "error"]
    ids_related: tuple[int, ...] = ()
    error: str | None = None
    error_kind: str | None = None
    files: tuple[str, ...] = ()
    touches_card: bool = False


@dataclass(frozen=True)
class _Unknown:
    reasons: tuple[str, ...]


class Evaluator:
    def __init__(self, registry: EntityRegistry):
        self.by_key = {key: entity for entity in registry.entities
                       for key in (entity.num, str(entity.num), f"#{entity.num}", entity.mnemonic)}

    def value(self, node: Id):
        entity = self.by_key[node.key]
        if node.func == "raw":
            value = parse_amount(entity.raw)
            if value is None or not value.is_finite():
                raise FormulaError(f"raw({node.key}) has no numeric raw value: {entity.raw!r}")
            return value
        if entity.value is None:
            return _Unknown((f"entity {node.key!r} has no recorded value (raw={entity.raw!r})",))
        if entity.type in ("amount", "number", "pct", "ratio", "date"):
            try:
                value = Decimal(str(entity.value))
            except InvalidOperation:
                if entity.type == "date":
                    raise FormulaError(f"invalid date value for {node.key!r}: {entity.value!r}")
            else:
                if not value.is_finite():
                    raise FormulaError(f"non-finite value for {node.key!r}: {entity.value!r}")
                return value
        return str(entity.value)

    def validate(self, node: Node) -> str:
        """Reject invalid references/types even when some recorded inputs are missing."""
        if isinstance(node, (Num, DateLit)):
            return "number"
        if isinstance(node, Str):
            return "string"
        if isinstance(node, Id):
            if node.key not in self.by_key:
                raise FormulaError(f"unknown entity id {node.key!r}")
            value = self.value(node)
            if isinstance(value, _Unknown):
                return "number" if self.by_key[node.key].type in (
                    "amount", "number", "pct", "ratio", "date") else "string"
            return "number" if isinstance(value, Decimal) else "string"
        if not isinstance(node, BinOp):
            raise FormulaError(f"unknown node {node!r}")
        left, right = self.validate(node.left), self.validate(node.right)
        if node.op in ("&&", "||"):
            if left != "bool" or right != "bool":
                raise FormulaError(f"expected boolean operands to {node.op!r}")
            return "bool"
        if node.op in ("==", "!="):
            if left != right:
                raise FormulaError(f"type clash in {node.op!r}: {left} vs {right}")
            return "bool"
        if left != "number" or right != "number":
            raise FormulaError(f"non-numeric operands to {node.op!r}")
        if node.op in ("<", "<=", ">", ">="):
            return "bool"
        if node.op in ("+", "-", "*", "/"):
            return "number"
        raise FormulaError(f"unknown operator {node.op!r}")

    def expression(self, node: Node):
        if isinstance(node, (Num, Str)):
            return node.value
        if isinstance(node, DateLit):
            return Decimal(node.value.toordinal())
        if isinstance(node, Id):
            return self.value(node)
        if not isinstance(node, BinOp):
            raise FormulaError(f"unknown node {node!r}")
        left = self.expression(node.left)
        if node.op in ("&&", "||"):
            if not isinstance(left, (bool, _Unknown)):
                raise FormulaError(f"expected boolean, got {left!r}")
            decisive = node.op == "||"
            if left is decisive:
                return decisive
            right = self.expression(node.right)
            if not isinstance(right, (bool, _Unknown)):
                raise FormulaError(f"expected boolean, got {right!r}")
            if right is decisive:
                return decisive
            if isinstance(left, _Unknown) or isinstance(right, _Unknown):
                return _Unknown(tuple(reason for value in (left, right) if isinstance(value, _Unknown)
                                      for reason in value.reasons))
            return right
        right = self.expression(node.right)
        if isinstance(left, _Unknown) or isinstance(right, _Unknown):
            return _Unknown(tuple(reason for value in (left, right) if isinstance(value, _Unknown)
                                  for reason in value.reasons))
        # Reuse the shared language's strict concrete type rules, with no symbolic leaves.
        return evaluate(BinOp(node.op, Id(0), Id(1)), {0: left, 1: right})

    def requirement(self, saved: SavedRequirement) -> EvaluationResult:
        definition = saved.definition
        ids, entities = set(), []
        try:
            ast = None
            if isinstance(definition, AssertionDefinition):
                ids = set(definition.ids_related)
            else:
                ast = parse(definition.equation)
                ids = collect_ids(ast)
            entities = [self.by_key[key] for key in ids if key in self.by_key]
            unknown = ids - self.by_key.keys()
            if unknown:
                raise FormulaError(f"unknown entity ids: {sorted(unknown, key=str)}")
            if isinstance(definition, AssertionDefinition):
                value = definition.condition
                if value is None:
                    value = _Unknown(("Assertion is explicitly unresolved",))
            else:
                if self.validate(ast) != "bool":
                    raise FormulaError("An equation must produce a boolean value")
                value = self.expression(ast)
            if isinstance(value, _Unknown):
                verdict, error, kind = "unverifiable", "; ".join(value.reasons), (
                    "assertion_unknown" if isinstance(definition, AssertionDefinition) else "missing_value")
            elif isinstance(value, bool):
                verdict, error, kind = ("true", None, None) if value else (
                    "false", "requirement is false for the recorded values", None)
            else:
                raise FormulaError("An equation must produce a boolean value")
        except (FormulaError, ArithmeticError, ValueError, TypeError, RecursionError) as exc:
            verdict, error = "error", (f"{exc} (equation: {getattr(definition, 'equation', 'assertion')}; "
                                       f"values: {[(entity.num, entity.raw) for entity in entities]})")
            kind = "arithmetic" if isinstance(exc, ArithmeticError) else "invalid"
        return EvaluationResult(saved.id, verdict, tuple(sorted({entity.num for entity in entities})),
                                error, kind, tuple(sorted({entity.file for entity in entities})),
                                any(entity.role == "card" for entity in entities))


def evaluate_requirements(requirements: tuple[SavedRequirement, ...] | list[SavedRequirement],
                          registry: EntityRegistry) -> dict[int, EvaluationResult]:
    if len({saved.id for saved in requirements}) != len(requirements):
        raise ValueError("Requirement identities must be unique")
    evaluator = Evaluator(registry)
    return {saved.id: evaluator.requirement(saved) for saved in requirements}
