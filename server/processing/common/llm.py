"""Model adapters and shared logging, retry, and JSON validation behavior."""

import json
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from logging import Logger, LoggerAdapter
from typing import Callable, Protocol

import jsonschema

from .errors import ProcessingError

LoggerLike = Logger | LoggerAdapter
TokenCallback = Callable[[str], None]


class LLMError(ProcessingError):
    """Model transport, response, or validation failed, possibly after output."""

    def __init__(self, message: str, *, partial_output: str | None = None):
        super().__init__(message)
        self.partial_output = partial_output


class ModelTransportError(LLMError):
    """A transient adapter failure that can be retried."""


class StructuredOutputUnsupported(LLMError):
    """The adapter cannot apply the requested output schema."""


@dataclass(frozen=True)
class ModelRequestOptions:
    model: str
    schema: dict | None = None
    schema_name: str = "response"
    timeout: float = 300.0
    max_retries: int = 2
    reasoning: str = "disabled"


class ModelAdapter(Protocol):
    def generate(self, prompt: str, options: ModelRequestOptions, *,
                 on_token: TokenCallback | None = None) -> str:
        """Perform one inference attempt, reporting surfaced text only.

        Failures with non-streamed partial text carry it in LLMError.partial_output.
        Transient failures raise ModelTransportError; unsupported schemas raise
        StructuredOutputUnsupported. Logging and retry policy belong to ModelClient.
        """
        ...


def log_model_prompt(prompt: str, model: str, schema_name: str, logger: LoggerLike) -> None:
    """Record the exact prompt before every request, including retries."""
    logger.info("\n--- MODEL PROMPT | %s | model=%s | schema=%s ---\n%s",
                datetime.now(timezone.utc).isoformat(), model, schema_name, prompt)


def log_model_output(text: str, model: str, schema_name: str, logger: LoggerLike) -> None:
    """Record exact model text, including invalid JSON and Markdown fences."""
    logger.info("\n--- MODEL OUTPUT | %s | model=%s | schema=%s ---\n%s",
                datetime.now(timezone.utc).isoformat(), model, schema_name, text)


# Extra attempts for transient transport failures (connection reset, timeout).
TRANSPORT_RETRIES = 3


class ModelClient:
    """Apply the same observability and validation policy to every adapter."""

    def __init__(self, adapter: ModelAdapter):
        self.adapter = adapter

    def call_llm(self, prompt: str, options: ModelRequestOptions, *,
                 logger: LoggerLike, on_token: TokenCallback | None = None) -> str:
        """Return exact surfaced text, with retries and structured-output fallback."""

        def request_text(request_options: ModelRequestOptions) -> str:
            log_model_prompt(prompt, options.model, options.schema_name, logger)
            chunks: list[str] = []

            def track(token: str) -> None:
                chunks.append(token)
                on_token(token)

            try:
                text = self.adapter.generate(prompt, request_options,
                                             on_token=track if on_token is not None else None)
            except Exception as error:
                partial = "".join(chunks) if chunks else getattr(error, "partial_output", None)
                if partial is not None:
                    log_model_output(partial, options.model, options.schema_name, logger)
                raise
            log_model_output(text, options.model, options.schema_name, logger)
            return text

        def request_text_resilient(request_options: ModelRequestOptions) -> str:
            for attempt in range(TRANSPORT_RETRIES + 1):
                try:
                    return request_text(request_options)
                except ModelTransportError as error:
                    if attempt == TRANSPORT_RETRIES:
                        raise LLMError(
                            f"LLM request failed after {TRANSPORT_RETRIES + 1} attempts: {error}"
                        ) from error
                    delay = 2 ** attempt
                    logger.warning(
                        "LLM request failed (%s); retrying in %ss (attempt %d/%d)",
                        error, delay, attempt + 1, TRANSPORT_RETRIES + 1,
                    )
                    time.sleep(delay)
            raise AssertionError("unreachable")

        try:
            return request_text_resilient(options)
        except StructuredOutputUnsupported as error:
            if options.schema is None:
                raise
            logger.warning("Schema rejected; retrying without structured output: %s", error)
            return request_text_resilient(replace(options, schema=None))

    def call_llm_json(
            self, prompt: str, options: ModelRequestOptions, *,
            logger: LoggerLike,
            on_token: TokenCallback | None = None,
    ) -> dict | list:
        """Log exact output before parsing and retry with validation feedback."""
        schema, max_retries = options.schema, options.max_retries
        attempt_prompt = prompt
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            text = self.call_llm(attempt_prompt, options, on_token=on_token, logger=logger)
            try:
                data = _json_from_text(text)
                if schema is not None:
                    jsonschema.validate(data, schema)
                return data
            except (LLMError, jsonschema.ValidationError) as exc:
                last_error = exc
                logger.warning("JSON validation failed (attempt %d/%d): %s", attempt + 1, max_retries + 1, exc)
                if attempt < max_retries:
                    attempt_prompt = (
                            prompt
                            + "\n\nYour previous answer failed validation:\n"
                            + str(exc)[:2000]
                            + "\nReturn ONLY corrected JSON."
                    )
        raise LLMError(f"LLM JSON validation failed after retries: {last_error}")


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
