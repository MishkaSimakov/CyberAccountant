"""Keep readable reasoning in processing logs and out of parsed answers."""

import json

import pytest
import requests

from checkier import llm
from checkier.file_log import file_log_run
from checkier.schemas import RELATIONS_SCHEMA

TRACE = "Проверяем суммы\r\n" * 4_000


@pytest.mark.parametrize("streaming", [False, True])
def test_reasoning_transport_uses_chat_and_preserves_schema_and_logging(tmp_path, monkeypatch, streaming):
    monkeypatch.setattr(llm, "load_api_credentials", lambda: ("https://example.test/api/v1", "key"))
    payloads = []
    trace = "Check all the supporting documents.\n"
    answer = '[{"equation":"1 == 1","tag":"ref","text":"Test"}]'

    class Response:
        status_code = 200
        encoding = None

        def json(self):
            return {"choices": [{"message": {"content": answer, "reasoning": trace}, "finish_reason": "stop"}]}

        def iter_lines(self, decode_unicode):
            for delta in ({"reasoning": trace}, {"content": answer}):
                yield "data: " + json.dumps({"choices": [{"delta": delta}]})
            yield "data: [DONE]"

    def post(url, **kwargs):
        assert url == "https://example.test/api/v1/chat/completions"
        payloads.append(kwargs["json"])
        return Response()

    monkeypatch.setattr(llm.requests, "post", post)
    path = tmp_path / "processing.log"
    tokens = []
    with file_log_run(path, "requirements"):
        assert llm.call_llm_json("audit prompt", "test-model", reasoning="enabled",
                                 schema=RELATIONS_SCHEMA, schema_name="relations",
                                 on_token=tokens.append if streaming else None) == json.loads(answer)
    payload, = payloads
    assert payload["messages"] == [{"role": "user", "content": "audit prompt"}]
    assert payload["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "relations", "schema": RELATIONS_SCHEMA, "strict": True,
    }}
    assert payload["reasoning"] == {"enabled": True}
    assert payload["include_reasoning"] is True
    assert "input" not in payload and "text" not in payload
    if streaming:
        assert payload["stream"] is True
        assert tokens == [answer]
    content = path.read_text()
    assert content.count(trace) == 1
    assert content.index("--- MODEL REASONING |") < content.index("--- MODEL OUTPUT |")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("reasoning,expected", [
    ("enabled", {"reasoning": {"enabled": True}, "include_reasoning": True}),
    ("disabled", {"reasoning": {"effort": "none"}}),
    ("exclude", {}),
])
def test_reasoning_request_settings_are_sent_to_api(tmp_path, monkeypatch, streaming, reasoning, expected):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    payloads = []

    def respond(payload, timeout, *callbacks):
        payloads.append(payload)
        return "[]" if streaming else {"output_text": "[]"}

    monkeypatch.setattr(llm, "_post_streaming" if streaming else "_post", respond)
    assert llm.call_llm("prompt", "test-model", reasoning=reasoning,
                        on_token=(lambda token: None) if streaming else None) == "[]"
    assert payloads == [dict(model="test-model", input="prompt", **expected)]


def test_api_counting_reasoning_without_returning_text_is_explicit_in_log(tmp_path, monkeypatch):
    # RouterAI's reply to the report-9 replay counted reasoning tokens but
    # contained only a message item, with no readable reasoning item.
    response = {
        "id": "diagnostic-response",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "[]"}]}],
        "usage": {"output_tokens": 17703, "output_tokens_details": {"reasoning_tokens": 15930}},
    }
    monkeypatch.setattr(llm, "_post", lambda *args: response)
    path = tmp_path / "processing.log"
    with file_log_run(path, "requirements"):
        assert llm.call_llm_json("prompt", "test-model", reasoning="enabled") == []
    content = path.read_text()
    assert "--- MODEL REASONING |" in content
    assert "No readable reasoning returned by API" in content
    assert '"reasoning_tokens": 15930' in content
    assert "diagnostic-response" in content
    assert content.index("No readable reasoning") < content.index("--- MODEL OUTPUT |")


