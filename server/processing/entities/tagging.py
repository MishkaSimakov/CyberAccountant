"""In-memory model tagging with source reconstruction and retry validation."""

from html import escape
from typing import Callable

from processing.common.llm import LoggerLike, ModelClient, ModelRequestOptions
from .markup import validate_markup
from .options import EntityOptions
from .prompts import _build_prompt
from .schemas import TAGGING_SCHEMA

def _coerce_segments(data) -> list:
    """Accept either {"segments": [...]} or a bare [...] and return the list."""
    if isinstance(data, dict) and "segments" in data:
        return data["segments"]
    if isinstance(data, list):
        return data
    raise ValueError(f"unexpected JSON shape: {type(data).__name__}")


def _validate_segments(doc: str, segments: list) -> list[str]:
    """Check the segment stream rebuilds *doc* exactly. Returns problems."""
    problems: list[str] = []
    rebuilt_parts: list[str] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            problems.append(f"segment {i} is not an object")
            continue
        c = seg.get("content", "")
        w = seg.get("wrap", "")
        t = seg.get("type", "")
        if not isinstance(c, str) or not isinstance(w, str):
            problems.append(f"segment {i}: content/wrap must be strings")
            continue
        rebuilt_parts.append(c)
        rebuilt_parts.append(w)
        if i < len(segments) - 1:
            if not w:
                problems.append(f"segment {i}: non-final wrap is empty")
            if t in ("EOF", ""):
                problems.append(f"segment {i}: EOF before end of array")
        else:
            if t != "EOF" or w != "":
                problems.append(f"last segment must be EOF with empty wrap, got type={t!r}")
    rebuilt = "".join(rebuilt_parts)
    if rebuilt != doc:
        # locate divergence
        k = 0
        limit = min(len(rebuilt), len(doc))
        while k < limit and rebuilt[k] == doc[k]:
            k += 1
        problems.append(
            f"rebuilt doc diverges at char {k}: "
            f"expected {doc[k:k + 40]!r}, got {rebuilt[k:k + 40]!r}"
        )
    return problems


def _segments_to_tagged(segments: list) -> str:
    """Render the segment stream as <ent ...> tagged markdown."""
    out: list[str] = []
    for seg in segments:
        if seg.get("type") == "EOF":
            out.append(seg.get("content", ""))
            continue
        attrs = f't="{seg["type"]}"'
        if seg.get("unit"):
            attrs += f' u="{escape(seg["unit"], quote=True)}"'
        if seg.get("norm") and seg["norm"] not in (None, "", "1"):
            attrs += f' n="{escape(seg["norm"], quote=True)}"'
        out.append(seg.get("content", ""))
        out.append(f"<ent {attrs}>{seg.get('wrap', '')}</ent>")
    return "".join(out)


def _is_transient(exc: Exception) -> bool:
    """Network/5xx errors are worth retrying; schema/validation errors are not."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(
        s in name or s in text
        for s in ("ssl", "timeout", "connection", "5", "rate limit", "temporar")
    )


def tag_document(
        options: EntityOptions,
        filename: str,
        markdown: str,
        on_token: Callable[[str], None] | None = None,
        *, client: ModelClient,
        logger: LoggerLike,
) -> tuple[str, int]:
    """Extract entries from one document without filesystem access.

    Return (tagged_markdown, retries_used), or raise when extraction fails.
    The caller owns persistence and scheduling.
    """
    content = markdown
    prompt = _build_prompt(filename, content)
    last_err: Exception | None = None
    for attempt in range(options.max_retries + 1):
        try:
            data = client.call_llm_json(
                prompt,
                options=ModelRequestOptions(
                    model=options.tag_model,
                    schema=TAGGING_SCHEMA,
                    schema_name="tagged_document",
                    timeout=options.request_timeout,
                    reasoning=options.reasoning,
                ),
                on_token=on_token,
                logger=logger,
            )
            segments = _coerce_segments(data)
            problems = _validate_segments(content, segments)
            if problems:
                raise ValueError("; ".join(problems))
            tagged = _segments_to_tagged(segments)
            validate_markup(tagged)
            return tagged, attempt
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("Tagging failed for %s (attempt %d/%d): %s", filename, attempt + 1, options.max_retries + 1, exc)
            if attempt < options.max_retries and _is_transient(exc):
                continue
            if attempt < options.max_retries:
                prompt = (
                        _build_prompt(filename, content)
                        + "\n\nYour previous answer was wrong. Common mistake: repeating the "
                          "document text instead of outputting the JSON segment array. "
                          "You MUST output ONLY a JSON array of objects like "
                          '[{"content":"...","wrap":"...","type":"..."}]. No prose, no document.\n'
                          "Validation error from previous attempt:\n"
                        + str(last_err)[:1500]
                )
            else:
                break
    raise RuntimeError(f"tagging failed for {filename}: {last_err}")


