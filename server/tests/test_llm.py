"""Preserve complete JSON containers and the original model output."""

import json

import pytest

from checkier import llm
from checkier.schemas import RELATIONS_SCHEMA


@pytest.mark.parametrize("prefix,suffix", [
    ("", ""),
    ("```json\n", ""),
    ("```json\n", "\n```"),
    ("```\n", ""),
    ("Here are the equations:\n", "\nDone."),
])
def test_parse_complete_array(prefix, suffix):
    equations = [
        {"equation": "#64 == #49 + #34", "tag": "agg"},
        {"equation": "#1 == #2", "tag": "ref"},
    ]
    assert llm._json_from_text(prefix + json.dumps(equations) + suffix) == equations


def test_parser_handles_brackets_and_escaped_quotes_in_strings():
    value = {"evidence": 'Текст с } ] и "кавычками"', "ids_related": ["1"]}
    assert llm._json_from_text("```json\n" + json.dumps(value, ensure_ascii=False)) == value


@pytest.mark.parametrize("text", [
    '```json\n[{"equation": "#1 == #2"}',
    '```json\n[{"equation": "#1 == #2"}, invalid]',
    '```json\n{"items": [{"equation": "#1 == #2"}]',
    "no JSON returned",
])
def test_invalid_outer_json_is_not_replaced_with_a_nested_object(text):
    with pytest.raises(llm.LLMError, match="not valid JSON"):
        llm._json_from_text(text)


def test_logs_every_retry_before_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    outputs = ["```json\r\nnot JSON: Привет\r\n", '```json\n[{"equation": "#1 == #2"}]']
    remaining = iter(outputs)
    monkeypatch.setattr(llm, "_post", lambda *args: {"output_text": next(remaining)})

    assert llm.call_llm_json("prompt", "test-model", schema=RELATIONS_SCHEMA, max_retries=1) == [
        {"equation": "#1 == #2"},
    ]
    files = sorted((tmp_path / "llm_outputs").glob("*.txt"))
    assert [path.read_bytes() for path in files] == [output.encode("utf-8") for output in outputs]


def test_final_validation_failure_still_logs_output(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    output = '{"equation": "#1 == #2"}'
    monkeypatch.setattr(llm, "_post", lambda *args: {"output_text": output})

    with pytest.raises(llm.LLMError, match="validation failed after retries"):
        llm.call_llm_json("prompt", "test-model", schema=RELATIONS_SCHEMA, max_retries=0)
    files = list((tmp_path / "llm_outputs").glob("*.txt"))
    assert len(files) == 1
    assert files[0].read_bytes() == output.encode("utf-8")


def test_streaming_output_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    output = "```json\n[]"
    monkeypatch.setattr(llm, "_post_streaming", lambda *args: output)

    assert llm.call_llm("prompt", "test-model", on_token=lambda token: None) == output
    files = list((tmp_path / "llm_outputs").glob("*.txt"))
    assert len(files) == 1
    assert files[0].read_bytes() == output.encode("utf-8")


def test_schema_rejection_is_not_retried_without_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    calls = []

    def post(payload, timeout):
        calls.append(dict(payload))
        raise llm.LLMError("API 400: text.format must contain a JSON schema")

    monkeypatch.setattr(llm, "_post", post)
    with pytest.raises(llm.LLMError, match="API 400"):
        llm.call_llm_json("prompt", "test-model", schema=RELATIONS_SCHEMA, max_retries=0)
    assert len(calls) == 1
    assert "text" in calls[0]
    assert not list((tmp_path / "llm_outputs").glob("*.txt"))


def test_schema_uses_responses_text_format(monkeypatch):
    payloads = []
    monkeypatch.setattr(llm, "_log_output", lambda *args: None)

    def post(payload, timeout):
        payloads.append(payload)
        return {"output_text": "[]"}

    monkeypatch.setattr(llm, "_post", post)
    assert llm.call_llm("prompt", "test-model", schema=RELATIONS_SCHEMA) == "[]"
    assert "response_format" not in payloads[0]
    assert payloads[0]["text"]["format"] == {
        "type": "json_schema",
        "name": "response",
        "schema": RELATIONS_SCHEMA,
        "strict": True,
    }


@pytest.mark.parametrize("response,match", [
    ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "truncated"),
    ({"choices": [{"finish_reason": "length", "message": {"content": "["}}]}, "finish_reason"),
])
def test_nonstreaming_truncation_is_explicit(response, match):
    with pytest.raises(llm.LLMError, match=match):
        llm._extract_text(response)
