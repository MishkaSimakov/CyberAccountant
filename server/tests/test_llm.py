"""Preserve complete JSON containers and the original model output."""

import json
import logging
from processing.common.llm import ModelRequestOptions
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from processing.common import llm
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


def test_logs_every_retry_before_validation(tmp_path, monkeypatch, llm_client):
    path = tmp_path / "processing.log"
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    outputs = ["```json\r\nnot JSON: Привет\r\n", '```json\n[{"equation": "#1 == #2"}]']
    remaining = iter(outputs)
    monkeypatch.setattr(llm_client.adapter, "generate", lambda *args, **kwargs: next(remaining))

    with file_log_run(path, "relations") as logger:
        assert llm_client.call_llm_json("prompt",
                                        ModelRequestOptions(model="test-model", schema=RELATIONS_SCHEMA, max_retries=1),
                                        logger=logger) == [
                   {"equation": "#1 == #2"},
               ]
    content = path.read_bytes()
    assert content.count(b"--- MODEL OUTPUT |") == 2
    assert all(output.encode("utf-8") in content for output in outputs)


def test_final_validation_failure_still_logs_output(tmp_path, monkeypatch, llm_client):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    output = '{"equation": "#1 == #2"}'
    monkeypatch.setattr(llm_client.adapter, "generate", lambda *args, **kwargs: output)

    path = tmp_path / "processing.log"
    with pytest.raises(llm.LLMError, match="validation failed after retries"):
        with file_log_run(path, "relations") as logger:
            llm_client.call_llm_json("prompt",
                                     ModelRequestOptions(model="test-model", schema=RELATIONS_SCHEMA, max_retries=0),
                                     logger=logger)
    assert output.encode("utf-8") in path.read_bytes()


def test_streaming_output_is_logged(tmp_path, monkeypatch, llm_client):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    output = "```json\n[]"
    monkeypatch.setattr(llm_client.adapter, "generate", lambda *args, **kwargs: output)

    path = tmp_path / "processing.log"
    with file_log_run(path, "extraction") as logger:
        assert llm_client.call_llm("prompt", ModelRequestOptions(model="test-model"), on_token=lambda token: None,
                                   logger=logger) == output
    assert output.encode("utf-8") in path.read_bytes()


def test_schema_rejection_is_not_retried_without_schema(tmp_path, monkeypatch, llm_client):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    calls = []

    def post(prompt, options, **kwargs):
        calls.append(options)
        raise llm.LLMError("API 400: text.format must contain a JSON schema")

    monkeypatch.setattr(llm_client.adapter, "generate", post)
    with pytest.raises(llm.LLMError, match="API 400"):
        llm_client.call_llm_json("prompt",
                                 ModelRequestOptions(model="test-model", schema=RELATIONS_SCHEMA, max_retries=0),
                                 logger=logging.getLogger(__name__))
    assert len(calls) == 1
    assert calls[0].schema == RELATIONS_SCHEMA
    assert not list((tmp_path / "llm_outputs").glob("*.txt"))


