"""Joint expected-value solving with feasibility and uniqueness diagnostics.

String identities use integer tokens, keeping string theory out of arithmetic
contexts while preserving equality, boolean structure and unknown target values.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import z3

from processing.common.registry import RegistryEntity as Entity, EntityRegistry
from processing.entities.values import parse_amount
from .constraints import Relation, Verdict
from .evaluation import Evaluator
from .results import compare_fields
from processing.common.formula import (
    BinOp,
    DateLit,
    FormulaError,
    Id,
    Node,
    Num,
    Str,
    collect_ids,
    parse,
)

NUMERIC_TYPES = ("amount", "number", "pct", "ratio")
NUMERIC_SORTS = (z3.Z3_REAL_SORT, z3.Z3_INT_SORT)


class _StrVal:
    """Typed string operand backed by an integer token."""

    __slots__ = ("value", "desc")

    def __init__(self, value: z3.ExprRef, desc: str) -> None:
        self.value = value
        self.desc = desc

    def __repr__(self) -> str:
        return f"_StrVal({self.value!r}, {self.desc})"


def _to_decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - Decimal raises InvalidOperation
        return None


def _real_val(d: Decimal):
    return z3.RealVal(format(d, "f"))


def _dec_of(v) -> Decimal | str:
    """Convert a z3 model value to Decimal (numbers) or plain str (strings)."""
    if z3.is_rational_value(v):
        f = Fraction(v.numerator_as_long(), v.denominator_as_long())
        return Decimal(f.numerator) / Decimal(f.denominator)
    if z3.is_int_value(v):
        return Decimal(v.as_long())
    return str(v).strip('"')


class Solver:
    def __init__(
            self,
            relations: list[Relation],
            registry: EntityRegistry,
            variable_keys: set | list | None = None,
            *, timeout_ms: int = 20_000,
    ) -> None:
        self.relations = relations
        self.registry = registry
        self.by_key: dict[str | int, Entity] = {}
        for e in registry.entities:
            self.by_key[e.mnemonic] = e
            self.by_key[e.num] = e
        # None -> every card entity is a variable (targeting disabled)
        self.variable_keys = None if variable_keys is None else set(variable_keys)
        self.consts: dict[int, z3.ExprRef] = {}
        #: entity keys with asserted grounding facts (constants)
        self.grounded: set[str | int] = set()
        #: solved (rigid) values for target-row variables (Decimal | str)
        self.pin: dict[str, Decimal | str] = {}
        #: target-row variables the system underdetermines
        self.free: set[str] = set()
        #: String literals are represented by distinct integer tokens.
        self.string_codes: dict[str, int] = {}
        self.uniqueness: dict[int, str] = {}
        self.diagnostics: list[dict] = []
        self.acc = z3.Solver()
        self.acc.set("timeout", timeout_ms)  # ms; unknown -> "unverifiable"
        self._build_consts()

    # -- entity classification -------------------------------------------------
    def _is_variable(self, e: Entity) -> bool:
        if e.role != "card":
            return False
        if self.variable_keys is None:
            return True
        return e.mnemonic in self.variable_keys or e.num in self.variable_keys

    def _const_sort(self, e: Entity) -> str:
        """int | real | string — mirrors how the const was/will be built.

        An unparsable numeric raw (e.g. "3/1") becomes a string const so
        identity comparisons work and only arithmetic on it is rejected.
        """
        if e.type == "date":
            return "int"
        if e.type in NUMERIC_TYPES and (
                e.value is None or _to_decimal(e.value) is not None
        ):
            return "real"
        return "string"

    def _new_const(self, e: Entity) -> z3.ExprRef:
        sort = self._const_sort(e)
        c: z3.ExprRef
        if sort == "int":
            c = z3.Int(f"e{e.num}")
        elif sort == "real":
            c = z3.Real(f"e{e.num}")
        else:
            c = z3.Int(f"s{e.num}")
        if self._is_variable(e):
            return c  # variable: deliberately unbound
        val = e.value
        if val is None:
            return c  # truth unknown — leave free, relations may pin it
        d = _to_decimal(val)
        if sort == "string":
            lit = self._string_literal(str(val))
        elif d is None or not d.is_finite():
            return c
        else:
            lit = z3.IntVal(int(d)) if sort == "int" else _real_val(d)
        self.acc.add(c == lit)
        self.grounded.add(e.mnemonic)
        self.grounded.add(e.num)
        return c

    def _const(self, e: Entity, func: str | None = None) -> z3.ExprRef:
        if func == "raw":
            if self._is_variable(e):
                multiplier = Decimal(e.norm)
                if not multiplier.is_finite() or not multiplier:
                    raise FormulaError(f"invalid raw multiplier for {e.mnemonic}")
                return self._const(e) / _real_val(multiplier)
            value = parse_amount(e.raw)
            if value is None or not value.is_finite():
                raise FormulaError(f"raw({e.mnemonic}) has no numeric raw value: {e.raw!r}")
            return _real_val(value)
        if e.num not in self.consts:
            self.consts[e.num] = self._new_const(e)
        return self.consts[e.num]

    def _build_consts(self) -> None:
        for e in self.registry.entities:
            self._const(e)

    def _string_literal(self, value: str):
        code = self.string_codes.setdefault(value, len(self.string_codes))
        return z3.IntVal(code)

    # -- formula translation ----------------------------------------------------
    def to_z3(self, node: Node):
        """Translate to a z3 Bool/Arith expr, a constant bool (decided string
        comparison or folded logic), or a _StrVal (string operand)."""
        if isinstance(node, Num):
            return _real_val(node.value)
        if isinstance(node, Str):
            return _StrVal(self._string_literal(node.value), repr(node.value))
        if isinstance(node, DateLit):
            return z3.IntVal(node.value.toordinal())
        if isinstance(node, Id):
            e = self.by_key.get(node.key)
            if e is None:
                raise FormulaError(f"unknown entity id {node.key!r}")
            if self._const_sort(e) == "string" and node.func != "raw":
                return _StrVal(self._const(e), f"{node.key} (raw={e.raw!r})")
            return self._const(e, node.func)
        if isinstance(node, BinOp):
            return self._binop(node)
        raise FormulaError(f"unknown AST node {node!r}")

    def _describe(self, x) -> str:
        if isinstance(x, _StrVal):
            return x.desc
        if isinstance(x, Id):
            e = self.by_key.get(x.key)
            extra = f" (type={e.type}, raw={e.raw!r})" if e else ""
            return f"{x.key}{extra}"
        return repr(getattr(x, "value", x))[:80]

    @staticmethod
    def _fold(op: str, parts: list):
        """Combine &&/|| operands that may mix bools and z3 BoolRefs."""
        exprs = [p for p in parts if not isinstance(p, bool)]
        if op == "&&":
            if any(p is False for p in parts):
                return False
        else:
            if any(p is True for p in parts):
                return True
        if not exprs:
            return op == "&&"
        return exprs[0] if len(exprs) == 1 else (z3.And(*exprs) if op == "&&" else z3.Or(*exprs))

    def _binop(self, node: BinOp):
        op = node.op
        if op in ("&&", "||"):
            l = self.to_z3(node.left)
            r = self.to_z3(node.right)
            for side, sub in ((l, node.left), (r, node.right)):
                if isinstance(side, bool):
                    continue
                if isinstance(side, _StrVal) or side.sort().kind() != z3.Z3_BOOL_SORT:
                    raise FormulaError(
                        f"non-boolean operand to '{op}': {self._describe(sub)}"
                    )
            return self._fold(op, [l, r])
        l = self.to_z3(node.left)
        r = self.to_z3(node.right)
        # Keep string tokens separate from numeric operands.
        if isinstance(l, _StrVal) or isinstance(r, _StrVal):
            if not (isinstance(l, _StrVal) and isinstance(r, _StrVal)):
                raise FormulaError(
                    f"type clash in '{op}': {self._describe(l)}"
                    f" vs {self._describe(r)}"
                )
            if op not in ("==", "!="):
                raise FormulaError(f"non-numeric operand to '{op}': {l.desc} vs {r.desc}")
            return l.value == r.value if op == "==" else l.value != r.value
        # folded booleans (string comparisons, nested logic) — only legal
        # in ==/!= against another boolean (Python bool or z3 BoolRef);
        # anything else is a type clash
        if isinstance(l, bool) or isinstance(r, bool):
            if op not in ("==", "!="):
                raise FormulaError(
                    f"type clash in '{op}': boolean vs non-boolean"
                    f" ({self._describe(node.left)} vs {self._describe(node.right)})"
                )

            def _as_bool(x):
                if isinstance(x, bool):
                    return z3.BoolVal(x)
                if isinstance(x, z3.ExprRef) and x.sort().kind() == z3.Z3_BOOL_SORT:
                    return x
                return None

            lb, rb = _as_bool(l), _as_bool(r)
            if lb is None or rb is None:
                raise FormulaError(
                    f"type clash in '{op}': boolean vs non-boolean"
                    f" ({self._describe(node.left)} vs {self._describe(node.right)})"
                )
            return lb == rb if op == "==" else lb != rb
        lk = l.sort().kind()
        rk = r.sort().kind()
        if op in ("==", "!="):
            clash = (lk in NUMERIC_SORTS) != (rk in NUMERIC_SORTS)
            if clash:
                raise FormulaError(
                    f"type clash in '{op}': {self._describe(node.left)}"
                    f" vs {self._describe(node.right)}"
                )
            return l == r if op == "==" else l != r
        if op in ("<", "<=", ">", ">="):
            if lk not in NUMERIC_SORTS or rk not in NUMERIC_SORTS:
                raise FormulaError(
                    f"non-numeric operand to '{op}': {self._describe(node.left)}"
                    f" vs {self._describe(node.right)}"
                )
            if op == "<":
                return l < r
            if op == "<=":
                return l <= r
            if op == ">":
                return l > r
            return l >= r
        # arithmetic
        if lk not in NUMERIC_SORTS or rk not in NUMERIC_SORTS:
            raise FormulaError(
                f"non-numeric operand to '{op}': {self._describe(node.left)}"
                f" vs {self._describe(node.right)}"
            )
        if op == "+":
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
        if z3.is_true(z3.simplify(r == 0)):
            raise FormulaError("division by zero")
        self.domain.append(r != 0)
        return l / r

    # -- solving ----------------------------------------------------------------
    def _check(self, assertion: z3.ExprRef):
        self.acc.push()
        self.acc.add(assertion)
        res = self.acc.check()
        self.acc.pop()
        return res

    def solve(self) -> None:
        for rel in self.relations:
            try:
                rel.ast = parse(rel.equation)
                if Evaluator(self.registry).validate(rel.ast) != "bool":
                    raise FormulaError("An equation must produce a boolean value")
                ids = collect_ids(rel.ast)
                rel.involved_files = sorted(
                    {self.by_key[k].file for k in ids if k in self.by_key}
                )
                rel.touches_card = any(
                    k in self.by_key and self.by_key[k].role == "card" for k in ids
                )
            except (FormulaError, ArithmeticError, ValueError, TypeError, RecursionError) as exc:
                rel.status = "error"
                rel.error = str(exc)

        for rel in self.relations:
            if rel.status != "pending" or rel.ast is None:
                continue
            try:
                self.domain = []
                result = self.to_z3(rel.ast)
                if self.domain and z3.is_bool(result):
                    result = z3.And(*self.domain, result)
            except (FormulaError, ArithmeticError, ValueError, TypeError, z3.Z3Exception) as exc:
                rel.status = "error"
                rel.error = str(exc)
                continue
            if not isinstance(result, bool) and not z3.is_bool(result):
                rel.status = "error"
                rel.error = "An equation must produce a boolean value"
                continue
            rel.unknowns = [
                k
                for k in collect_ids(rel.ast)
                if k in self.by_key
                   and self.by_key[k].mnemonic not in self.grounded
                   and self.by_key[k].num not in self.grounded
            ]
            if isinstance(result, bool):
                rel.status = "true" if result else "false"
                if not result:
                    rel.error = "string identity comparison is false"
                continue
            negated = self._check(z3.Not(result))
            if negated == z3.unsat:
                rel.status = "entailed"
                continue
            feasible = self._check(result)
            if feasible == z3.sat:
                self.acc.add(result)
                rel.status = "accepted"
            elif feasible == z3.unsat:
                rel.status = "incompatible"
                rel.error = "contradicts fixed inputs or previously accepted constraints"
            else:
                rel.status = "unknown"
                rel.error = "solver returned unknown (timeout?)"

        self.diagnostics = [dict(index=rel.index, equation=rel.equation,
                                 status={"true": "entailed", "false": "incompatible"}.get(rel.status, rel.status),
                                 error=rel.error) for rel in self.relations]
        self._solve_variables()

    def _solve_variables(self) -> None:
        outcome = self.acc.check()
        if outcome != z3.sat:
            for entity in self.registry.entities:
                if self._is_variable(entity):
                    self.uniqueness[entity.num] = "unknown" if outcome == z3.unknown else "inconsistent"
            return
        model = self.acc.model()
        strings = {code: value for value, code in self.string_codes.items()}
        for entity in self.registry.entities:
            if not self._is_variable(entity):
                continue
            const = self._const(entity)
            value = model.eval(const)
            if value.eq(const):
                self.free.add(entity.mnemonic)
                self.uniqueness[entity.num] = "underdetermined"
                continue
            different = self._check(const != value)
            if different == z3.unsat:
                try:
                    expected = strings[value.as_long()] if self._const_sort(entity) == "string" else _dec_of(value)
                    self.pin[entity.mnemonic] = expected
                    self.uniqueness[entity.num] = "unique"
                except (ValueError, KeyError):
                    self.free.add(entity.mnemonic)
                    self.uniqueness[entity.num] = "unknown"
            else:
                self.free.add(entity.mnemonic)
                self.uniqueness[entity.num] = "unknown" if different == z3.unknown else "underdetermined"

    # -- outputs ----------------------------------------------------------------
    def verdicts(self) -> list[Verdict]:
        return compare_fields(self.registry, {
            entity.num: self.pin[entity.mnemonic] for entity in self.registry.entities
            if entity.mnemonic in self.pin}, {
                                  entity.num for entity in self.registry.entities if self._is_variable(entity)})

    def dangling_files(self) -> list[str]:
        """In-scope files whose entities are not referenced by any relation."""
        used_files: set[str] = set()
        for rel in self.relations:
            used_files.update(rel.involved_files)
        all_files = {e.file for e in self.registry.entities}
        return sorted(all_files - used_files)
