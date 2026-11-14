"""Tokenizer, parser and evaluator for the relation formula language.

Grammar (informal)::

    expr      := or_expr
    or_expr   := and_expr ("||" and_expr)*
    and_expr  := cmp_expr ("&&" cmp_expr)*
    cmp_expr  := add_expr (("=="|"!="|"<"|"<="|">"|">=") add_expr)?
    add_expr  := mul_expr (("+"|"-") mul_expr)*
    mul_expr  := unary (("*"|"/") unary)*
    unary     := "-" unary | primary
    primary   := NUMBER | "STRING" | DATE | ID | call | "(" expr ")"
    call      := ("norm"|"raw"|"start"|"end") "(" ID ")"
    ID        := "mnemonic-word" | "#" digits          (per id_style)

Evaluation is symbolic: entity ids evaluate to ``Sym`` leaves carrying the
entity key; known constants are substituted by the caller before evaluation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


class FormulaError(Exception):
    pass


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

@dataclass
class Token:
    kind: str  # num | str | date | id | op | lparen | rparen | func | eof
    value: Any
    pos: int


OPS = ("==", "!=", "<=", ">=", "&&", "||", "+", "-", "*", "/", "<", ">")
OP_SET = set(OPS)

_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_ID_RE_MNEMONIC = re.compile(r"[a-z]+-[a-z]+")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def tokenize(src: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        # string literal
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 1
            if j >= n:
                raise FormulaError(f"unterminated string at {i}")
            tokens.append(Token("str", src[i + 1 : j], i))
            i = j + 1
            continue
        # two-char ops
        two = src[i : i + 2]
        if two in ("==", "!=", "<=", ">=", "&&", "||"):
            tokens.append(Token("op", two, i))
            i += 2
            continue
        if c in "+-*/<>":
            tokens.append(Token("op", c, i))
            i += 1
            continue
        if c == "(":
            tokens.append(Token("lparen", c, i))
            i += 1
            continue
        if c == ")":
            tokens.append(Token("rparen", c, i))
            i += 1
            continue
        # date DD.MM.YYYY (check before number)
        m = _DATE_RE.match(src, i)
        if m:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            tokens.append(Token("date", d, i))
            i = m.end()
            continue
        # number
        m = _NUM_RE.match(src, i)
        if m:
            raw = m.group(0).replace(",", ".")
            try:
                tokens.append(Token("num", Decimal(raw), i))
            except InvalidOperation as exc:
                raise FormulaError(f"bad number {raw!r} at {i}") from exc
            i = m.end()
            continue
        # mnemonic id  word-word
        m = _ID_RE_MNEMONIC.match(src, i)
        if m:
            tokens.append(Token("id", m.group(0), i))
            i = m.end()
            continue
        # numeric id #123
        if c == "#":
            j = i + 1
            while j < n and src[j].isdigit():
                j += 1
            if j == i + 1:
                raise FormulaError(f"expected digits after '#' at {i}")
            tokens.append(Token("id", int(src[i + 1 : j]), i))
            i = j
            continue
        # function names
        for fname in ("start", "end", "norm", "raw"):
            if src.startswith(fname, i) and i + len(fname) < n and src[i + len(fname)] == "(":
                tokens.append(Token("func", fname, i))
                i += len(fname)
                break
        else:
            raise FormulaError(f"unexpected character {c!r} at {i}")
    tokens.append(Token("eof", None, n))
    return tokens


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------

@dataclass
class Node:
    pass


@dataclass
class Num(Node):
    value: Decimal


@dataclass
class Str(Node):
    value: str


@dataclass
class DateLit(Node):
    value: date  # toordinal() used for comparisons


@dataclass
class Id(Node):
    key: str | int  # mnemonic (str) or numeric id (int)
    func: str | None = None  # None | "norm" | "raw" | "start" | "end"


@dataclass
class BinOp(Node):
    op: str
    left: Node
    right: Node


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect_op(self, *ops: str) -> Token:
        tok = self.peek()
        if tok.kind == "op" and tok.value in ops:
            return self.advance()
        raise FormulaError(f"expected one of {ops}, got {tok.kind}:{tok.value!r}")

    # expr := or_expr
    def parse(self) -> Node:
        node = self.parse_or()
        if self.peek().kind != "eof":
            raise FormulaError(f"trailing tokens at {self.peek().pos}")
        return node

    def parse_or(self) -> Node:
        node = self.parse_and()
        while self.peek().kind == "op" and self.peek().value == "||":
            op = self.advance().value
            right = self.parse_and()
            node = BinOp(op, node, right)
        return node

    def parse_and(self) -> Node:
        node = self.parse_cmp()
        while self.peek().kind == "op" and self.peek().value == "&&":
            op = self.advance().value
            right = self.parse_cmp()
            node = BinOp(op, node, right)
        return node

    def parse_cmp(self) -> Node:
        node = self.parse_add()
        tok = self.peek()
        if tok.kind == "op" and tok.value in ("==", "!=", "<", "<=", ">", ">="):
            op = self.advance().value
            right = self.parse_add()
            node = BinOp(op, node, right)
        return node

    def parse_add(self) -> Node:
        node = self.parse_mul()
        while True:
            tok = self.peek()
            if tok.kind == "op" and tok.value in ("+", "-"):
                op = self.advance().value
                right = self.parse_mul()
                node = BinOp(op, node, right)
            else:
                return node

    def parse_mul(self) -> Node:
        node = self.parse_unary()
        while True:
            tok = self.peek()
            if tok.kind == "op" and tok.value in ("*", "/"):
                op = self.advance().value
                right = self.parse_unary()
                node = BinOp(op, node, right)
            else:
                return node

    def parse_unary(self) -> Node:
        tok = self.peek()
        if tok.kind == "op" and tok.value == "-":
            self.advance()
            operand = self.parse_unary()
            return BinOp("*", Num(Decimal(-1)), operand)
        return self.parse_primary()

    def parse_primary(self) -> Node:
        tok = self.peek()
        if tok.kind == "num":
            self.advance()
            return Num(tok.value)
        if tok.kind == "str":
            self.advance()
            return Str(tok.value)
        if tok.kind == "date":
            self.advance()
            return DateLit(tok.value)
        if tok.kind == "id":
            self.advance()
            return Id(tok.value)
        if tok.kind == "func":
            fname = self.advance().value
            if self.peek().kind != "lparen":
                raise FormulaError(f"expected '(' after {fname} at {self.peek().pos}")
            self.advance()  # consume (
            inner = self.peek()
            if inner.kind != "id":
                raise FormulaError(f"{fname}() expects an entity id at {inner.pos}")
            self.advance()
            if self.peek().kind != "rparen":
                raise FormulaError(f"expected ')' at {self.peek().pos}")
            self.advance()
            return Id(inner.value, func=fname)
        if tok.kind == "lparen":
            self.advance()
            node = self.parse_or()
            if self.peek().kind != "rparen":
                raise FormulaError(f"expected ')' at {self.peek().pos}")
            self.advance()
            return node
        raise FormulaError(f"unexpected token {tok.kind}:{tok.value!r} at {tok.pos}")


def parse(src: str) -> Node:
    return Parser(tokenize(src)).parse()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

@dataclass
class Sym:
    """Symbolic leaf: an unresolved entity reference."""

    key: str | int


Value = Decimal | str | int | bool | Sym


def _truthy(v: Value) -> bool:
    if isinstance(v, bool):
        return v
    raise FormulaError(f"expected boolean, got {v!r}")


def evaluate(node: Node, env: dict[str | int, Value]) -> Value:
    """Evaluate *node* with *env* mapping entity keys to concrete values.

    Unresolved ids become ``Sym`` leaves so the solver can inspect which
    variables remain.
    """

    def ev(n: Node) -> Value:
        if isinstance(n, Num):
            return n.value
        if isinstance(n, Str):
            return n.value
        if isinstance(n, DateLit):
            return Decimal(n.value.toordinal())
        if isinstance(n, Id):
            if n.key in env:
                v = env[n.key]
                # norm()/raw() currently do not change semantics — the value in
                # env is already normalized; raw() would need raw values.
                return v
            return Sym(n.key)
        if isinstance(n, BinOp):
            return eval_binop(n, ev)
        raise FormulaError(f"unknown node {n!r}")

    def eval_binop(n: BinOp, ev) -> Value:
        op = n.op
        if op == "&&":
            l = ev(n.left)
            if not _truthy(l):
                return False
            return _truthy(ev(n.right))
        if op == "||":
            l = ev(n.left)
            if _truthy(l):
                return True
            return _truthy(ev(n.right))
        l = ev(n.left)
        r = ev(n.right)
        # short-circuit symbolic propagation: keep expressions symbolic
        if isinstance(l, Sym) or isinstance(r, Sym):
            if op in ("==", "!="):
                # identity: if both keys equal and no func difference, true
                if isinstance(l, Sym) and isinstance(r, Sym):
                    if l.key == r.key and op == "==":
                        return True
                return Sym(f"({l!r}{op}{r!r})")  # opaque symbolic comparator
            if op in ("<", "<=", ">", ">="):
                return Sym(f"({l!r}{op}{r!r})")
            return Sym(f"({l!r}{op}{r!r})")
        # Concrete operands must have compatible types. In particular, Python
        # treats bool as int, but the formula language keeps them separate.
        l_numeric = isinstance(l, (Decimal, int)) and not isinstance(l, bool)
        r_numeric = isinstance(r, (Decimal, int)) and not isinstance(r, bool)
        if op in ("==", "!="):
            if type(l) is not type(r) and not (l_numeric and r_numeric):
                raise FormulaError(f"type clash in '{op}': {l!r} vs {r!r}")
        elif not (l_numeric and r_numeric):
            raise FormulaError(f"non-numeric operands to '{op}': {l!r} vs {r!r}")
        if op == "==":
            return l == r
        if op == "!=":
            return l != r
        if op == "<":
            return l < r
        if op == "<=":
            return l <= r
        if op == ">":
            return l > r
        if op == ">=":
            return l >= r
        if op == "+":
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
        if op == "/":
            return l / r
        raise FormulaError(f"unknown operator {op!r}")

    return ev(node)


def collect_ids(node: Node) -> set[str | int]:
    """All entity ids referenced in the AST."""
    ids: set[str | int] = set()

    def walk(n: Node) -> None:
        if isinstance(n, Id):
            ids.add(n.key)
        elif isinstance(n, BinOp):
            walk(n.left)
            walk(n.right)

    walk(node)
    return ids
