"""Tests for stage 1 tagging retries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from checkier.config import PipelineConfig
from checkier.llm import LLMError
from checkier.stage1_tag import _is_transient, _tag_one

DOC = "hello world"
H8 = "abcdef01"

#: segments that rebuild DOC exactly
GOOD = json.dumps(
    [
        {"content": "", "wrap": "hello", "type": "number"},
        {"content": " world", "wrap": "", "type": "EOF"},
    ]
)
#: valid JSON/schema, but the rebuilt doc diverges from DOC
MISMATCH = json.dumps(
    [
        {"content": "", "wrap": "hello", "type": "number"},
        {"content": " WRONG", "wrap": "", "type": "EOF"},
    ]
)


def _cfg(tmp_path: Path, max_retries: int = 2) -> PipelineConfig:
    cfg = PipelineConfig(
        data_dir=tmp_path / "data",
        out_dir=tmp_path / "out",
        max_retries=max_retries,
    )
    cfg.files_dir.mkdir(parents=True)
    (cfg.files_dir / f"{H8}.md").write_text(DOC, encoding="utf-8")
    return cfg


class _FakeLLM:
    """Queued-response fake for checkier.stage1_tag.call_llm."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt, **_kwargs):
        self.prompts.append(prompt)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_rebuild_mismatch_retries_with_feedback(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    fake = _FakeLLM([MISMATCH, GOOD])
    monkeypatch.setattr("checkier.stage1_tag.call_llm", fake)

    tagged, retries = _tag_one(cfg, H8, "doc.md")

    assert retries == 1
    assert "<ent" in tagged
    # the second attempt carries the validation error back to the model
    assert "failed validation" in fake.prompts[1]
    assert "diverges" in fake.prompts[1]
    assert len(fake.prompts) == 2


def test_retry_budget_is_not_exceeded(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, max_retries=2)
    fake = _FakeLLM([MISMATCH, MISMATCH, MISMATCH, GOOD])  # GOOD never reached
    monkeypatch.setattr("checkier.stage1_tag.call_llm", fake)

    with pytest.raises(RuntimeError, match="tagging failed"):
        _tag_one(cfg, H8, "doc.md")

    # exactly max_retries + 1 calls — no nested inner retry loop
    assert len(fake.prompts) == cfg.max_retries + 1


def test_transient_error_retries_same_prompt(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    fake = _FakeLLM([LLMError("API 503: overloaded", status_code=503), GOOD])
    monkeypatch.setattr("checkier.stage1_tag.call_llm", fake)

    tagged, retries = _tag_one(cfg, H8, "doc.md")

    assert retries == 1
    assert "<ent" in tagged
    # transient errors retry blindly — no feedback appended
    assert fake.prompts[0] == fake.prompts[1]


def test_bad_json_retries_with_feedback(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    fake = _FakeLLM(["this is not json", GOOD])
    monkeypatch.setattr("checkier.stage1_tag.call_llm", fake)

    _, retries = _tag_one(cfg, H8, "doc.md")

    assert retries == 1
    assert "failed validation" in fake.prompts[1]


def test_is_transient():
    assert _is_transient(LLMError("API 503: overloaded", status_code=503))
    assert _is_transient(LLMError("API 429: rate limited", status_code=429))
    assert _is_transient(requests.Timeout())
    assert _is_transient(requests.ConnectionError())
    # validation errors are never transient — even when the text contains "5"
    assert not _is_transient(ValueError("rebuilt doc diverges at char 52: 555"))
    assert not _is_transient(LLMError("model output is not valid JSON"))
    assert not _is_transient(LLMError("API 400: bad request", status_code=400))