@pytest.mark.parametrize("reasoning", [
    {"output": [{"type": "reasoning", "content": [{"type": "reasoning_text", "text": TRACE}],
                 "summary": [{"type": "summary_text", "text": "Short summary"}]}]},
    {"output": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": TRACE}]}]},
    {"choices": [{"message": {"reasoning": TRACE, "reasoning_content": TRACE,
                              "reasoning_details": [{"type": "reasoning.text", "text": TRACE}]}}]},
    {"choices": [{"message": {"reasoning_content": TRACE}}]},
    {"choices": [{"message": {"reasoning_details": [{"type": "reasoning.text", "text": TRACE}]}}]},
    {"choices": [{"message": {"reasoning_details": [{"type": "reasoning.summary", "summary": TRACE}]}}]},
])
@pytest.mark.parametrize("stage", ["entity_extraction", "requirements"])
def test_reasoning_is_logged_once_without_changing_json(tmp_path, monkeypatch, reasoning, stage):
    monkeypatch.setattr(llm, "_post", lambda *args: dict(reasoning, output_text="[]"))
    path = tmp_path / "processing.log"
    with file_log_run(path, stage):
        assert llm.call_llm_json("prompt", "test-model", reasoning="enabled") == []
    content = path.read_bytes()
    assert content.count(TRACE.encode()) == 1
    assert content.count(b"--- MODEL REASONING |") == 1
    assert content.index(b"--- MODEL PROMPT |") < content.index(b"--- MODEL REASONING |")
    assert content.index(b"--- MODEL REASONING |") < content.index(b"--- MODEL OUTPUT |")
    assert b"model=test-model | schema=response" in content
    assert b"Short summary" not in content


class StreamResponse:
    status_code = 200
    encoding = None

    def __init__(self, events, error=None):
        self.events = events
        self.error = error

    def iter_lines(self, decode_unicode):
        for event in self.events:
            yield "data: " + json.dumps(event)
        if self.error:
            raise self.error
        yield "data: [DONE]"


def mock_stream(monkeypatch, events, error=None):
    monkeypatch.setattr(llm, "load_api_credentials", lambda: ("https://example.test/v1", "key"))
    monkeypatch.setattr(llm.requests, "post", lambda *args, **kwargs: StreamResponse(events, error))


@pytest.mark.parametrize("events", [
    [
        {"type": "response.reasoning_text.delta", "output_index": 0, "delta": TRACE[:15]},
        {"type": "response.reasoning_text.delta", "output_index": 0, "delta": TRACE[15:]},
        {"type": "response.reasoning_summary_text.delta", "output_index": 0, "delta": "Short summary"},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "reasoning", "content": [{"type": "reasoning_text", "text": TRACE}]}},
        {"type": "response.completed", "response": {"output": [
            {"type": "reasoning", "content": [{"type": "reasoning_text", "text": TRACE}]},
        ]}},
    ],
    [{"type": "response.reasoning_summary_text.delta", "delta": TRACE}],
    [{"type": "response.output_item.done", "item": {
        "type": "reasoning", "content": [{"type": "reasoning_text", "text": TRACE}],
    }}],
    [{"type": "response.completed", "response": {"output": [
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": TRACE}]},
    ]}}],
    [{"choices": [{"delta": {"reasoning": TRACE, "reasoning_details": [
        {"type": "reasoning.text", "text": TRACE},
    ]}}]}],
    [{"choices": [{"delta": {"reasoning_content": TRACE}}]}],
    [{"choices": [{"delta": {"reasoning_details": [{"type": "reasoning.text", "text": TRACE}]}}]}],
    [
        {"type": "response.reasoning_text.delta", "output_index": 0, "delta": TRACE[:15]},
        {"type": "response.output_item.done", "output_index": 0, "item": {
            "type": "reasoning", "content": [{"type": "reasoning_text", "text": TRACE[:15]}],
        }},
        {"type": "response.reasoning_text.delta", "output_index": 2, "delta": TRACE[15:]},
    ],
])
def test_streaming_reasoning_is_logged_without_duplicates_or_answer_leaks(tmp_path, monkeypatch, events):
    mock_stream(monkeypatch, events + [{"type": "response.output_text.delta", "delta": "[]"}])
    path = tmp_path / "processing.log"
    tokens = []
    with file_log_run(path, "requirements"):
        assert llm.call_llm_json("prompt", "test-model", on_token=tokens.append, reasoning="enabled") == []
    assert tokens == ["[]"]
    content = path.read_bytes()
    assert content.count(TRACE.encode()) == 1
    assert content.count(b"--- MODEL REASONING |") == 1
    assert b"Short summary" not in content


