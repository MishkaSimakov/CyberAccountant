"""Structured-output transport, parsing, and truncation safeguards."""

import json
import logging
from processing.common.llm import (
    LLMError, ModelClient, ModelRequestOptions, ModelTransportError,
    StructuredOutputUnsupported, _json_from_text,
)

import jsonschema
import pytest

from infrastructure.llm import HttpModelAdapter
from infrastructure.llm import (
    _endpoint_for_url,
    _extract_text,
)

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["allowed"]}},
    "required": ["answer"],
    "additionalProperties": False,
}


@pytest.fixture
def http_adapter():
    return HttpModelAdapter("https://provider.example/v1", "test-key")


def test_base_url_gets_responses_endpoint():
    assert _endpoint_for_url("https://provider.example/v1/") == "https://provider.example/v1/responses"


@pytest.mark.parametrize("url", [
    "https://provider.example/v1/responses",
    "https://provider.example/v1/responses/",
    "https://provider.example/v1/chat/completions",
    "https://provider.example/v1/chat/completions/",
])
def test_full_endpoint_urls_are_rejected(url):
    with pytest.raises(LLMError, match="must be a base URL"):
        _endpoint_for_url(url)


def test_schema_uses_responses_text_format(monkeypatch, http_adapter):
    payloads = []

    def post(payload, timeout):
        payloads.append(payload)
        return {"output_text": '{"answer":"allowed"}'}

    monkeypatch.setattr(http_adapter, "_post", post)
    assert http_adapter.generate("Return an answer",
                                 ModelRequestOptions(model="model", schema=SCHEMA)) == '{"answer":"allowed"}'
    assert len(payloads) == 1
    assert "response_format" not in payloads[0]
    assert payloads[0]["reasoning"] == {"effort": "low"}
    assert payloads[0]["text"] == {
        "format": {
            "type": "json_schema",
            "name": "response",
            "schema": SCHEMA,
            "strict": True,
        }
    }


def test_schema_rejection_never_falls_back_to_plain_prompting(monkeypatch, http_adapter):
    payloads = []

    def reject(payload, timeout):
        payloads.append(payload)
        raise LLMError("API 400: text.format must contain a JSON schema")

    monkeypatch.setattr(http_adapter, "_post", reject)
    with pytest.raises(LLMError, match="API 400"):
        ModelClient(http_adapter).call_llm("Return an answer", ModelRequestOptions(model="model", schema=SCHEMA),
                                           logger=logging.getLogger(__name__))
    assert len(payloads) == 1
    assert "text" in payloads[0]


@pytest.mark.parametrize("text", [
    '{"answer":"allowed"}',
    '```json\n{"answer":"allowed"}\n```',
    '```json\n{"answer":"allowed"}',
])
def test_json_parser_accepts_complete_json_with_optional_fences(text):
    assert _json_from_text(text) == {"answer": "allowed"}


def test_json_parser_rejects_cropped_json():
    with pytest.raises(LLMError, match="not valid JSON"):
        _json_from_text('```json\n{"answer":"allowed"')


@pytest.mark.parametrize("response,match", [
    ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "truncated"),
    ({"choices": [{"finish_reason": "length", "message": {"content": '{"answer"'}}]}, "finish_reason"),
])
def test_nonstreaming_truncation_is_explicit(response, match):
    with pytest.raises(LLMError, match=match):
        _extract_text(response)


class StreamResponse:
    status_code = 200
    encoding = None

    def __init__(self, events):
        self.events = events

    def iter_lines(self, decode_unicode):
        for event in self.events:
            yield "data: " + json.dumps(event)
        yield "data: [DONE]"