@pytest.mark.parametrize("failures", [0, 2, llm.TRANSPORT_RETRIES + 1])
def test_transport_errors_are_retried_with_backoff(monkeypatch, failures, llm_client):
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
    calls = 0

    def post(prompt, options, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= failures:
            raise llm.ModelTransportError("Connection reset by peer")
        return "ok"

    monkeypatch.setattr(llm_client.adapter, "generate", post)
    if failures <= llm.TRANSPORT_RETRIES:
        assert llm_client.call_llm("prompt", ModelRequestOptions(model="test-model"),
                                   logger=logging.getLogger(__name__)) == "ok"
        assert calls == failures + 1
    else:
        with pytest.raises(llm.LLMError, match="failed after .* attempts"):
            llm_client.call_llm("prompt", ModelRequestOptions(model="test-model"), logger=logging.getLogger(__name__))
        assert calls == llm.TRANSPORT_RETRIES + 1


def test_schema_options_reach_adapter(tmp_path, monkeypatch, llm_client):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    payloads = []

    def post(prompt, options, **kwargs):
        payloads.append(options)
        return "[]"

    monkeypatch.setattr(llm_client.adapter, "generate", post)
    assert llm_client.call_llm_json("prompt", ModelRequestOptions(model="test-model", schema=RELATIONS_SCHEMA),
                                    logger=logging.getLogger(__name__)) == []
    assert len(payloads) == 1
    assert payloads[0].schema == RELATIONS_SCHEMA
    assert payloads[0].schema_name == "response"
    assert not (tmp_path / "llm_outputs").exists()


def test_file_log_appends_across_stages_without_implicit_routing(tmp_path, monkeypatch, llm_client):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(llm_client.adapter, "generate", lambda prompt, options, **kwargs: prompt)
    path = tmp_path / "processing.log"
    append_log(path, "Earlier stage output\n")
    for stage in ("entity_extraction", "date_normalization"):
        with file_log_run(path, stage) as logger:
            assert llm_client.call_llm(stage, ModelRequestOptions(model="test-model"), logger=logger) == stage
            llm_client.call_llm("unbound nested call", ModelRequestOptions(model="test-model"),
                                logger=logging.getLogger(__name__))
    content = path.read_text()
    assert content.startswith("Earlier stage output\n")
    assert content.count("\nRUN ") == 2
    assert content.count("--- MODEL OUTPUT |") == 2
    assert content.index("entity_extraction") < content.index("date_normalization")
    assert "unbound nested call" not in content
    assert not (tmp_path / "llm_outputs").exists()
    llm_client.call_llm("outside the file run", ModelRequestOptions(model="test-model"),
                        logger=logging.getLogger(__name__))
    assert not (tmp_path / "llm_outputs").exists()
    assert path.read_text() == content


def test_parallel_file_logs_keep_model_outputs_isolated(tmp_path, monkeypatch, llm_client):
    barrier = Barrier(2, timeout=10)

    def respond(prompt, options, **kwargs):
        barrier.wait()
        return prompt

    monkeypatch.setattr(llm_client.adapter, "generate", respond)

    def run(name):
        path = tmp_path / f"{name}.log"
        with file_log_run(path, "extraction") as logger:
            llm_client.call_llm(name, ModelRequestOptions(model="test-model"), logger=logger)
        return path.read_text()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(run, ("first-document", "second-document"))
    assert "first-document" in first and "second-document" not in first
    assert "second-document" in second and "first-document" not in second


def test_interrupted_stream_keeps_partial_output_in_the_file_log(tmp_path, monkeypatch, llm_client):
    output = "Частичный ответ\r\n" * 4_000

    def stream(prompt, options, *, on_token):
        on_token(output)
        raise llm.LLMError("Stream interrupted")

    monkeypatch.setattr(llm_client.adapter, "generate", stream)
    path = tmp_path / "processing.log"
    with pytest.raises(llm.LLMError, match="Stream interrupted"):
        with file_log_run(path, "extraction") as logger:
            llm_client.call_llm("prompt", ModelRequestOptions(model="test-model"), on_token=lambda token: None,
                                logger=logger)
    content = path.read_bytes()
    assert output.encode("utf-8") in content
    assert content.count(b"--- MODEL OUTPUT |") == 1
    assert b"ERROR (LLMError): Stream interrupted" in content


@pytest.mark.parametrize("streaming", [False, True])
def test_file_log_preserves_full_prompts_before_each_retry(tmp_path, monkeypatch, streaming, llm_client):
    path = tmp_path / "processing.log"
    prompt = "Полный запрос\r\n" * 4_000
    prompts = []
    outputs = ["not JSON", "[]"]

    def respond(prompt, options, *, on_token=None):
        sent_prompt = prompt
        assert path.read_bytes().endswith(sent_prompt.encode("utf-8") + b"\n")
        prompts.append(sent_prompt)
        output = outputs[len(prompts) - 1]
        return output

    monkeypatch.setattr(llm_client.adapter, "generate", respond)
    with file_log_run(path, "extraction") as logger:
        assert llm_client.call_llm_json(prompt,
                                        ModelRequestOptions(model="test-model", schema=RELATIONS_SCHEMA, max_retries=1),
                                        on_token=(lambda token: None) if streaming else None, logger=logger) == []
    assert prompts[0] == prompt
    assert "Your previous answer failed validation" in prompts[1]
    content = path.read_bytes()
    assert content.count(b"--- MODEL PROMPT |") == 2
    assert content.count(b"--- MODEL OUTPUT |") == 2
    for sent_prompt in prompts:
        assert sent_prompt.encode("utf-8") in content


@pytest.mark.parametrize("fallback", [False, True])
def test_file_log_keeps_prompts_when_requests_fail_or_use_schema_fallback(tmp_path, monkeypatch, fallback, llm_client):
    path = tmp_path / "processing.log"
    prompt = "The exact prompt\r\n"
    calls = 0

    def respond(prompt, options, **kwargs):
        nonlocal calls
        calls += 1
        assert path.read_bytes().endswith(prompt.encode("utf-8") + b"\n")
        if fallback and options.schema is None:
            return "[]"
        if fallback:
            raise llm.StructuredOutputUnsupported("Schema unsupported")
        raise llm.LLMError("API unavailable")

    monkeypatch.setattr(llm_client.adapter, "generate", respond)
    if fallback:
        with file_log_run(path, "extraction") as logger:
            assert llm_client.call_llm_json(prompt, ModelRequestOptions(model="test-model", schema=RELATIONS_SCHEMA),
                                            logger=logger) == []
    else:
        with pytest.raises(llm.LLMError, match="API unavailable"):
            with file_log_run(path, "extraction") as logger:
                llm_client.call_llm(prompt, ModelRequestOptions(model="test-model"), logger=logger)
    assert calls == (2 if fallback else 1)
    content = path.read_bytes()
    assert content.count(b"--- MODEL PROMPT |") == calls
    assert content.count(prompt.encode("utf-8")) == calls


def test_nonstreaming_failure_preserves_exact_partial_text(tmp_path, monkeypatch, llm_client):
    partial = "Частичный ответ\r\n" * 4_000

    def fail(prompt, options, **kwargs):
        raise llm.LLMError("model output is truncated", partial_output=partial)

    monkeypatch.setattr(llm_client.adapter, "generate", fail)
    path = tmp_path / "processing.log"
    with pytest.raises(llm.LLMError, match="truncated"):
        with file_log_run(path, "extraction") as logger:
            llm_client.call_llm("prompt", ModelRequestOptions("test-model"), logger=logger)
    assert partial.encode("utf-8") in path.read_bytes()
    assert path.read_bytes().count(b"--- MODEL OUTPUT |") == 1


def test_transport_retry_warnings_use_the_supplied_logger(tmp_path, monkeypatch, llm_client):
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
    calls = 0

    def post(prompt, options, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise llm.ModelTransportError("transport timeout")
        return "success"

    monkeypatch.setattr(llm_client.adapter, "generate", post)
    path = tmp_path / "processing.log"
    with file_log_run(path, "extraction") as logger:
        assert llm_client.call_llm("prompt", ModelRequestOptions(model="test-model"), logger=logger) == "success"
    content = path.read_text()
    assert "transport timeout" in content and "retrying in 1s" in content
    assert content.count("--- MODEL PROMPT |") == 2


@pytest.mark.parametrize("streaming", [False, True])
def test_local_adapter_gets_shared_logging_validation_and_retries(tmp_path, streaming):
    path = tmp_path / "local-model.log"
    answers = ["```json\r\nnot JSON", "[]"]
    tokens = []

    class LocalModel:
        def __init__(self):
            self.prompts = []

        def generate(self, prompt, options, *, on_token=None):
            # Logging must happen before every inference attempt, including retries.
            assert path.read_bytes().endswith(prompt.encode() + b"\n")
            self.prompts.append(prompt)
            output = answers[len(self.prompts) - 1]
            if on_token is not None:
                on_token(output)
            return output

    adapter = LocalModel()
    with file_log_run(path, "local_inference", report_id=12) as logger:
        result = llm.ModelClient(adapter).call_llm_json(
            "Return relations", ModelRequestOptions("local", schema=RELATIONS_SCHEMA, max_retries=1),
            logger=logger, on_token=tokens.append if streaming else None,
        )
    assert result == []
    assert len(adapter.prompts) == 2
    assert "failed validation" in adapter.prompts[1]
    assert tokens == (answers if streaming else [])
    content = path.read_bytes()
    assert content.count(b"--- MODEL PROMPT |") == 2
    assert content.count(b"--- MODEL OUTPUT |") == 2
    assert b"JSON validation failed" in content
    assert b"report=12" in content
    assert all(answer.encode() in content for answer in answers)
