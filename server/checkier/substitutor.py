"""Check requirements using recorded entity values, without inferring values."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from processing.common.registry import EntityRegistry
from processing.entities.values import parse_amount
from processing.common.formula import (
    BinOp, FormulaError, Id, Node, Num, Str, collect_ids, parse,
)

if TYPE_CHECKING:
    from checkier.solver import Relation


class MissingValue(FormulaError):
    """An entity exists, but has no recorded value to substitute."""


class Substitutor:
    def __init__(self, registry: EntityRegistry) -> None:
        self.by_key = {key: entity for entity in registry.entities
                       for key in (entity.num, entity.mnemonic)}

    def substitute(self, node: Node) -> Node:
        """Return a new AST whose references are replaced by recorded literals.

        Bare IDs and norm() use normalized values; raw() parses the original
        numeric text without applying its unit multiplier.
        """
        if isinstance(node, Id):
            entity = self.by_key.get(node.key)
            if entity is None:
                raise FormulaError(f"unknown entity id {node.key!r}")
            if node.func == "raw":
                value = parse_amount(entity.raw)
                if value is None or not value.is_finite():
                    raise FormulaError(f"raw({node.key}) has no numeric raw value: {entity.raw!r}")
                return Num(value)
            if entity.value is None:
                raise MissingValue(f"entity {node.key!r} has no recorded value (raw={entity.raw!r})")
            if entity.type in ("amount", "number", "pct", "ratio", "date"):
                try:
                    value = Decimal(str(entity.value))
                except InvalidOperation:
                    if entity.type == "date":
                        raise FormulaError(f"invalid date value for {node.key!r}: {entity.value!r}")
                else:
                    if not value.is_finite():
                        raise FormulaError(f"non-finite value for {node.key!r}: {entity.value!r}")
                    return Num(value)
            return Str(str(entity.value))
        if isinstance(node, BinOp):
            return BinOp(node.op, self.substitute(node.left), self.substitute(node.right))
        return node

    def check(self, relations: list[Relation]) -> None:
        """Set requirement outcomes independently of solver acceptance or pins."""
        from processing.common.requirements import FormulaDefinition
        from processing.verification.evaluation import Evaluator, SavedRequirement

        evaluator = Evaluator(EntityRegistry(self.by_key[key] for key in self.by_key if isinstance(key, int)))
        for relation in relations:
            direct = evaluator.requirement(SavedRequirement(relation.index, FormulaDefinition(
                equation=relation.equation)))
            relation.status, relation.error = direct.verdict, direct.error
            relation.involved_files, relation.touches_card = list(direct.files), direct.touches_card
            relation.unknowns = []
            try:
                relation.ast = parse(relation.equation)
                relation.unknowns = sorted((key for key in collect_ids(relation.ast)
                                            if key in self.by_key and self.by_key[key].value is None), key=str)
            except (FormulaError, ValueError):
                relation.ast = None