@pytest.mark.parametrize("streaming", [False, True])
def test_truncated_response_keeps_reasoning(tmp_path, monkeypatch, streaming):
    response = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                "output": [{"type": "reasoning", "content": [{"text": TRACE}]}]}
    if streaming:
        mock_stream(monkeypatch, [{"type": "response.incomplete", "response": response}])
    else:
        monkeypatch.setattr(llm, "_post", lambda *args: response)
    path = tmp_path / "processing.log"
    with pytest.raises(llm.LLMError, match="max_output_tokens"):
        with file_log_run(path, "requirements"):
            llm.call_llm("prompt", "test-model", on_token=(lambda token: None) if streaming else None)
    content = path.read_bytes()
    assert content.count(TRACE.encode()) == 1
    assert content.index(b"--- MODEL REASONING |") < content.index(b"ERROR (LLMError)")


def test_interrupted_stream_preserves_reasoning_for_every_attempt(tmp_path, monkeypatch):
    mock_stream(monkeypatch, [{"type": "response.reasoning_text.delta", "delta": TRACE}],
                requests.ConnectionError("Connection reset"))
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)
    path = tmp_path / "processing.log"
    with pytest.raises(llm.LLMError, match="failed after"):
        with file_log_run(path, "requirements"):
            llm.call_llm("prompt", "test-model", on_token=lambda token: None)
    content = path.read_bytes()
    assert content.count(TRACE.encode()) == llm.TRANSPORT_RETRIES + 1
    assert content.count(b"--- MODEL PROMPT |") == llm.TRANSPORT_RETRIES + 1


def test_absent_or_encrypted_reasoning_has_no_empty_log_entry(tmp_path, monkeypatch):
    responses = iter([
        {"output_text": "[]"},
        {"output_text": "[]", "output": [{"type": "reasoning", "encrypted_content": "opaque"}]},
    ])
    monkeypatch.setattr(llm, "_post", lambda *args: next(responses))
    path = tmp_path / "processing.log"
    with file_log_run(path, "requirements"):
        llm.call_llm("prompt", "test-model")
        llm.call_llm("prompt", "test-model")
    assert "MODEL REASONING" not in path.read_text()


def test_validation_retries_keep_each_reasoning_trace(tmp_path, monkeypatch):
    responses = iter([
        {"output_text": "invalid JSON", "output": [{"type": "reasoning", "content": [{"text": "First trace"}]}]},
        {"output_text": "[]", "output": [{"type": "reasoning", "content": [{"text": "Second trace"}]}]},
    ])
    monkeypatch.setattr(llm, "_post", lambda *args: next(responses))
    path = tmp_path / "processing.log"
    with file_log_run(path, "requirements"):
        assert llm.call_llm_json("prompt", "test-model") == []
    content = path.read_text()
    assert content.count("--- MODEL REASONING |") == 2
    assert content.index("First trace") < content.index("invalid JSON") < content.index("Second trace")
