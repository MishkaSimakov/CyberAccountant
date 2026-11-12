"""Thin OpenRouter client with JSON-schema structured output, streaming and retries."""

from __future__ import annotations

import json
import re
from typing import Callable

import jsonschema
import requests

from checkier.config import load_api_credentials


class LLMError(RuntimeError):
    """LLM call failure. ``status_code`` is set for HTTP error responses."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _extract_text(data: dict) -> str:
    """Tolerantly pull the assistant text out of Responses- or Chat-shaped replies.

    Reasoning/thinking blocks are skipped: only ``output_text`` / message
    content is returned, never chain-of-thought."""
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


def _post(payload: dict, timeout: float) -> dict:
    url, key = load_api_credentials()
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise LLMError(
            f"API {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )
    return resp.json()


def _post_streaming(
    payload: dict,
    timeout: float,
    on_token: Callable[[str], None] | None = None,
) -> str:
    """POST with stream=true; accumulate text deltas; optionally report each.

    Handles both the Responses API (``response.output_text.delta``) and Chat
    Completions (``choices[].delta.content``) streaming shapes.
    """
    url, key = load_api_credentials()
    payload = dict(payload)
    payload["stream"] = True
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=timeout,
        stream=True,
    )
    if resp.status_code != 200:
        raise LLMError(
            f"API {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )
    resp.encoding = "utf-8"  # force UTF-8; default guess mangles Cyrillic
    chunks: list[str] = []
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
            d = (choice.get("delta") or {}).get("content")
            if isinstance(d, str):
                chunks.append(d)
                if on_token:
                    on_token(d)
    return "".join(chunks)


def call_llm(
    prompt: str,
    model: str,
    schema: dict | None = None,
    schema_name: str = "response",
    timeout: float = 300.0,
    on_token: Callable[[str], None] | None = None,
    reasoning: str = "disabled",
) -> str:
    """Single LLM call. If *schema* is given, request structured JSON output
    with strict mode; transparently falls back to plain prompting when the
    endpoint rejects the structured-output field. Streams tokens to on_token
    when provided.

    reasoning: "disabled" (default, no thinking tokens — fast/cheap),
    "enabled" (model may reason), or "exclude" (reason but strip from output).
    """
    payload: dict = {"model": model, "input": prompt}
    # Control reasoning/thinking token generation. "disabled" avoids the
    # latency + token cost of chain-of-thought for structured tasks.
    if reasoning == "disabled":
        payload["reasoning"] = {"enabled": False}
    elif reasoning == "exclude":
        payload["reasoning"] = {"exclude": True}
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        }
    try:
        if on_token is not None:
            return _post_streaming(payload, timeout, on_token)
        data = _post(payload, timeout)
        return _extract_text(data)
    except LLMError as exc:
        # Some endpoints/models reject response_format; retry without it.
        if schema is None or "response_format" not in str(exc):
            raise
        payload.pop("response_format")
        if on_token is not None:
            return _post_streaming(payload, timeout, on_token)
        data = _post(payload, timeout)
        return _extract_text(data)


def json_from_text(text: str) -> dict | list:
    """Extract and parse the first JSON object/array in *text*."""
    text = text.strip()
    # strip markdown fences if present
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fall back: first balanced {..} or [..]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"model output is not valid JSON: {text[:500]}")


def call_llm_json(
    prompt: str,
    model: str,
    schema: dict | None = None,
    schema_name: str = "response",
    timeout: float = 300.0,
    max_retries: int = 2,
    on_token: Callable[[str], None] | None = None,
    reasoning: str = "disabled",
) -> dict | list:
    """Call the LLM and validate the result against *schema* (if given),
    retrying with the validation error fed back."""
    attempt_prompt = prompt
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        text = call_llm(
            attempt_prompt, model, schema=schema, schema_name=schema_name,
            timeout=timeout, on_token=on_token, reasoning=reasoning,
        )
        try:
            data = json_from_text(text)
            if schema is not None:
                jsonschema.validate(data, schema)
            return data
        except (LLMError, jsonschema.ValidationError) as exc:
            last_error = exc
            if attempt < max_retries:
                attempt_prompt = (
                    prompt
                    + "\n\nYour previous answer failed validation:\n"
                    + str(exc)[:2000]
                    + "\nReturn ONLY corrected JSON."
                )
    raise LLMError(f"LLM JSON validation failed after retries: {last_error}")
