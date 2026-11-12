"""Stage 6: SMT-backed equation system solver (Z3 for numeric/date
constraints, worklist propagation for string identities).

Model:
  - данные/request entities are constants (truth by definition);
  - entities of the requested card row (``variable_keys``) are variables to
    solve and check against their stated values;
  - every other card entity is trusted and grounded as a constant.

String identities (docids, contract numbers) are NOT given to z3 — the
String/seq theory in this z3 build hangs on mixed string+arithmetic contexts
(ignoring solver timeouts). Instead, all ``==``/``!=`` between string-typed
operands are resolved by WORKLIST PROPAGATION: grounded entity values and
string literals are known; an ``==`` between a known and an unknown node
assigns the known value; between two known nodes it is decided true/false on
the spot. This keeps contradictions LOCAL — a false claim (e.g. an
out-of-scope document's contract number) can never poison unrelated
identities, which blind union-find merging would do.

Numeric/date relations go to z3 as LRA/LIA (amounts as Real, dates as Int
ordinals). Each relation is either *entailed* by the constants, *contradicted*
by them, or *contingent*; contingent relations accumulate into one growing
constraint set, so systems only solvable jointly (``x + y == a &&
x - y == b``) pin their variables exactly like single-unknown equations.

After solving, every target-row variable is checked for *rigidity*: if the
negation of its model value is still satisfiable, the system underdetermines
it and the cell is reported ``unverified`` (недостаточно данных) rather than
``ok``/``mismatch``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

import z3

from cyberaccountant.entities import Entity, EntityRegistry, parse_amount
from cyberaccountant.formula import (
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


class FormulaUndetermined(FormulaError):
    """A relation is well-formed but cannot be decided for lack of data
    (e.g. an identity between two ungrounded string variables)."""


@dataclass
class Relation:
    index: int
    equation: str
    tag: str | None = None
    source: str = "llm"
    ast: Node | None = None
    status: str = "pending"  # pending | true | false | error | unverifiable
    error: str | None = None
    unknowns: list = field(default_factory=list)
    involved_files: list = field(default_factory=list)


@dataclass
class Fishy:
    index: int
    evidence: str
    ids_related: list = field(default_factory=list)
    verdict: str = "false"


@dataclass
class Verdict:
    key: str | int | None = None
    num: int = 0
    mnemonic: str = ""
    role: str = ""
    file: str = ""
    raw: str = ""
    type: str = ""
    status: str = ""  # ok | mismatch | unverified | constant
    stated: str | None = None
    expected: str | None = None
    diff: str | None = None
    target: bool = True  # False for non-target card cells (trusted constants)


class _StrVal:
    """Resolved (or unresolvable) string operand — never enters z3."""

    __slots__ = ("value", "desc")

    def __init__(self, value: str | None, desc: str) -> None:
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
        #: num -> resolved string value for string-typed entities
        self.str_value: dict[int, str | None] = {}
        self.acc = z3.Solver()
        self.acc.set("timeout", 20_000)  # ms; unknown -> "unverifiable"
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
            # string consts are symbolic only — they never enter z3
            self.str_value.setdefault(e.num, None)
            return z3.String(f"s{e.num}")  # placeholder, unused in assertions
        if self._is_variable(e):
            return c  # variable: deliberately unbound
        val = e.value
        if val is None:
            return c  # truth unknown — leave free, relations may pin it
        d = _to_decimal(val)
        if d is None:
            return c
        lit = z3.IntVal(int(d)) if sort == "int" else _real_val(d)
        self.acc.add(c == lit)
        self.grounded.add(e.mnemonic)
        self.grounded.add(e.num)
        return c

    def _const(self, e: Entity, func: str | None = None) -> z3.ExprRef:
        if func == "raw":
            if e.num in self.consts:
                return self.consts[e.num]
            v = parse_amount(e.raw)
            if v is None:
                raise FormulaError(
                    f"raw({e.mnemonic}) has no numeric raw value: {e.raw!r}"
                )
            c: z3.ExprRef = z3.Real(f"raw_{e.num}")
            self.acc.add(c == _real_val(v))
            self.grounded.add(e.mnemonic)
            self.grounded.add(e.num)
            self.consts[e.num] = c
            return c
        if e.num not in self.consts:
            self.consts[e.num] = self._new_const(e)
        return self.consts[e.num]

    def _build_consts(self) -> None:
        for e in self.registry.entities:
            self._const(e)

    # -- string identity resolution (worklist propagation, no z3) ---------------
    def _resolve_strings(self) -> None:
        known: dict[tuple, str] = {}  # ("ent", num) | ("lit", value) -> value
        eqs: list[tuple[tuple, tuple]] = []

        def key_of(node: Node) -> tuple | None:
            if isinstance(node, Str):
                return ("lit", node.value)
            if isinstance(node, Id) and node.func is None:
                e = self.by_key.get(node.key)
                if e is not None and self._const_sort(e) == "string":
                    return ("ent", e.num)
            return None

        for e in self.registry.entities:
            if self._const_sort(e) == "string" and e.value is not None:
                known[("ent", e.num)] = str(e.value)

        for rel in self.relations:
            if rel.ast is None:
                continue
            for op, l_node, r_node in self._eq_leaves(rel.ast):
                if op != "==":
                    continue  # != never assigns values
                ka, kb = key_of(l_node), key_of(r_node)
                if ka is not None and kb is not None:
                    eqs.append((ka, kb))

        # propagate known values along == edges until fixpoint; a known-known
        # mismatch is left for translation-time leaf evaluation (local false)
        changed = True
        while changed:
            changed = False
            for a, b in eqs:
                va, vb = known.get(a), known.get(b)
                if va is not None and vb is None:
                    known[b] = va
                    changed = True
                elif vb is not None and va is None:
                    known[a] = vb
                    changed = True

        for e in self.registry.entities:
            if self._const_sort(e) != "string":
                continue
            v = known.get(("ent", e.num))
            self.str_value[e.num] = v
            if self._is_variable(e):
                if v is not None:
                    self.pin[e.mnemonic] = v  # rigid by propagation
                    self.grounded.add(e.mnemonic)
                    self.grounded.add(e.num)
                else:
                    self.free.add(e.mnemonic)

    @staticmethod
    def _eq_leaves(node: Node):
        """All ==/!= comparisons anywhere in the boolean expression tree."""
        if isinstance(node, BinOp):
            if node.op in ("==", "!="):
                yield node.op, node.left, node.right
            else:
                yield from Solver._eq_leaves(node.left)
                yield from Solver._eq_leaves(node.right)

    # -- formula translation ----------------------------------------------------
    def to_z3(self, node: Node):
        """Translate to a z3 Bool/Arith expr, a constant bool (decided string
        comparison or folded logic), or a _StrVal (string operand)."""
        if isinstance(node, Num):
            return _real_val(node.value)
        if isinstance(node, Str):
            return _StrVal(node.value, repr(node.value))
        if isinstance(node, DateLit):
            return z3.IntVal(node.value.toordinal())
        if isinstance(node, Id):
            e = self.by_key.get(node.key)
            if e is None:
                raise FormulaError(f"unknown entity id {node.key!r}")
            if self._const_sort(e) == "string" and node.func != "raw":
                return _StrVal(self.str_value.get(e.num), f"{node.key} (raw={e.raw!r})")
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
                if not isinstance(side, bool) and side.sort().kind() != z3.Z3_BOOL_SORT:
                    raise FormulaError(
                        f"non-boolean operand to '{op}': {self._describe(sub)}"
                    )
            return self._fold(op, [l, r])
        l = self.to_z3(node.left)
        r = self.to_z3(node.right)
        # string comparisons are decided here, never in z3
        if isinstance(l, _StrVal) or isinstance(r, _StrVal):
            if not (isinstance(l, _StrVal) and isinstance(r, _StrVal)):
                raise FormulaError(
                    f"type clash in '{op}': {self._describe(l)}"
                    f" vs {self._describe(r)}"
                )
            if l.value is None or r.value is None:
                raise FormulaUndetermined(
                    f"string value undetermined in '{op}': {l.desc} vs {r.desc}"
                )
            eq = l.value == r.value
            return eq if op == "==" else not eq
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
                ids = collect_ids(rel.ast)
                rel.involved_files = sorted(
                    {self.by_key[k].file for k in ids if k in self.by_key}
                )
            except FormulaError as exc:
                rel.status = "error"
                rel.error = str(exc)

        self._resolve_strings()

        for rel in self.relations:
            if rel.status != "pending" or rel.ast is None:
                continue
            try:
                result = self.to_z3(rel.ast)
            except FormulaUndetermined as exc:
                rel.status = "unverifiable"
                rel.error = str(exc)
                continue
            except FormulaError as exc:
                rel.status = "error"
                rel.error = str(exc)
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
            if self._check(z3.Not(result)) == z3.unsat:
                rel.status = "true"  # entailed by facts alone
            elif self._check(result) == z3.sat:
                self.acc.add(result)  # contingent: accumulate, solve jointly
                rel.status = "true"
            elif self._check(result) == z3.unsat:
                rel.status = "false"
                rel.error = "contradicts documents or accepted relations"
            else:
                rel.status = "unverifiable"
                rel.error = "solver returned unknown (timeout?)"

        self._solve_variables()

    def _solve_variables(self) -> None:
        if self.acc.check() != z3.sat:
            return  # acc only grows consistent constraints — defensive
        m = self.acc.model()
        for e in self.registry.entities:
            if not self._is_variable(e) or self._const_sort(e) == "string":
                continue  # string vars resolved by propagation in _resolve_strings
            c = self._const(e)
            # NOTE: no model_completion — completion makes z3 construct
            # arbitrary values for unconstrained constants (pathologically
            # slow). Without it, eval returns the const itself when the
            # model doesn't fix it — i.e. "free".
            val = m.eval(c)
            if isinstance(val, z3.ExprRef) and val.eq(c):
                self.free.add(e.mnemonic)
                continue
            # rigidity: can the variable still differ from the model value?
            self.acc.push()
            self.acc.add(c != val)
            rigid = self.acc.check() == z3.unsat
            self.acc.pop()
            if rigid:
                try:
                    self.pin[e.mnemonic] = _dec_of(val)
                except ValueError:
                    self.free.add(e.mnemonic)  # non-arithmetic pinned value
            else:
                self.free.add(e.mnemonic)

    # -- outputs ----------------------------------------------------------------
    def verdicts(self) -> list[Verdict]:
        out: list[Verdict] = []
        for e in self.registry.entities:
            stated = str(e.value) if e.value is not None else None
            if self._is_variable(e):
                if e.mnemonic not in self.pin:
                    out.append(
                        Verdict(
                            key=e.mnemonic, num=e.num, mnemonic=e.mnemonic,
                            role=e.role, file=e.file, raw=e.raw, type=e.type,
                            status="unverified", stated=stated, target=True,
                        )
                    )
                    continue
                expected = self.pin[e.mnemonic]
                exp_str = str(expected)
                if stated is None:
                    status, diff = "ok", None
                else:
                    d_stated = _to_decimal(stated)
                    if isinstance(expected, Decimal) and d_stated is not None:
                        status = "ok" if expected == d_stated else "mismatch"
                        diff = (
                            str(abs(expected - d_stated))
                            if status == "mismatch"
                            else None
                        )
                    else:
                        status = "ok" if exp_str == stated else "mismatch"
                        diff = None
                out.append(
                    Verdict(
                        key=e.mnemonic, num=e.num, mnemonic=e.mnemonic,
                        role=e.role, file=e.file, raw=e.raw, type=e.type,
                        status=status, stated=stated, expected=exp_str,
                        diff=diff, target=True,
                    )
                )
            else:
                out.append(
                    Verdict(
                        key=e.mnemonic, num=e.num, mnemonic=e.mnemonic,
                        role=e.role, file=e.file, raw=e.raw, type=e.type,
                        status="constant", stated=stated, target=False,
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
    variable_keys: set | list | None = None,
) -> tuple[list[Relation], list[Fishy], list[Verdict], list[str]]:
    """Top-level entry: build relations, solve against the registry.

    *relations_raw* items either have an ``equation`` key (Relation) or an
    ``evidence`` key (Fishy). *variable_keys* (entity mnemonics/nums of the
    requested card row) selects which card entities are treated as variables;
    ``None`` treats all card entities as variables.
    Returns (relations, fishy, verdicts, dangling).
    """
    rels: list[Relation] = []
    fishy: list[Fishy] = []
    for r in relations_raw:
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
    solver = Solver(rels, registry, variable_keys=variable_keys)
    solver.solve()
    return rels, fishy, solver.verdicts(), solver.dangling_files()
