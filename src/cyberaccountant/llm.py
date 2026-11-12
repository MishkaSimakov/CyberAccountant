"""Thin OpenRouter client with JSON-schema structured output and retries."""

from __future__ import annotations

import json
import re

import jsonschema
import requests

from cyberaccountant.config import load_api_credentials


class LLMError(RuntimeError):
    pass


def _extract_text(data: dict) -> str:
    """Tolerantly pull the assistant text out of Responses- or Chat-shaped replies."""
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    # OpenAI Responses API: {"output": [{"content": [{"type": "output_text", "text": ...}]}]}
    for item in data.get("output") or []:
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
        raise LLMError(f"API {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def call_llm(
    prompt: str,
    model: str,
    schema: dict | None = None,
    schema_name: str = "response",
    timeout: float = 300.0,
) -> str:
    """Single LLM call. If *schema* is given, request structured JSON output;
    transparently falls back to plain prompting when the endpoint rejects the
    structured-output field."""
    payload: dict = {"model": model, "input": prompt}
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema},
        }
    try:
        data = _post(payload, timeout)
    except LLMError as exc:
        # Some endpoints/models reject response_format; retry without it.
        if schema is None or "response_format" not in str(exc):
            raise
        payload.pop("response_format")
        data = _post(payload, timeout)
    return _extract_text(data)


def _json_from_text(text: str) -> dict | list:
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
) -> dict | list:
    """Call the LLM and validate the result against *schema* (if given),
    retrying with the validation error fed back."""
    attempt_prompt = prompt
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        text = call_llm(
            attempt_prompt, model, schema=schema, schema_name=schema_name, timeout=timeout
        )
        try:
            data = _json_from_text(text)
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
