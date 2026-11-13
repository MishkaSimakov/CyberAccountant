"""Check requirements using recorded entity values, without inferring values."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from checkier.entities import EntityRegistry, parse_amount
from processing.common.formula import (
    BinOp, FormulaError, Id, Node, Num, Str, collect_ids, evaluate, parse,
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
        for relation in relations:
            relation.status, relation.error = "pending", None
            relation.unknowns = []
            try:
                relation.ast = parse(relation.equation)
                ids = collect_ids(relation.ast)
                relation.involved_files = sorted({self.by_key[key].file for key in ids if key in self.by_key})
                relation.touches_card = any(self.by_key[key].role == "card" for key in ids if key in self.by_key)
                unknown = ids - self.by_key.keys()
                if unknown:
                    raise FormulaError(f"unknown entity ids: {sorted(unknown, key=str)}")
                relation.unknowns = sorted((key for key in ids if self.by_key[key].value is None), key=str)
                result = evaluate(self.substitute(relation.ast), {})
                if not isinstance(result, bool):
                    raise FormulaError("An equation must produce a boolean value")
                relation.status = "true" if result else "false"
                if not result:
                    relation.error = "requirement is false for the recorded values"
            except MissingValue as exc:
                relation.status, relation.error = "unverifiable", str(exc)
            except (FormulaError, ArithmeticError, ValueError, TypeError, RecursionError) as exc:
                relation.status = "error"
                relation.error = f"{exc} (equation: {relation.equation})"
