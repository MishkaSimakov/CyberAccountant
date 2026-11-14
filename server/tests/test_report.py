"""Smoke test for the human-readable report formatter."""

import pytest

from entity_fixtures import registry_from_drafts as build_registry
from checkier.report import format_report_text
from processing.verification.results import build_report
from checkier.solver import Relation


@pytest.mark.parametrize("tag, status, role, excluded", [
    ("scope", "false", "data", True),
    ("scope", "true", "data", False),
    ("scope", "error", "data", False),
    ("scope", "unverifiable", "data", False),
    ("ref", "false", "data", False),
    ("scope", "false", "card", False),
    ("scope", "false", "request", False),
])
def test_out_of_scope_is_derived_only_from_failed_scope_formulas(tmp_path, tag, status, role, excluded):
    registry = build_registry({})
    relation = Relation(index=0, equation='value == "TARGET"', tag=tag, status=status,
                        source="custom", involved_files=["file"])
    result = build_report({"file": {"role": role}}, registry, [relation], [], [], [], {})
    assert result["files"]["out_of_scope"] == ({"file": [relation.equation]} if excluded else {})
    assert result["summary"]["out_of_scope"] == int(excluded)


def _sample_report() -> dict:
    return {
        "card": {"file": "card1", "cells": [
            {"id": "qiwi-neme", "num": 0, "mnemonic": "qiwi-neme", "raw": "1000",
             "type": "amount", "status": "ok", "stated": "1000", "expected": "1000",
             "diff": "0", "file": "card1"},
            {"id": "neme-qiwi", "num": 1, "mnemonic": "neme-qiwi", "raw": "500",
             "type": "amount", "status": "mismatch", "stated": "500", "expected": "400",
             "diff": "100", "file": "card1"},
            {"id": "foo-baz", "num": 2, "mnemonic": "foo-baz", "raw": "42",
             "type": "number", "status": "unverified", "stated": "42",
             "expected": None, "diff": None, "file": "card1"},
        ]},
        "constants": [],
        "files": {
            "duplicates": {"h8a": "h8b"},
            "out_of_scope": {"h8c": ["contract"]},
            "dangling": ["h8d"],
        },
        "fishy": [{"evidence": "нет печати", "ids_related": ["qiwi-neme"],
                   "verdict": "false"}],
        "relations": [
            {"index": 0, "equation": "a == b", "tag": "agg", "source": "llm",
             "status": "true", "error": None, "files": ["h8a"],
             "touches_card": True},
            {"index": 1, "equation": "c * d == e", "tag": None, "source": "llm",
             "status": "false", "error": None, "files": ["h8a", "h8d"],
             "touches_card": True},
            {"index": 2, "equation": "f * g == h", "tag": None, "source": "llm",
             "status": "error", "error": "non-numeric entity in arithmetic equation",
             "files": ["h8c"], "touches_card": False},
        ],
        "summary": {
            "card_cells_total": 3, "ok": 1, "mismatch": 1, "unverified": 1,
            "duplicates": 1, "out_of_scope": 1, "dangling": 1, "fishy": 1,
            "relations_total": 3, "relations_true": 1, "relations_false": 1,
            "relations_false_on_card": 1, "relations_unverifiable": 0,
        },
    }


def test_format_report_text_renders_all_sections():
    text = format_report_text(_sample_report(), mapping={
        "h8a": {"original": "данные/09_акт.md"},
        "h8b": {"original": "данные/10_акт_копия.md"},
        "h8c": {"original": "данные/15_акт.md"},
        "h8d": {"original": "данные/20_ведомость.md"},
    })
    assert "1 mismatch" in text
    assert "expected=400" in text
    assert "UNVERIFIED" in text
    assert "touching card (1 of 3)" in text
    assert "#1 FALSE" in text
    assert "non-numeric entity" not in text  # off-card relation is skipped
    assert "skipped, 1 of 3" in text
    assert "нет печати" in text
    assert "данные/09_акт.md" in text  # mapping resolves obfuscated names


def test_direct_evaluation_overrides_constraint_acceptance_in_summary_and_findings():
    from processing.verification.evaluation import EvaluationResult

    relation = Relation(0, '#0 == "TARGET"', tag="scope", status="accepted", requirement_id=71)
    evaluation = EvaluationResult(71, "false", files=("2",))
    result = build_report({"2": {"role": "data"}}, build_registry({}), [relation], [], [], [], {},
                          evaluations={71: evaluation})
    assert result["summary"]["relations_false"] == 1
    assert result["summary"]["requirements_false"] == 1
    assert result["files"]["out_of_scope"] == {"2": [relation.equation]}
    assert relation.status == "accepted"


def test_console_formatter_accepts_canonical_numeric_assertion_references():
    report = _sample_report()
    report["fishy"][0]["ids_related"] = [0, 1]
    assert "ids: 0, 1" in format_report_text(report)
