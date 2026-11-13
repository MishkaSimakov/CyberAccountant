"""Preserve complete JSON containers and the original model output."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from checkier import llm
from checkier.file_log import append_log, file_log_run
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


def test_schema_fallback_response_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    calls = []

    def post(payload, timeout):
        calls.append(dict(payload))
        if "response_format" in payload:
            raise llm.LLMError("API 400: response_format unsupported")
        return {"output_text": "[]"}

    monkeypatch.setattr(llm, "_post", post)
    assert llm.call_llm_json("prompt", "test-model", schema=RELATIONS_SCHEMA) == []
    assert len(calls) == 2
    files = list((tmp_path / "llm_outputs").glob("*.txt"))
    assert len(files) == 1
    assert files[0].read_bytes() == b"[]"


def test_file_log_appends_across_stages_and_restores_the_context(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(llm, "_post", lambda payload, timeout: {"output_text": payload["input"]})
    path = tmp_path / "processing.log"
    append_log(path, "Earlier stage output\n")
    for stage in ("entity_extraction", "date_normalization"):
        with file_log_run(path, stage):
            assert llm.call_llm(stage, "test-model") == stage
    content = path.read_text()
    assert content.startswith("Earlier stage output\n")
    assert content.count("\nRUN ") == 2
    assert content.count("--- MODEL OUTPUT |") == 2
    assert content.index("entity_extraction") < content.index("date_normalization")
    assert not (tmp_path / "llm_outputs").exists()
    llm.call_llm("outside the file run", "test-model")
    assert len(list((tmp_path / "llm_outputs").glob("*.txt"))) == 1
    assert path.read_text() == content


def test_parallel_file_logs_keep_model_outputs_isolated(tmp_path, monkeypatch):
    barrier = Barrier(2, timeout=10)

    def respond(payload, timeout):
        barrier.wait()
        return {"output_text": payload["input"]}

    monkeypatch.setattr(llm, "_post", respond)

    def run(name):
        path = tmp_path / f"{name}.log"
        with file_log_run(path, "extraction"):
            llm.call_llm(name, "test-model")
        return path.read_text()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(run, ("first-document", "second-document"))
    assert "first-document" in first and "second-document" not in first
    assert "second-document" in second and "first-document" not in second


def test_interrupted_stream_keeps_partial_output_in_the_file_log(tmp_path, monkeypatch):
    output = "Частичный ответ\r\n" * 4_000

    def stream(payload, timeout, on_token):
        on_token(output)
        raise llm.LLMError("Stream interrupted")

    monkeypatch.setattr(llm, "_post_streaming", stream)
    path = tmp_path / "processing.log"
    with pytest.raises(llm.LLMError, match="Stream interrupted"):
        with file_log_run(path, "extraction"):
            llm.call_llm("prompt", "test-model", on_token=lambda token: None)
    content = path.read_bytes()
    assert output.encode("utf-8") in content
    assert content.count(b"--- MODEL OUTPUT |") == 1
    assert b"ERROR (LLMError): Stream interrupted" in content
