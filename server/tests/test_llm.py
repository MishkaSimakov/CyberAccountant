"""Preserve complete JSON containers and the original model output."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import requests

from checkier import llm
from app.logging import append_log, file_log_run
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
    path = tmp_path / "processing.log"
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    outputs = ["```json\r\nnot JSON: Привет\r\n", '```json\n[{"equation": "#1 == #2"}]']
    remaining = iter(outputs)
    monkeypatch.setattr(llm, "_post", lambda *args: {"output_text": next(remaining)})

    with file_log_run(path, "relations") as logger:
        assert llm.call_llm_json("prompt", "test-model", schema=RELATIONS_SCHEMA, max_retries=1, logger=logger) == [
            {"equation": "#1 == #2"},
        ]
    content = path.read_bytes()
    assert content.count(b"--- MODEL OUTPUT |") == 2
    assert all(output.encode("utf-8") in content for output in outputs)


def test_final_validation_failure_still_logs_output(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    output = '{"equation": "#1 == #2"}'
    monkeypatch.setattr(llm, "_post", lambda *args: {"output_text": output})

    path = tmp_path / "processing.log"
    with pytest.raises(llm.LLMError, match="validation failed after retries"):
        with file_log_run(path, "relations") as logger:
            llm.call_llm_json("prompt", "test-model", schema=RELATIONS_SCHEMA, max_retries=0, logger=logger)
    assert output.encode("utf-8") in path.read_bytes()


def test_streaming_output_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    output = "```json\n[]"
    monkeypatch.setattr(llm, "_post_streaming", lambda *args: output)

    path = tmp_path / "processing.log"
    with file_log_run(path, "extraction") as logger:
        assert llm.call_llm("prompt", "test-model", on_token=lambda token: None, logger=logger) == output
    assert output.encode("utf-8") in path.read_bytes()


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


@pytest.mark.parametrize("failures", [0, 2, llm.TRANSPORT_RETRIES + 1])
def test_transport_errors_are_retried_with_backoff(monkeypatch, failures):
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
    calls = 0

    def post(payload, timeout):
        nonlocal calls
        calls += 1
        if calls <= failures:
            raise requests.ConnectionError("Connection reset by peer")
        return {"output_text": "ok"}

    monkeypatch.setattr(llm, "_post", post)
    if failures <= llm.TRANSPORT_RETRIES:
        assert llm.call_llm("prompt", "test-model") == "ok"
        assert calls == failures + 1
    else:
        with pytest.raises(llm.LLMError, match="failed after .* attempts"):
            llm.call_llm("prompt", "test-model")
        assert calls == llm.TRANSPORT_RETRIES + 1


def test_schema_uses_responses_text_format(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    payloads = []

    def post(payload, timeout):
        payloads.append(payload)
        return {"output_text": "[]"}

    monkeypatch.setattr(llm, "_post", post)
    assert llm.call_llm_json("prompt", "test-model", schema=RELATIONS_SCHEMA) == []
    assert len(payloads) == 1
    fmt = payloads[0]["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] == RELATIONS_SCHEMA
    assert not (tmp_path / "llm_outputs").exists()


def test_file_log_appends_across_stages_without_implicit_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(llm, "_post", lambda payload, timeout: {"output_text": payload["input"]})
    path = tmp_path / "processing.log"
    append_log(path, "Earlier stage output\n")
    for stage in ("entity_extraction", "date_normalization"):
        with file_log_run(path, stage) as logger:
            assert llm.call_llm(stage, "test-model", logger=logger) == stage
            llm.call_llm("unbound nested call", "test-model")
    content = path.read_text()
    assert content.startswith("Earlier stage output\n")
    assert content.count("\nRUN ") == 2
    assert content.count("--- MODEL OUTPUT |") == 2
    assert content.index("entity_extraction") < content.index("date_normalization")
    assert "unbound nested call" not in content
    assert not (tmp_path / "llm_outputs").exists()
    llm.call_llm("outside the file run", "test-model")
    assert not (tmp_path / "llm_outputs").exists()
    assert path.read_text() == content


def test_parallel_file_logs_keep_model_outputs_isolated(tmp_path, monkeypatch):
    barrier = Barrier(2, timeout=10)

    def respond(payload, timeout):
        barrier.wait()
        return {"output_text": payload["input"]}

    monkeypatch.setattr(llm, "_post", respond)

    def run(name):
        path = tmp_path / f"{name}.log"
        with file_log_run(path, "extraction") as logger:
            llm.call_llm(name, "test-model", logger=logger)
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
        with file_log_run(path, "extraction") as logger:
            llm.call_llm("prompt", "test-model", on_token=lambda token: None, logger=logger)
    content = path.read_bytes()
    assert output.encode("utf-8") in content
    assert content.count(b"--- MODEL OUTPUT |") == 1
    assert b"ERROR (LLMError): Stream interrupted" in content


@pytest.mark.parametrize("streaming", [False, True])
def test_file_log_preserves_full_prompts_before_each_retry(tmp_path, monkeypatch, streaming):
    path = tmp_path / "processing.log"
    prompt = "Полный запрос\r\n" * 4_000
    prompts = []
    outputs = ["not JSON", "[]"]

    def respond(payload, timeout, on_token=None):
        sent_prompt = payload["input"]
        assert path.read_bytes().endswith(sent_prompt.encode("utf-8") + b"\n")
        prompts.append(sent_prompt)
        output = outputs[len(prompts) - 1]
        return output if streaming else {"output_text": output}

    monkeypatch.setattr(llm, "_post_streaming" if streaming else "_post", respond)
    with file_log_run(path, "extraction") as logger:
        assert llm.call_llm_json(prompt, "test-model", schema=RELATIONS_SCHEMA, max_retries=1,
                                 on_token=(lambda token: None) if streaming else None, logger=logger) == []
    assert prompts[0] == prompt
    assert "Your previous answer failed validation" in prompts[1]
    content = path.read_bytes()
    assert content.count(b"--- MODEL PROMPT |") == 2
    assert content.count(b"--- MODEL OUTPUT |") == 2
    for sent_prompt in prompts:
        assert sent_prompt.encode("utf-8") in content


@pytest.mark.parametrize("fallback", [False, True])
def test_file_log_keeps_prompts_when_requests_fail_or_use_schema_fallback(tmp_path, monkeypatch, fallback):
    path = tmp_path / "processing.log"
    prompt = "The exact prompt\r\n"
    calls = 0

    def respond(payload, timeout):
        nonlocal calls
        calls += 1
        assert path.read_bytes().endswith(prompt.encode("utf-8") + b"\n")
        if fallback and "text" not in payload:
            return {"output_text": "[]"}
        raise llm.LLMError("API 400: response_format unsupported" if fallback else "API unavailable")

    monkeypatch.setattr(llm, "_post", respond)
    if fallback:
        with file_log_run(path, "extraction") as logger:
            assert llm.call_llm_json(prompt, "test-model", schema=RELATIONS_SCHEMA, logger=logger) == []
    else:
        with pytest.raises(llm.LLMError, match="API unavailable"):
            with file_log_run(path, "extraction") as logger:
                llm.call_llm(prompt, "test-model", logger=logger)
    assert calls == (2 if fallback else 1)
    content = path.read_bytes()
    assert content.count(b"--- MODEL PROMPT |") == calls
    assert content.count(prompt.encode("utf-8")) == calls


@pytest.mark.parametrize("chat", [False, True])
def test_truncated_nonstreaming_response_preserves_exact_partial_text(tmp_path, monkeypatch, chat):
    partial = "Частичный ответ\r\n" * 4_000
    response = ({"choices": [{"finish_reason": "length", "message": {"content": partial}}]} if chat else
                {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                 "output_text": partial})
    monkeypatch.setattr(llm, "_post", lambda *args: response)
    path = tmp_path / "processing.log"
    with pytest.raises(llm.LLMError, match="truncated"):
        with file_log_run(path, "extraction") as logger:
            llm.call_llm("prompt", "test-model", logger=logger)
    assert partial.encode("utf-8") in path.read_bytes()
    assert path.read_bytes().count(b"--- MODEL OUTPUT |") == 1


def test_transport_retry_warnings_use_the_supplied_logger(tmp_path, monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
    calls = 0

    def post(payload, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.Timeout("transport timeout")
        return {"output_text": "success"}

    monkeypatch.setattr(llm, "_post", post)
    path = tmp_path / "processing.log"
    with file_log_run(path, "extraction") as logger:
        assert llm.call_llm("prompt", "test-model", logger=logger) == "success"
    content = path.read_text()
    assert "transport timeout" in content and "retrying in 1s" in content
    assert content.count("--- MODEL PROMPT |") == 2
