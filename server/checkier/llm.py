"""OpenAI-compatible Responses client with structured output and retries."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from uuid import uuid4

import jsonschema
import requests

from checkier.config import load_api_credentials
from checkier.file_log import append_log, current_log_path

logger = logging.getLogger(__name__)


def log_model_prompt(prompt: str, model: str, schema_name: str) -> None:
    """Append the exact prompt before sending each request, including retries."""
    if path := current_log_path.get():
        timestamp = datetime.now(timezone.utc).isoformat()
        append_log(path, f"\n--- MODEL PROMPT | {timestamp} | model={model} | schema={schema_name} ---\n{prompt}\n")


def log_model_output(text: str, model: str, schema_name: str) -> None:
    """Keep the exact model text, including invalid JSON and Markdown fences."""
    if path := current_log_path.get():
        timestamp = datetime.now(timezone.utc).isoformat()
        append_log(path, f"\n--- MODEL OUTPUT | {timestamp} | model={model} | schema={schema_name} ---\n{text}\n")
        return
    root = Path(os.environ.get("STORAGE_ROOT", Path(__file__).resolve().parents[2] / "storage"))
    directory = root / "llm_outputs"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = directory / f"{timestamp}-{uuid4().hex}.txt"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    except OSError:
        logger.exception("Could not save raw LLM output to %s", path)
    else:
        logger.info("Saved raw LLM output to %s (model=%s, schema=%s)", path, model, schema_name)


class LLMError(RuntimeError):
    pass


# Extra attempts for transient transport failures (connection reset, timeout).
TRANSPORT_RETRIES = 3


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
    _raise_if_incomplete(data)
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


def _post(payload: dict, timeout: float) -> dict:
    base_url, key = load_api_credentials()
    url = _endpoint_for_url(base_url)
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise LLMError(f"API {resp.status_code}: {resp.text[:500]}")
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
    base_url, key = load_api_credentials()
    url = _endpoint_for_url(base_url)
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
        raise LLMError(f"API {resp.status_code}: {resp.text[:500]}")
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


def call_llm(
        prompt: str,
        model: str,
        schema: dict | None = None,
        schema_name: str = "response",
        timeout: float = 300.0,
        on_token: Callable[[str], None] | None = None,
        reasoning: str = "disabled",
) -> str:
    """Single LLM call with optional strict JSON Schema structured output.

    Schema rejection is surfaced to the caller; silently switching to an
    unconstrained request would make structured-output guarantees meaningless.
    Streams tokens to on_token when provided.

    reasoning: "disabled" requests minimum effort ("low"), "enabled" uses
    the model default, and "exclude" hides reasoning from parsed output.
    """
    if schema is not None and not isinstance(schema, dict):
        raise LLMError("schema must be a JSON Schema object, not natural language")
    payload: dict = {"model": model, "input": prompt}
    # Portable APIs do not guarantee that reasoning can be fully disabled.
    if reasoning == "disabled":
        payload["reasoning"] = {"effort": "low"}
    if schema is not None:
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": schema,
                "strict": True,
            }
        }

    def request_text() -> str:
        log_model_prompt(prompt, model, schema_name)
        if on_token is None:
            return _extract_text(_post(payload, timeout))
        chunks: list[str] = []

        def track(token: str) -> None:
            chunks.append(token)
            on_token(token)

        try:
            return _post_streaming(payload, timeout, track)
        except Exception:
            if chunks:
                log_model_output("".join(chunks), model, schema_name)
            raise

    def request_text_resilient() -> str:
        """Retry transient transport failures (connection reset, timeout).

        Long non-streaming requests sit idle while the model generates and are
        routinely dropped by the provider or middleboxes; these are safe to
        retry because a reset means no response was produced."""
        transport_errors = (requests.ConnectionError, requests.Timeout)
        for attempt in range(TRANSPORT_RETRIES + 1):
            try:
                return request_text()
            except transport_errors as exc:
                if attempt == TRANSPORT_RETRIES:
                    raise LLMError(
                        f"LLM request failed after {TRANSPORT_RETRIES + 1} attempts: {exc}"
                    ) from exc
                delay = 2 ** attempt
                logger.warning(
                    "LLM request failed (%s); retrying in %ss (attempt %d/%d)",
                    exc, delay, attempt + 1, TRANSPORT_RETRIES + 1,
                )
                time.sleep(delay)
        raise AssertionError("unreachable")

    try:
        text = request_text_resilient()
    except LLMError as exc:
        # Some endpoints/models reject response_format; retry without it.
        if schema is None or "response_format" not in str(exc):
            raise
        payload.pop("text", None)
        text = request_text_resilient()
    log_model_output(text, model, schema_name)
    return text


def _json_from_text(text: str) -> dict | list:
    """Parse JSON with optional surrounding prose or (possibly unclosed) fences."""
    text = text.strip()
    # Strip Markdown fences if present, including a missing closing fence.
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    else:
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1)
        text = re.sub(r"\s*```$", "", text, count=1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Decode the outermost container; never salvage an item from a broken array.
    match = re.search(r"[\[{]", text)
    if match:
        try:
            value, _ = json.JSONDecoder().raw_decode(text, match.start())
            return value
        except json.JSONDecodeError:
            pass
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
