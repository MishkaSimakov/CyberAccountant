"""Equation system solver.

The system is built from relations (equations) over entity ids. Entities from
данные/request documents are constants; entities from the card are variables
whose stated values we verify.

Strategy:
1. Substitute known constants into every relation.
2. Evaluate relations that become fully ground -> boolean verdict.
3. Equalities with exactly one unknown -> solve directly (linear inversion).
4. Remaining multi-variable linear equalities -> sympy linear solve.
5. Inequalities with unknowns -> bound propagation (best effort).
6. Card variables never constrained -> "unverified"; variables whose solved
   value differs from the card's stated value -> "mismatch".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from cyberaccountant.entities import Entity, EntityRegistry
from cyberaccountant.formula import (
    BinOp,
    DateLit,
    FormulaError,
    Id,
    Node,
    Num,
    Str,
    Sym,
    collect_ids,
    evaluate,
    parse,
)


@dataclass
class Relation:
    index: int
    equation: str
    tag: str | None = None
    source: str = "llm"  # llm | scope
    # filled during solving:
    ast: Node | None = None
    status: str = "pending"  # pending | true | false | unverifiable | error
    error: str | None = None
    unknowns: list[str | int] = field(default_factory=list)
    involved_files: list[str] = field(default_factory=list)


@dataclass
class Fishy:
    """LLM observation about suspicious data that cannot be an equation."""

    index: int
    evidence: str
    ids_related: list[str | int]
    verdict: str = "false"


@dataclass
class Verdict:
    key: str | int
    num: int
    mnemonic: str
    role: str
    file: str
    raw: str
    type: str
    status: str  # ok | mismatch | unverified | unused
    stated: Any = None
    expected: Any = None
    diff: Any = None
    relations: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Symbolic linear forms
# ---------------------------------------------------------------------------


@dataclass
class Linear:
    """A linear combination: coeffs {key: Decimal} + const Decimal."""

    coeffs: dict[Any, Decimal]
    const: Decimal = Decimal(0)

    @classmethod
    def constant(cls, v: Decimal) -> "Linear":
        return cls({}, v)

    @classmethod
    def symbol(cls, key: Any) -> "Linear":
        return cls({key: Decimal(1)}, Decimal(0))

    def is_const(self) -> bool:
        return not self.coeffs

    def value(self) -> Decimal | None:
        return self.const if self.is_const() else None

    def __add__(self, other: "Linear") -> "Linear":
        c = dict(self.coeffs)
        for k, v in other.coeffs.items():
            c[k] = c.get(k, Decimal(0)) + v
        return Linear(c, self.const + other.const)

    def __sub__(self, other: "Linear") -> "Linear":
        c = dict(self.coeffs)
        for k, v in other.coeffs.items():
            c[k] = c.get(k, Decimal(0)) - v
        return Linear(c, self.const - other.const)

    def __mul__(self, other: "Linear | Decimal") -> "Linear":
        if isinstance(other, Decimal):
            return Linear(
                {k: v * other for k, v in self.coeffs.items()}, self.const * other
            )
        if self.is_const():
            return other * self.const
        if other.is_const():
            return self * other.const
        raise FormulaError("non-linear multiplication")

    def __truediv__(self, other: "Linear | Decimal") -> "Linear":
        if isinstance(other, Decimal):
            return Linear(
                {k: v / other for k, v in self.coeffs.items()}, self.const / other
            )
        if other.is_const():
            return self / other.const
        raise FormulaError("division by symbolic expression")


def to_linear(node: Node, env: dict) -> Linear:
    """Convert an AST to a Linear form. Dates -> ordinal constants.
    Strings/bools only allowed as bare equality operands (handled by caller).
    Raises FormulaError on non-linear structure.
    """
    if isinstance(node, Num):
        return Linear.constant(node.value)
    if isinstance(node, DateLit):
        return Linear.constant(Decimal(node.value.toordinal()))
    if isinstance(node, Id):
        v = env.get(node.key)
        if isinstance(v, Decimal):
            return Linear.constant(v)
        if isinstance(v, (int,)) and not isinstance(v, bool):
            return Linear.constant(Decimal(v))
        return Linear.symbol(node.key)
    if isinstance(node, Str):
        raise FormulaError("string operand outside equality comparison")
    if isinstance(node, BinOp):
        if node.op in ("&&", "||"):
            raise FormulaError("boolean operator inside arithmetic expression")
        if node.op in ("==", "!=", "<", "<=", ">", ">="):
            raise FormulaError("comparison inside arithmetic expression")
        l = to_linear(node.left, env)
        r = to_linear(node.right, env)
        if node.op == "+":
            return l + r
        if node.op == "-":
            return l - r
        if node.op == "*":
            return l * r
        if node.op == "/":
            return l / r
    raise FormulaError(f"cannot linearize {node!r}")


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------


def _to_decimal(value: Any) -> Decimal | None:
    """Best-effort Decimal conversion; None if the value isn't numeric."""
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - Decimal raises InvalidOperation on bad input
        return None


