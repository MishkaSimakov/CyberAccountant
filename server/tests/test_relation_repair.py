"""Tests for stage 5b: equation repair retries."""

from __future__ import annotations

import json
from pathlib import Path

from checkier.config import PipelineConfig
from checkier.entities import EntityRegistry
from checkier.stage5_relations import solve_with_repairs


def _cfg(tmp_path: Path, max_retries: int = 2) -> PipelineConfig:
    cfg = PipelineConfig(
        data_dir=tmp_path / "data",
        out_dir=tmp_path / "out",
        max_retries=max_retries,
    )
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _registry():
    """One card docid entity whose raw '3/1' needs quoting in equations."""
    reg = EntityRegistry(seed=1)
    docid = reg.add("docid", "3/1", "", "1", "cardfile", "card", (0, 3))
    return reg, docid


class _FakeRepair:
    """Queued-response fake for checkier.stage5_relations.call_llm_json."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt, **_kwargs):
        self.prompts.append(prompt)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_type_clash_repaired_with_feedback(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    reg, docid = _registry()
    bad = f"{docid.mnemonic} == 3/1"  # unquoted string literal -> type clash
    fixed = f'{docid.mnemonic} == "3/1"'
    relations_raw = [{"equation": bad, "tag": "ref", "source": "llm"}]
    fake = _FakeRepair([[{"index": 0, "equation": fixed}]])
    monkeypatch.setattr("checkier.stage5_relations.call_llm_json", fake)

    rels, _fishy, verdicts, _dangling = solve_with_repairs(
        cfg, relations_raw, reg, {"in_scope": {"cardfile"}},
        variable_keys=[docid.mnemonic],
    )

    assert rels[0].status == "true"
    assert rels[0].equation == fixed
    assert relations_raw[0]["equation"] == fixed  # mutated in place
    assert verdicts[0].status == "ok"
    # the repair prompt carries the error back to the model
    assert bad in fake.prompts[0]
    assert "type clash" in fake.prompts[0]
    # relations.json rewritten with the corrected equation
    saved = json.loads(cfg.relations_path.read_text(encoding="utf-8"))
    assert saved[0]["equation"] == fixed


def test_repair_budget_is_not_exceeded(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, max_retries=2)
    reg, docid = _registry()
    relations_raw = [
        {"equation": f"{docid.mnemonic} == 3/1", "tag": "ref", "source": "llm"}
    ]
    fake = _FakeRepair(
        [
            [{"index": 0, "equation": f"{docid.mnemonic} == 4/2"}],  # still broken
            [{"index": 0, "equation": f"{docid.mnemonic} == 5/3"}],  # still broken
        ]
    )
    monkeypatch.setattr("checkier.stage5_relations.call_llm_json", fake)

    rels, *_ = solve_with_repairs(
        cfg, relations_raw, reg, {"in_scope": {"cardfile"}},
        variable_keys=[docid.mnemonic],
    )

    # exactly cfg.max_retries repair calls, and the error survives
    assert len(fake.prompts) == cfg.max_retries
    assert rels[0].status == "error"


def test_repair_drop_removes_equation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    reg, docid = _registry()
    relations_raw = [
        {"equation": f"{docid.mnemonic} == 3/1", "tag": "ref", "source": "llm"},
        {"evidence": "something odd", "ids_related": [docid.mnemonic],
         "verdict": "false", "source": "llm"},
    ]
    fake = _FakeRepair([[{"index": 0, "drop": True}]])
    monkeypatch.setattr("checkier.stage5_relations.call_llm_json", fake)

    rels, fishy, *_ = solve_with_repairs(
        cfg, relations_raw, reg, {"in_scope": {"cardfile"}},
        variable_keys=[docid.mnemonic],
    )

    assert rels == []  # dropped
    assert len(fishy) == 1  # fishy entries untouched
    assert relations_raw == [
        {"evidence": "something odd", "ids_related": [docid.mnemonic],
         "verdict": "false", "source": "llm"}
    ]


def test_no_errors_no_repair_call(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    reg, docid = _registry()
    relations_raw = [
        {"equation": f'{docid.mnemonic} == "3/1"', "tag": "ref",
         "source": "llm"}
    ]
    fake = _FakeRepair([])  # pop() would raise if called
    monkeypatch.setattr("checkier.stage5_relations.call_llm_json", fake)

    rels, *_ = solve_with_repairs(
        cfg, relations_raw, reg, {"in_scope": {"cardfile"}},
        variable_keys=[docid.mnemonic],
    )

    assert rels[0].status == "true"
    assert fake.prompts == []