def test_streaming_truncation_is_explicit(monkeypatch, http_adapter):
    monkeypatch.setattr("infrastructure.llm.requests.post", lambda *args, **kwargs: StreamResponse([
        {"type": "response.output_text.delta", "delta": '{"answer"'},
        {"type": "response.incomplete", "response": {
            "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        }},
    ]))
    with pytest.raises(LLMError, match="max_output_tokens"):
        http_adapter.generate("prompt", ModelRequestOptions("model", timeout=1), on_token=lambda token: None)


def test_schema_violation_is_validated_and_retried(monkeypatch, llm_client):
    responses = iter(['{"answer":42}', '{"answer":"allowed"}'])
    prompts = []

    def respond(prompt, options, **kwargs):
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr(llm_client.adapter, "generate", respond)
    result = llm_client.call_llm_json("Return an answer",
                                      ModelRequestOptions(model="model", schema=SCHEMA, max_retries=1),
                                      logger=logging.getLogger(__name__))
    assert result == {"answer": "allowed"}
    jsonschema.validate(result, SCHEMA)
    assert len(prompts) == 2
    assert "failed validation" in prompts[1]


def test_http_uses_injected_credentials_instead_of_environment(monkeypatch):
    monkeypatch.setenv("AI_API_URL", "https://wrong.example/v1")
    monkeypatch.setenv("AI_API_KEY", "wrong-key")
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"output_text": "answer"}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("infrastructure.llm.requests.post", post)
    first = HttpModelAdapter("https://first.example/v1", "first-key")
    second = HttpModelAdapter("https://second.example/v2", "second-key")
    for adapter in (first, second):
        assert adapter.generate("prompt", ModelRequestOptions("model", timeout=7)) == "answer"
    assert [url for url, _ in calls] == ["https://first.example/v1/responses", "https://second.example/v2/responses"]
    assert [kwargs["headers"]["Authorization"] for _, kwargs in calls] == ["Bearer first-key", "Bearer second-key"]
    assert all(kwargs["timeout"] == 7 for _, kwargs in calls)


@pytest.mark.parametrize("chat", [False, True])
def test_http_stream_preserves_tokens_and_logs_partial_transport_failures(tmp_path, monkeypatch, chat):
    import requests

    from app.logging import file_log_run

    partial = "Частичный ответ\r\n"
    calls = []
    tokens = []
    path = tmp_path / "stream.log"

    class InterruptedResponse:
        status_code = 200
        encoding = None

        def iter_lines(self, decode_unicode):
            yield 'data: {"type":"response.reasoning_text.delta","delta":"private"}'
            event = ({"choices": [{"delta": {"content": partial}}]} if chat else
                     {"type": "response.output_text.delta", "delta": partial})
            yield "data: " + json.dumps(event)
            raise requests.ConnectionError("stream lost")

    def post(url, **kwargs):
        calls.append(kwargs)
        return InterruptedResponse()

    monkeypatch.setattr("infrastructure.llm.requests.post", post)
    monkeypatch.setattr("processing.common.llm.time.sleep", lambda seconds: None)
    client = ModelClient(HttpModelAdapter("https://provider.example/v1", "key"))
    with pytest.raises(LLMError, match="failed after 4 attempts"):
        with file_log_run(path, "extraction") as logger:
            client.call_llm("prompt\r\n", ModelRequestOptions("model"), logger=logger, on_token=tokens.append)
    assert tokens == [partial] * 4
    assert all(call["stream"] is True and call["json"]["stream"] is True for call in calls)
    content = path.read_bytes()
    assert content.count(b"--- MODEL PROMPT |") == 4
    assert content.count(b"--- MODEL OUTPUT |") == 4
    assert content.count(partial.encode()) == 4
    assert b"private" not in content


@pytest.mark.parametrize("streaming", [False, True])
def test_http_adapter_reports_transient_failure_without_retrying(monkeypatch, http_adapter, streaming):
    import requests

    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        raise requests.Timeout("inference timeout")

    monkeypatch.setattr("infrastructure.llm.requests.post", post)
    with pytest.raises(ModelTransportError, match="inference timeout"):
        http_adapter.generate("prompt", ModelRequestOptions("model"),
                              on_token=(lambda token: None) if streaming else None)
    assert len(calls) == 1


def test_http_adapter_reports_unsupported_schema_without_retrying(monkeypatch, http_adapter):
    calls = []

    class Response:
        status_code = 400
        text = "response_format unsupported"

    def post(*args, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr("infrastructure.llm.requests.post", post)
    with pytest.raises(StructuredOutputUnsupported):
        http_adapter.generate("prompt", ModelRequestOptions("model", schema=SCHEMA))
    assert len(calls) == 1


@pytest.mark.parametrize("chat", [False, True])
def test_http_truncation_carries_full_partial_text_for_shared_logging(tmp_path, monkeypatch, http_adapter, chat):
    from app.logging import file_log_run

    partial = "Частичный ответ\r\n" * 4_000
    data = ({"choices": [{"finish_reason": "length", "message": {"content": partial}}]} if chat else
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output_text": partial})
    monkeypatch.setattr(http_adapter, "_post", lambda *args: data)
    path = tmp_path / "processing.log"
    with pytest.raises(LLMError, match="truncated") as failure:
        with file_log_run(path, "extraction") as logger:
            ModelClient(http_adapter).call_llm("prompt", ModelRequestOptions("model"), logger=logger)
    assert failure.value.partial_output == partial
    assert partial.encode() in path.read_bytes()
    assert path.read_bytes().count(b"--- MODEL OUTPUT |") == 1