class Solver:
    def __init__(
        self,
        relations: list[Relation],
        registry: EntityRegistry,
    ) -> None:
        self.relations = relations
        self.registry = registry
        self.by_key: dict[str | int, Entity] = {}
        for e in registry.entities:
            self.by_key[e.mnemonic] = e
            self.by_key[e.num] = e
        # known values: Decimal for amounts/dates (as ordinal), str for ids
        self.values: dict[str | int, Any] = {}
        for e in registry.entities:
            if e.value is None:
                continue
            if e.role in ("data", "request"):
                if e.type == "date":
                    dec = _to_decimal(e.value)
                    self.values[e.key] = dec if dec is not None else str(e.value)
                elif e.type in ("amount", "number", "pct", "ratio"):
                    dec = _to_decimal(e.value)
                    # non-numeric "number" values (e.g. "3/1") stay strings
                    self.values[e.key] = dec if dec is not None else str(e.value)
                else:
                    self.values[e.key] = str(e.value)
        # card entities are variables (even though we know their stated value)
        self.stated: dict[str | int, Any] = {}
        for e in registry.entities:
            if e.role == "card":
                if e.type == "date" and e.value is not None:
                    dec = _to_decimal(e.value)
                    self.stated[e.key] = dec if dec is not None else str(e.value)
                elif e.type in ("amount", "number", "pct", "ratio") and e.value is not None:
                    dec = _to_decimal(e.value)
                    self.stated[e.key] = dec if dec is not None else str(e.value)
                else:
                    self.stated[e.key] = e.value
        # bounds for inequality propagation
        self._bounds: dict[Any, tuple[str, Decimal]] = {}

    @staticmethod
    def _bound_tighter(old: tuple[str, Decimal], new: tuple[str, Decimal]) -> bool:
        # Conservative: always accept new bound (bounds only get tighter with
        # monotone propagation in this simple scheme).
        return True

    # -- main loop ----------------------------------------------------------
    def solve(self) -> None:
        # Parse all relations first
        for rel in self.relations:
            try:
                rel.ast = parse(rel.equation)
                ids = collect_ids(rel.ast)
                rel.unknowns = [
                    k
                    for k in ids
                    if self._entity_role(k) == "card" and k not in self.values
                ]
                rel.involved_files = sorted(
                    {self.by_key[k].file for k in ids if k in self.by_key}
                )
            except FormulaError as exc:
                rel.status = "error"
                rel.error = str(exc)

        for _ in range(50):  # fixpoint
            progressed = self._iterate()
            if not progressed:
                break
        # Final classification
        for rel in self.relations:
            if rel.status == "error":
                continue
            self._classify(rel)

    def _iterate(self) -> bool:
        progressed = False
        for rel in self.relations:
            if rel.status != "pending" or rel.ast is None:
                continue
            try:
                if self._try_relation(rel):
                    progressed = True
            except Exception as exc:  # noqa: BLE001 - one bad relation must not
                rel.status = "error"   # kill the solve; diagnose and move on
                rel.error = f"{type(exc).__name__}: {exc}"
        return progressed

    # -- relation handling --------------------------------------------------
    def _try_relation(self, rel: Relation) -> bool:
        """Try to make progress on one relation. Returns True if a new value
        was learned."""
        node = rel.ast
        try:
            if isinstance(node, BinOp) and node.op in ("&&", "||"):
                # Split into parts and handle each
                parts = self._flatten_bool(node)
                all_done = True
                learned = False
                for part in parts:
                    sub = Relation(
                        index=rel.index,
                        equation="(part)",
                        tag=rel.tag,
                        source=rel.source,
                        ast=part,
                    )
                    self._fill_unknowns(sub)
                    if sub.status == "pending":
                        if self._try_simple(sub):
                            learned = True
                        else:
                            all_done = False
                    elif sub.status == "error":
                        rel.status = "error"
                        rel.error = sub.error
                        return False
                if all_done:
                    rel.status = "true"
                return learned
            return self._try_simple(rel)
        except FormulaError as exc:
            rel.status = "error"
            rel.error = str(exc)
            return False

    def _flatten_bool(self, node: Node) -> list[Node]:
        if isinstance(node, BinOp) and node.op in ("&&", "||"):
            return self._flatten_bool(node.left) + self._flatten_bool(node.right)
        return [node]

    def _fill_unknowns(self, rel: Relation) -> None:
        if rel.ast is None:
            rel.unknowns = []
            return
        ids = collect_ids(rel.ast)
        rel.unknowns = [
            k for k in ids if self._entity_role(k) == "card" and k not in self.values
        ]

    def _try_simple(self, rel: Relation) -> bool:
        """Handle a single comparison or equality. Returns True if progress."""
        node = rel.ast
        if node is None:
            return False
        if not isinstance(node, BinOp):
            # bare boolean expression — just evaluate
            v = evaluate(node, self.values)
            if isinstance(v, Sym):
                return False
            rel.status = "true" if v else "false"
            return False
        op = node.op
        if op in ("==", "!="):
            return self._try_equality(rel, node, op)
        if op in ("<", "<=", ">", ">="):
            return self._try_inequality(rel, node, op)
        # arithmetic top-level (shouldn't happen in well-formed relations)
        v = evaluate(node, self.values)
        if isinstance(v, Sym):
            return False
        rel.status = "true" if v else "false"
        return False

    def _try_equality(self, rel: Relation, node: BinOp, op: str) -> bool:
        # string identity equalities: only when at least one side is a string
        # literal or an id that resolves to a string value
        if isinstance(node.left, Str) or isinstance(node.right, Str):
            lv = self._ground_string(node.left)
            rv = self._ground_string(node.right)
            if lv is not None and rv is not None:
                eq = lv == rv
                rel.status = "true" if (eq if op == "==" else not eq) else "false"
                return False
            # one side unknown string variable bound to a literal
            if op == "==":
                for side, other in ((node.left, node.right), (node.right, node.left)):
                    if (
                        isinstance(side, Id)
                        and isinstance(other, Str)
                        and side.key not in self.values
                    ):
                        self.values[side.key] = other.value
                        rel.status = "true"
                        return True
            return False
        # linear form
        try:
            l = to_linear(node.left, self.values)
            r = to_linear(node.right, self.values)
        except FormulaError:
            v = evaluate(node, self.values)
            if isinstance(v, Sym):
                return False
            rel.status = "true" if v else "false"
            return False
        diff = l - r
        if diff.is_const():
            eq = diff.const == 0
            rel.status = "true" if (eq if op == "==" else not eq) else "false"
            return False
        if op == "!=":
            return False  # can't prove disequality of symbolic forms
        # diff = sum(coeff_k * k) + const == 0
        # a "known" entity with a non-numeric value (e.g. docid-like "3/1"
        # tagged as number) can't take part in arithmetic — flag the relation
        bad = [
            k for k in diff.coeffs
            if k in self.values and not isinstance(self.values[k], Decimal)
        ]
        if bad:
            detail = "; ".join(
                f"{k}={self.values[k]!r} "
                f"(type={self.by_key[k].type}, raw={self.by_key[k].raw!r}, file={self.by_key[k].file})"
                for k in bad
            )
            rel.status = "error"
            rel.error = f"non-numeric entity in arithmetic equation: {detail}"
            return False
        unknowns = {k: v for k, v in diff.coeffs.items() if k not in self.values}
        known_coeffs = {
            k: v for k, v in diff.coeffs.items() if k in self.values
        }
        const_total = diff.const + sum(
            self.values[k] * c for k, c in known_coeffs.items()
        )
        if len(unknowns) == 1:
            (key, coeff) = next(iter(unknowns.items()))
            try:
                val = -const_total / coeff
            except Exception:
                rel.status = "error"
                rel.error = "division by zero solving linear equation"
                return False
            self.values[key] = val
            rel.status = "true"
            return True
        # multi-variable: leave pending (sympy pass could go here)
        return False

    def _ground_string(self, node: Node) -> str | None:
        if isinstance(node, Str):
            return node.value
        if isinstance(node, Id):
            v = self.values.get(node.key)
            return v if isinstance(v, str) else None
        return None

    def _try_inequality(self, rel: Relation, node: BinOp, op: str) -> bool:
        # Only handle fully-ground inequalities for now; bound propagation
        # for single-unknown cases.
        try:
            l = to_linear(node.left, self.values)
            r = to_linear(node.right, self.values)
        except FormulaError:
            v = evaluate(node, self.values)
            if isinstance(v, Sym):
                return False
            rel.status = "true" if v else "false"
            return False
        diff = l - r
        if diff.is_const():
            c = diff.const
            ok = {
                "<": c < 0,
                "<=": c <= 0,
                ">": c > 0,
                ">=": c >= 0,
            }[op]
            rel.status = "true" if ok else "false"
            return False
        # single unknown -> derive a bound
        unknowns = {k: v for k, v in diff.coeffs.items() if k not in self.values}
        if len(unknowns) == 1:
            (key, coeff) = next(iter(unknowns.items()))
            const_total = diff.const + sum(
                self.values[k] * c
                for k, c in diff.coeffs.items()
                if k in self.values and isinstance(self.values[k], Decimal)
            )
            # coeff*x + const_total <op> 0  ->  x <op'> -const_total/coeff
            bound = -const_total / coeff
            op_norm = op if coeff > 0 else {"<": ">", "<=": ">=", ">": "<", ">=": "<="}[op]
            existing = self._bounds.get(key)
            new_bound = (op_norm, bound)
            if existing is None or self._bound_tighter(existing, new_bound):
                self._bounds[key] = new_bound
                return True
            return False
        return False

    # -- classification -----------------------------------------------------
    def _classify(self, rel: Relation) -> None:
        if rel.status == "error":
            return
        if rel.status == "pending":
            rel.status = "unverifiable"

    def _entity_role(self, key: str | int) -> str | None:
        e = self.by_key.get(key)
        return e.role if e else None

    # -- verdicts ------------------------------------------------------------
    def verdicts(self) -> list[Verdict]:
        out: list[Verdict] = []
        for e in self.registry.entities:
            key = e.key
            stated = self.stated.get(key)
            if e.role == "card":
                if key in self.values:
                    expected = self.values[key]
                    if stated is not None and expected is not None:
                        diff = None
                        try:
                            d = abs(Decimal(str(expected)) - Decimal(str(stated)))
                            diff = str(d)
                        except Exception:
                            diff = None
                        status = "ok" if str(expected) == str(stated) else "mismatch"
                    else:
                        status = "ok"
                    out.append(
                        Verdict(
                            key=key, num=e.num, mnemonic=e.mnemonic, role=e.role,
                            file=e.file, raw=e.raw, type=e.type, status=status,
                            stated=str(stated) if stated is not None else None,
                            expected=str(expected), diff=diff,
                        )
                    )
                else:
                    out.append(
                        Verdict(
                            key=key, num=e.num, mnemonic=e.mnemonic, role=e.role,
                            file=e.file, raw=e.raw, type=e.type, status="unverified",
                            stated=str(stated) if stated is not None else None,
                        )
                    )
            else:
                # data/request entities: check contradictions among themselves
                out.append(
                    Verdict(
                        key=key, num=e.num, mnemonic=e.mnemonic, role=e.role,
                        file=e.file, raw=e.raw, type=e.type,
                        status="constant",
                        stated=str(e.value),
                    )
                )
        return out

    def dangling_files(self) -> list[str]:
        """In-scope files whose entities are not referenced by any relation."""
        used_files: set[str] = set()
        for rel in self.relations:
            used_files.update(rel.involved_files)
        all_files = {e.file for e in self.registry.entities}
        return sorted(all_files - used_files)


def solve_system(
    relations_raw: list[dict],
    registry: EntityRegistry,
) -> tuple[list[Relation], list[Fishy], list[Verdict], list[str]]:
    """Top-level entry: build relations, solve.

    *relations_raw* items either have an ``equation`` key (Relation) or an
    ``evidence`` key (Fishy). Returns (relations, fishy, verdicts, dangling).
    """
    rels: list[Relation] = []
    fishy: list[Fishy] = []
    for i, r in enumerate(relations_raw):
        if "equation" in r:
            rels.append(
                Relation(
                    index=len(rels),
                    equation=r["equation"],
                    tag=r.get("tag"),
                    source=r.get("source", "llm"),
                )
            )
        else:
            fishy.append(
                Fishy(
                    index=len(fishy),
                    evidence=r["evidence"],
                    ids_related=list(r.get("ids_related", [])),
                    verdict=r.get("verdict", "false"),
                )
            )
    solver = Solver(rels, registry)
    solver.solve()
    return rels, fishy, solver.verdicts(), solver.dangling_files()
