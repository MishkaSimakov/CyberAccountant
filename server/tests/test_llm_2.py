"""Structured-output transport, parsing, and truncation safeguards."""

import json
from typing import Any, cast

import jsonschema
import pytest

from checkier.llm import (
    LLMError,
    _endpoint_for_url,
    _extract_text,
    _json_from_text,
    _post_streaming,
    call_llm,
    call_llm_json,
)


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["allowed"]}},
    "required": ["answer"],
    "additionalProperties": False,
}


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


def test_schema_uses_responses_text_format(monkeypatch):
    payloads = []

    def post(payload, timeout):
        payloads.append(payload)
        return {"output_text": '{"answer":"allowed"}'}

    monkeypatch.setattr("checkier.llm._post", post)
    assert call_llm("Return an answer", "model", schema=SCHEMA) == '{"answer":"allowed"}'
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


def test_natural_language_schema_is_rejected_before_transport(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Natural-language schema must not reach the provider")

    monkeypatch.setattr("checkier.llm._post", unexpected)
    with pytest.raises(LLMError, match="JSON Schema object"):
        call_llm("Return an answer", "model", schema=cast(Any, "Return any JSON object"))


def test_schema_rejection_never_falls_back_to_plain_prompting(monkeypatch):
    payloads = []

    def reject(payload, timeout):
        payloads.append(payload)
        raise LLMError("API 400: text.format must contain a JSON schema")

    monkeypatch.setattr("checkier.llm._post", reject)
    with pytest.raises(LLMError, match="API 400"):
        call_llm("Return an answer", "model", schema=SCHEMA)
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


def test_streaming_truncation_is_explicit(monkeypatch):
    monkeypatch.setattr("checkier.llm.load_api_credentials", lambda: ("https://example.test", "key"))
    monkeypatch.setattr("checkier.llm.requests.post", lambda *args, **kwargs: StreamResponse([
        {"type": "response.output_text.delta", "delta": '{"answer"'},
        {"type": "response.incomplete", "response": {
            "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        }},
    ]))
    with pytest.raises(LLMError, match="max_output_tokens"):
        _post_streaming({"model": "model"}, timeout=1)


def test_schema_violation_is_validated_and_retried(monkeypatch):
    responses = iter(['{"answer":42}', '{"answer":"allowed"}'])
    prompts = []

    def respond(prompt, _model, **kwargs):
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr("checkier.llm.call_llm", respond)
    result = call_llm_json("Return an answer", "model", schema=SCHEMA, max_retries=1)
    assert result == {"answer": "allowed"}
    jsonschema.validate(result, SCHEMA)
    assert len(prompts) == 2
    assert "failed validation" in prompts[1]
