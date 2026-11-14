"""In-memory verification regressions and independent solver diagnostics."""

import logging
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import z3

from entity_fixtures import canonical_documents
from processing.common.corpus import ReviewedCorpus
from processing.common.documents import DocumentRevision, ReviewedDocument
from processing.common.requirements import AssertionDefinition, FormulaDefinition
from processing.common.scope import Scope
from processing.verification.evaluation import SavedRequirement
from processing.verification.interface import VerificationInput
from processing.verification.processor import Verifier
from processing.verification.solver import Solver


def source(card, data="", equations=(), *, label=None, assertions=()):
    canonical = canonical_documents({"1": ("card", card), "2": ("data", data)})
    corpus = ReviewedCorpus(1, tuple(ReviewedDocument(
        DocumentRevision(1, int(key), 3), key + ".md", "final_report" if role == "card" else "supporting", text)
                                     for key, (role, text) in canonical.items()))
    requirements = tuple(SavedRequirement(50 + i, FormulaDefinition(equation=equation, source="saved", tag="agg"))
                         for i, equation in enumerate(equations))
    return VerificationInput(corpus, Scope(target_label=label), requirements + tuple(assertions))


def run(value):
    return Verifier().process(value, logger=logging.getLogger(__name__))


def test_article_0800_and_total_fail_stated_values_but_solve_expected_amounts(monkeypatch):
    value = source('| Статья | 0800 | Итого |\n| Этап | <ent t="amount">112500</ent> | '
                   '<ent t="amount">312500</ent> |\n',
                   '<ent t="amount">100000</ent> <ent t="amount">200000</ent>',
                   ("#0 == #2", "#1 == #2 + #3"), label="Этап")
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("Stage accessed the filesystem"))
    output = run(value)
    assert [result.verdict for result in output.requirement_results.values()] == ["false", "false"]
    assert output.expected_values == {0: "100000", 1: "300000"}
    assert output.uniqueness == {0: "unique", 1: "unique"}
    assert [Decimal(cell["diff"]) for cell in output.report["card"]["cells"]] == [12500, 12500]
    assert output.report["summary"]["relations_false"] == 2
    assert all(item["status"] == "accepted" for item in output.diagnostics)
    assert output.report["entity_documents"] == {0: 1, 1: 1, 2: 2, 3: 2}
    assert output.report["revisions"] == [dict(report_id=1, document_id=i, version=3) for i in (1, 2)]


def test_joint_solving_and_false_fixed_scope_check_do_not_interfere():
    value = source('<ent t="amount">999</ent> <ent t="amount">999</ent>',
                   '<ent t="contract">OTHER</ent>',
                   ('#2 == "TARGET"', '#0 + #1 == 1000', '#0 - #1 == 200'))
    value = replace(value, requirements=(SavedRequirement(50, FormulaDefinition(
        equation='#2 == "TARGET"', tag="scope")), *value.requirements[1:]))
    output = run(value)
    assert output.expected_values == {0: "600", 1: "400"}
    assert output.diagnostics[0]["status"] == "incompatible"
    assert output.findings["out_of_scope"] == {"2": ['#2 == "TARGET"']}
    assert output.report["summary"]["requirements_false"] == 3


def test_unresolved_requirement_and_underdetermined_field_are_distinct():
    value = source('<ent t="amount">50</ent> <ent t="date">unknown</ent>', equations=(
        "0 <= #0 && #0 <= 100", "#1 == 01.01.2026"))
    output = run(value)
    assert output.requirement_results[50].verdict == "true"
    assert output.uniqueness[0] == "underdetermined"
    assert output.requirement_results[51].verdict == "unverifiable"
    assert output.uniqueness[1] == "unique"
    assert output.report["card"]["cells"][1]["status"] == "unverified"


def test_incompatible_constraints_are_diagnostics_not_requirement_fulfillment():
    output = run(source('<ent t="amount">2</ent>', equations=("#0 == 1", "#0 == 2")))
    assert [item.verdict for item in output.requirement_results.values()] == ["false", "true"]
    assert [item["status"] for item in output.diagnostics] == ["accepted", "incompatible"]
    assert output.expected_values == {0: "1"}


def test_string_targets_are_unknown_and_disjunction_does_not_pin_them():
    value = source('<ent t="contract">WRONG</ent>', equations=('#0 == "RIGHT"',))
    output = run(value)
    assert output.expected_values == {0: "RIGHT"}
    assert output.report["card"]["cells"][0]["status"] == "mismatch"
    value = replace(value, requirements=(SavedRequirement(1, FormulaDefinition(
        equation='#0 == "RIGHT" || 1 == 1')),))
    output = run(value)
    assert output.expected_values == {}
    assert output.uniqueness[0] == "underdetermined"


def test_raw_and_norm_are_separate_solver_expressions():
    output = run(source('<ent t="amount" n="1000">0.11</ent>', equations=(
        "raw(#0) == 0.1", "norm(#0) == 100")))
    assert output.expected_values == {0: "100"}
    assert [item["status"] for item in output.diagnostics] == ["accepted", "entailed"]
    assert all(item.verdict == "false" for item in output.requirement_results.values())


def test_solver_timeout_keeps_direct_verdict(monkeypatch):
    monkeypatch.setattr(Solver, "_check", lambda *args: z3.unknown)
    output = run(source('<ent t="amount">2</ent>', equations=("#0 == 1",)))
    assert output.requirement_results[50].verdict == "false"
    assert output.diagnostics[0]["status"] == "unknown"
    assert output.expected_values == {}


def test_uniqueness_timeout_is_not_reported_as_underdetermined(monkeypatch):
    original = Solver._check

    def check(solver, assertion):
        return z3.unknown if z3.is_distinct(assertion) else original(solver, assertion)

    monkeypatch.setattr(Solver, "_check", check)
    output = run(source('<ent t="amount">2</ent>', equations=("#0 == 1",)))
    assert output.uniqueness == {0: "unknown"}
    assert output.expected_values == {}


def test_assertion_only_documents_are_used_and_ids_are_preserved():
    output = run(source('<ent t="amount">2</ent>', '<ent t="amount">3</ent>', assertions=(
        SavedRequirement(900, AssertionDefinition(condition=False, ids_related=(1,))),)))
    assert output.requirement_results[900].ids_related == (1,)
    assert output.findings["dangling"] == ["1"]
