"""Smoke test for the human-readable report formatter."""

from cyberaccountant.report import format_report_text


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
             "status": "true", "error": None, "files": ["h8a"]},
            {"index": 1, "equation": "c * d == e", "tag": None, "source": "llm",
             "status": "false", "error": None, "files": ["h8a", "h8d"]},
            {"index": 2, "equation": "f * g == h", "tag": None, "source": "llm",
             "status": "error", "error": "non-numeric entity in arithmetic equation",
             "files": ["h8c"]},
        ],
        "summary": {
            "card_cells_total": 3, "ok": 1, "mismatch": 1, "unverified": 1,
            "duplicates": 1, "out_of_scope": 1, "dangling": 1, "fishy": 1,
            "relations_total": 3, "relations_true": 1, "relations_false": 1,
            "relations_unverifiable": 0,
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
    assert "#1 FALSE" in text
    assert "non-numeric entity" in text
    assert "нет печати" in text
    assert "данные/09_акт.md" in text  # mapping resolves obfuscated names
