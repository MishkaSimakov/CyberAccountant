"""Single-attempt OpenAI-compatible Responses HTTP inference adapter."""

from __future__ import annotations

import json
from urllib.parse import urlparse

import requests

from processing.common.llm import (
    LLMError, ModelRequestOptions, ModelTransportError,
    StructuredOutputUnsupported, TokenCallback,
)


def _raise_if_incomplete(data: dict) -> None:
    """Reject truncated responses before their partial text reaches the parser."""
    if data.get("status") == "incomplete" or data.get("incomplete_details"):
        details = data.get("incomplete_details") or {}
        raise LLMError(f"model output is incomplete/truncated: {details}")
    for choice in data.get("choices") or []:
        if choice.get("finish_reason") == "length":
            raise LLMError("model output is truncated: finish_reason='length'")


def _extract_text(data: dict) -> str:
    """Tolerantly pull the assistant text out of Responses- or Chat-shaped replies.

    Reasoning/thinking blocks are skipped: only ``output_text`` / message
    content is returned, never chain-of-thought."""
    try:
        _raise_if_incomplete(data)
        return _message_text(data)
    except LLMError as error:
        try:
            partial = _message_text(data)
        except LLMError:
            partial = json.dumps(data, ensure_ascii=False)
        raise LLMError(str(error), partial_output=partial) from error


def _message_text(data: dict) -> str:
    """Extract surfaced text even from a truncated reply, for failure logging."""
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    # OpenAI Responses API: {"output": [{"type": "message", "content": [...]}]}
    for item in data.get("output") or []:
        # skip reasoning items entirely
        if item.get("type") == "reasoning":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                return content["text"]
    # Chat completions shape
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        if isinstance(message.get("content"), str):
            return message["content"]
    raise LLMError(f"could not extract text from response: {str(data)[:500]}")


def _endpoint_for_url(base_url: str) -> str:
    """Append the Responses endpoint to a versioned API base URL."""
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    if path.endswith(("/responses", "/chat/completions")):
        raise LLMError(
            "AI_API_URL must be a base URL such as https://api.example.com/v1, "
            "not a /responses or /chat/completions endpoint"
        )
    return parsed._replace(path=f"{path}/responses").geturl()


class HttpModelAdapter:
    """Translate one inference attempt to HTTP; the shared client owns policy."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def generate(self, prompt: str, options: ModelRequestOptions, *,
                 on_token: TokenCallback | None = None) -> str:
        payload: dict = {"model": options.model, "input": prompt}
        if options.reasoning == "disabled":
            payload["reasoning"] = {"effort": "low"}
        if options.schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": options.schema_name,
                    "schema": options.schema,
                    "strict": True,
                }
            }
        try:
            if on_token is None:
                return _extract_text(self._post(payload, options.timeout))
            return self._post_streaming(payload, options.timeout, on_token)
        except (requests.ConnectionError, requests.Timeout) as error:
            raise ModelTransportError(str(error)) from error

    @staticmethod
    def _check_response(response) -> None:
        if response.status_code != 200:
            message = f"API {response.status_code}: {response.text[:500]}"
            if "response_format" in response.text:
                raise StructuredOutputUnsupported(message)
            raise LLMError(message)

    def _post(self, payload: dict, timeout: float) -> dict:
        url = _endpoint_for_url(self.base_url)
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=timeout,
        )
        self._check_response(resp)
        return resp.json()

    def _post_streaming(
            self,
            payload: dict,
            timeout: float,
            on_token: TokenCallback | None = None,
    ) -> str:
        """POST with stream=true; accumulate text deltas; optionally report each.

        Handles both the Responses API (``response.output_text.delta``) and Chat
        Completions (``choices[].delta.content``) streaming shapes.
        """
        url = _endpoint_for_url(self.base_url)
        payload = dict(payload)
        payload["stream"] = True
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=timeout,
            stream=True,
        )
        self._check_response(resp)
        resp.encoding = "utf-8"  # force UTF-8; default guess mangles Cyrillic
        chunks: list[str] = []
        incomplete: str | None = None
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            data_str = raw[len("data:"):].strip()
            if data_str == "[DONE]":
                break
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            # Skip reasoning/thinking deltas — only surface final answer text.
            etype = event.get("type", "")
            response = event.get("response") or {}
            if etype == "response.incomplete" or response.get("status") == "incomplete":
                incomplete = str(response.get("incomplete_details") or event.get("error") or {})
            if "reasoning" in etype:
                continue
            # Responses API streaming delta
            delta = event.get("delta", "")
            if etype == "response.output_text.delta" and isinstance(delta, str):
                chunks.append(delta)
                if on_token:
                    on_token(delta)
            # Chat completions streaming delta
            for choice in event.get("choices", []):
                if choice.get("finish_reason") == "length":
                    incomplete = "finish_reason='length'"
                d = (choice.get("delta") or {}).get("content")
                if isinstance(d, str):
                    chunks.append(d)
                    if on_token:
                        on_token(d)
        if incomplete is not None:
            raise LLMError(f"model output is incomplete/truncated: {incomplete}")
        return "".join(chunks)
