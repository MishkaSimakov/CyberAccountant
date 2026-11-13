"""Tests for the formula tokenizer/parser/evaluator."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from processing.common.formula import (
    BinOp,
    FormulaError,
    Id,
    Num,
    Sym,
    collect_ids,
    evaluate,
    parse,
    tokenize,
)


def test_tokenize_number():
    toks = tokenize("245000.50")
    assert toks[0].kind == "num"
    assert toks[0].value == Decimal("245000.50")


def test_tokenize_ru_decimal_comma():
    toks = tokenize("245000,50")
    assert toks[0].value == Decimal("245000.50")


def test_tokenize_date():
    toks = tokenize("28.01.2026")
    assert toks[0].kind == "date"
    assert toks[0].value == date(2026, 1, 28)


def test_tokenize_mnemonic_id():
    toks = tokenize("crumble-wink")
    assert toks[0].kind == "id"
    assert toks[0].value == "crumble-wink"


def test_tokenize_numeric_id():
    toks = tokenize("#541")
    assert toks[0].kind == "id"
    assert toks[0].value == 541


def test_tokenize_string():
    toks = tokenize('"ВЕД-ЗП-2026-01"')
    assert toks[0].kind == "str"


def test_parse_arithmetic():
    ast = parse("a-b + c-d * 2")
    assert isinstance(ast, BinOp)


def test_parse_comparison():
    ast = parse("a-b == 100")
    assert isinstance(ast, BinOp)
    assert ast.op == "=="


def test_parse_function():
    ast = parse("norm(a-b)")
    assert isinstance(ast, Id)
    assert ast.func == "norm"


def test_eval_ground():
    ast = parse("2 + 3 * 4")
    assert evaluate(ast, {}) == Decimal(14)


def test_eval_comparison():
    ast = parse("10 > 5")
    assert evaluate(ast, {}) is True


def test_eval_symbolic():
    ast = parse("x-y + 2")
    result = evaluate(ast, {})
    assert isinstance(result, Sym)


def test_eval_string_eq():
    ast = parse('"abc" == "abc"')
    assert evaluate(ast, {}) is True


def test_eval_date_comparison():
    ast = parse("01.01.2026 < 31.05.2026")
    assert evaluate(ast, {}) is True


def test_eval_env_substitution():
    ast = parse("a-b == 10")
    result = evaluate(ast, {"a-b": Decimal(10)})
    assert result is True


def test_collect_ids():
    ast = parse("a-b + c-d == e-f")
    ids = collect_ids(ast)
    assert ids == {"a-b", "c-d", "e-f"}


def test_unbalanced_raises():
    with pytest.raises(FormulaError):
        parse("(1 + 2")


def test_and_or():
    ast = parse("1 < 2 && 3 > 2")
    assert evaluate(ast, {}) is True
    ast = parse("1 > 2 || 3 > 2")
    assert evaluate(ast, {}) is True


def test_formula_imports_and_runs_with_only_the_standard_library():
    server = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", """
import sys
from decimal import Decimal

sys.path.insert(0, sys.argv[1])
from processing.common.formula import collect_ids, evaluate, parse

node = parse('#0 + norm(alpha-beta) == 10 && 01.01.2026 < 02.01.2026')
assert collect_ids(node) == {0, 'alpha-beta'}
assert evaluate(node, {0: Decimal('4'), 'alpha-beta': Decimal('6')}) is True
assert not any(name.split('.')[0] in {
    'app', 'checkier', 'sqlalchemy', 'fastapi', 'infrastructure', 'requests', 'z3',
} for name in sys.modules)
""", str(server)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
