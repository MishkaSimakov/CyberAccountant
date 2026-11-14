"""Stage 1: two-request entity wrapping and metadata extraction.

Request 1 returns only a segment stream that reconstructs the processed
filename header and OCR document exactly. Request 2 receives deterministic
per-document IDs in XML-like markup and returns closed, ID-keyed metadata under
an on-the-fly JSON Schema. Dates use inclusive, timezone-free day timestamps.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from typing import Callable

from checkier.config import PipelineConfig
from checkier.llm import call_llm_json
from checkier.schemas import TAG_TYPES, WRAPPING_SCHEMA, entity_metadata_schema


@dataclass
class TagProgress:
    """Per-file tagging outcome, reported to the progress callback."""

    file: str  # obfuscated h8
    original: str
    ok: bool
    entities: int = 0
    retries: int = 0
    error: str | None = None


@dataclass
class TagStats:
    """Running totals across all files."""

    total: int = 0
    done: int = 0
    ok: int = 0
    failed: int = 0
    entities: int = 0
    retries: int = 0
    per_file: list[TagProgress] = field(default_factory=list)


TagCallback = Callable[[TagProgress, TagStats], None]

DATE_FORMAT_EXAMPLES = [
    "10.05.2026 or 10.05.26",
    "2026-05-10",
    "10 мая 2026 or 10 мая 2026 г.",
    '"10" мая 2026 or «10» мая 2026 (including the quotation marks)',
    "Май 2026 or мая 2026",
    "2 квартал 2026",
    "2026 год when it is used as a period label",
]
DATE_FORMAT_EXAMPLES_TEXT = "\n".join(f"- {example}" for example in DATE_FORMAT_EXAMPLES)

TAG_PROMPT = """\
You identify numerical entities in accounting documents. This request performs
WRAPPING ONLY: do not classify entities and do not normalize their values.

DATE / PERIOD FORMATS
Wrap each complete expression as ONE entity:
%%DATE_FORMAT_EXAMPLES%%

A quoted day + month + year + optional "г." is ONE date, never a separate
number and month/year entity. For a range such as 01.05.2026–10.05.2026,
wrap the two dates separately and leave the dash outside the tags.

TASK
Read the processed filename header and document at the bottom. Output a JSON
array of segments that rebuilds both exactly.

REQUIRED OUTPUT FORMAT (no other text, no markdown fence, no commentary):
[{"content":"...","wrap":"...","type":"entity"}, ..., {"content":"...","wrap":"","type":"EOF"}]

Each entity entry contains the text before the entity in "content" and the exact
entity text in "wrap". The final entry contains all remaining text, an empty
"wrap", and type "EOF". No unit, norm, value, entity-class, start, or end fields
are allowed in this request.

Concatenating every "content" then its "wrap" (in array order) MUST give back
the input character-for-character.

Rules:
- Wrap every amount, date/period, document identifier, contract number, count,
  coefficient, percentage, and ratio, including tables and the filename header.
- Do not wrap a bare year inside running text unless it is a full date/period.
- Keep all whitespace and punctuation exactly.
- OUTPUT ONLY THE JSON ARRAY. Do NOT repeat the input or add prose.

EXAMPLE
Input:
```
# АКТ № ОРГ-0603

| **Дата** | 03.06.2026 |
```

Output:
[
  {"content":"# АКТ № ","wrap":"ОРГ-0603","type":"entity"},
  {"content":"\n\n| **Дата** | ","wrap":"03.06.2026","type":"entity"},
  {"content":" |\n","wrap":"","type":"EOF"}
]

NOW PROCESS THIS INPUT.

%%CONTENT%%
""".replace("%%DATE_FORMAT_EXAMPLES%%", DATE_FORMAT_EXAMPLES_TEXT)

METADATA_PROMPT = """\
You classify and normalize entities in an XML-like accounting document.
Every <ent id="..."> element is already wrapped. Do not add, remove, split, or
merge entities. Return one metadata object for every supplied ID and no others.

Metadata fields for every ID:
- type: amount | date | docid | contract | number | pct | ratio
- unit: RUB, RUB_K, USD, EUR, person_month, etc.; use "" when not applicable
- norm: numeric multiplier to the base unit as a string, normally "1"
- value: normalized non-date value as a string, or null if unresolved
- start/end: inclusive date bounds, or null for non-dates and unresolved dates

For date entities, value must be null. Return BOTH start and end, or BOTH null:
- exact date: YYYY-MM-DDT00:00:00 through the same day at T23:59:59
- month: first day at T00:00:00 through last day at T23:59:59
- quarter/year: first through last inclusive calendar day using the same times
- incomplete or ambiguous expressions: null/null; never invent missing parts

For non-date entities, start and end must be null. Keep identifiers as their
exact normalized string. Percentages omit the percent sign in value. Ratios use
a decimal value when determinable. Return ONLY the JSON object required by the
schema.

INPUT
%%MARKED%%
"""


def _build_prompt(filename: str, content: str) -> str:
    """Build request 1 without interpreting braces in document content."""
    header = "# " + " ".join(filename.splitlines())
    processed = f"{header}\n{content}"
    return TAG_PROMPT.replace("%%CONTENT%%", processed), processed


def _coerce_segments(data) -> list:
    if isinstance(data, dict) and "segments" in data:
        return data["segments"]
    if isinstance(data, list):
        return data
    raise ValueError(f"unexpected JSON shape: {type(data).__name__}")


def _validate_segments(doc: str, segments: list) -> list[str]:
    """Check the segment stream rebuilds *doc* exactly. Returns problems."""
    problems: list[str] = []
    rebuilt_parts: list[str] = []
    if not segments:
        return ["segment array is empty"]
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
        rebuilt_parts.extend((c, w))
        if i < len(segments) - 1:
            if not w or t != "entity":
                problems.append(f"segment {i}: non-final entry must be a non-empty entity")
        elif t != "EOF" or w != "":
            problems.append(f"last segment must be EOF with empty wrap, got type={t!r}")
    rebuilt = "".join(rebuilt_parts)
    if rebuilt.rstrip("\n") != doc.rstrip("\n"):
        k = 0
        limit = min(len(rebuilt), len(doc))
        while k < limit and rebuilt[k] == doc[k]:
            k += 1
        problems.append(
            f"rebuilt doc diverges at char {k}: "
            f"expected {doc[k:k + 40]!r}, got {rebuilt[k:k + 40]!r}"
        )
    return problems


def _segments_to_marked(segments: list) -> tuple[str, list[str]]:
    """Render deterministic temporary IDs for the metadata request."""
    out: list[str] = []
    ids: list[str] = []
    for seg in segments:
        out.append(seg.get("content", ""))
        if seg.get("type") != "EOF":
            entity_id = f"e{len(ids)}"
            ids.append(entity_id)
            out.append(f'<ent id="{entity_id}">{seg["wrap"]}</ent>')
    return "".join(out), ids


def _validate_timestamp(value: str, *, end: bool) -> None:
    expected = "23:59:59" if end else "00:00:00"
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid ISO date bound {value!r}") from exc
    if parsed.strftime("%H:%M:%S") != expected:
        raise ValueError(f"date bound must end at T{expected}: {value!r}")


def _validate_metadata(data, ids: list[str]) -> dict[str, dict]:
    """Enforce exact IDs and semantic date/value constraints after validation."""
    if not isinstance(data, dict) or set(data) != set(ids):
        raise ValueError("metadata IDs do not exactly match wrapped entity IDs")
    for entity_id in ids:
        item = data[entity_id]
        if not isinstance(item, dict):
            raise ValueError(f"metadata {entity_id} is not an object")
        if set(item) != {"type", "unit", "norm", "value", "start", "end"}:
            raise ValueError(f"metadata {entity_id} has missing or extra fields")
        if item.get("type") not in TAG_TYPES:
            raise ValueError(f"metadata {entity_id} has an invalid entity type")
        try:
            norm = Decimal(str(item.get("norm", "")))
        except InvalidOperation as exc:
            raise ValueError(f"metadata {entity_id} has a non-numeric norm") from exc
        if not norm.is_finite():
            raise ValueError(f"metadata {entity_id} has a non-finite norm")
        if not isinstance(item.get("unit"), str):
            raise ValueError(f"metadata {entity_id} unit must be a string")
        value, start, end = item.get("value"), item.get("start"), item.get("end")
        if value is not None and not isinstance(value, str):
            raise ValueError(f"metadata {entity_id} value must be a string or null")
        if item["type"] == "date":
            if value is not None:
                raise ValueError(f"date metadata {entity_id} must use start/end, not value")
            if (start is None) != (end is None):
                raise ValueError(f"date metadata {entity_id} has an incomplete interval")
            if start is not None:
                _validate_timestamp(start, end=False)
                _validate_timestamp(end, end=True)
                if start > end:
                    raise ValueError(f"date metadata {entity_id} has a reversed interval")
        elif start is not None or end is not None:
            raise ValueError(f"non-date metadata {entity_id} must not have date bounds")
    return data


def _segments_to_tagged(segments: list, metadata: dict[str, dict]) -> str:
    """Merge validated metadata into saved <ent> markup."""
    out: list[str] = []
    index = 0
    for seg in segments:
        out.append(seg.get("content", ""))
        if seg.get("type") == "EOF":
            continue
        item = metadata[f"e{index}"]
        index += 1
        attrs = [f't="{item["type"]}"']
        if item["unit"]:
            attrs.append(f'u="{escape(item["unit"], quote=True)}"')
        if item["norm"] != "1":
            attrs.append(f'n="{escape(item["norm"], quote=True)}"')
        if item["type"] == "date":
            attrs.append(f'start="{item["start"] or ""}"')
            attrs.append(f'end="{item["end"] or ""}"')
        else:
            attrs.append(f'value="{escape(item["value"] or "", quote=True)}"')
        out.append(f'<ent {" ".join(attrs)}>{seg["wrap"]}</ent>')
    return "".join(out)


def _is_transient(exc: Exception) -> bool:
    """Network/5xx errors are worth retrying; schema/validation errors are not."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return any(
        s in name or s in text
        for s in ("ssl", "timeout", "connection", "5", "rate limit", "temporar")
    )


def _tag_one(
        cfg: PipelineConfig,
        h8: str,
        original_name: str,
        on_token: Callable[[str], None] | None = None,
) -> tuple[str, int]:
    """Tag one file. Returns (tagged_text, retries_used)."""
    src = cfg.files_dir / f"{h8}.md"
    return tag_document(cfg, original_name, src.read_text(encoding="utf-8"), on_token)


def tag_document(
        cfg: PipelineConfig,
        filename: str,
        markdown: str,
        on_token: Callable[[str], None] | None = None,
) -> tuple[str, int]:
    """Extract entries in two calls without changing the preserved OCR source.

    Return (tagged_markdown_with_processed_filename, retries_used), or raise
    when extraction fails. The caller owns persistence and scheduling.
    """
    prompt, processed = _build_prompt(filename, markdown)
    last_err: Exception | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            data = call_llm_json(
                prompt,
                model=cfg.tag_model,
                schema=WRAPPING_SCHEMA,
                schema_name="entity_wrapping",
                timeout=cfg.request_timeout,
                on_token=on_token,
                reasoning=cfg.reasoning,
            )
            segments = _coerce_segments(data)
            problems = _validate_segments(processed, segments)
            if problems:
                raise ValueError("; ".join(problems))
            marked, ids = _segments_to_marked(segments)
            if ids:
                metadata = call_llm_json(
                    METADATA_PROMPT.replace("%%MARKED%%", marked),
                    model=cfg.tag_model,
                    schema=entity_metadata_schema(ids),
                    schema_name="entity_metadata",
                    timeout=cfg.request_timeout,
                    on_token=on_token,
                    reasoning=cfg.reasoning,
                )
                _validate_metadata(metadata, ids)
            else:
                metadata = {}
            return _segments_to_tagged(segments, metadata), attempt
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < cfg.max_retries and _is_transient(exc):
                continue
            if attempt < cfg.max_retries:
                prompt, processed = _build_prompt(filename, markdown)
                prompt += (
                    "\n\nYour previous answer was wrong. Return ONLY the wrapping JSON array. "
                    "Do not add metadata to this first request.\nValidation error:\n"
                    + str(last_err)[:1500]
                )
            else:
                break
    raise RuntimeError(f"tagging failed for {filename}: {last_err}")


def run_tag(
        cfg: PipelineConfig,
        mapping: dict,
        on_progress: TagCallback | None = None,
        on_token: Callable[[str, str], None] | None = None,
) -> dict[str, tuple[str, str]]:
    """Tag all non-duplicate files. Returns {h8: (role, tagged_text)}."""
    cfg.tagged_dir.mkdir(parents=True, exist_ok=True)
    todo = [
        (h8, entry["role"], Path(entry["original"]).name)
        for h8, entry in mapping.items()
        if not entry.get("duplicate_of")
    ]
    stats = TagStats(total=len(todo))
    results: dict[str, tuple[str, str]] = {}

    def _work(h8: str, role: str, name: str) -> tuple[str, str, str, int, str]:
        def _tok(tok: str, _h8: str = h8) -> None:
            if on_token:
                on_token(_h8, tok)

        tagged, retries = _tag_one(cfg, h8, name, on_token=_tok)
        return h8, role, tagged, retries, name

    with ThreadPoolExecutor(max_workers=cfg.tag_workers) as pool:
        futs = {pool.submit(_work, h8, role, name): h8 for h8, role, name in todo}
        for fut in as_completed(futs):
            h8 = futs[fut]
            try:
                h8, role, tagged, retries, name = fut.result()
                (cfg.tagged_dir / f"{h8}.md").write_text(tagged, encoding="utf-8")
                results[h8] = (role, tagged)
                ent_count = tagged.count("<ent ")
                prog = TagProgress(
                    file=h8, original=name, ok=True,
                    entities=ent_count, retries=retries,
                )
                stats.done += 1
                stats.ok += 1
                stats.entities += ent_count
                stats.retries += retries
            except Exception as exc:  # noqa: BLE001
                name = next(n for hh, _, n in todo if hh == h8)
                prog = TagProgress(
                    file=h8, original=name, ok=False,
                    error=str(exc)[:300],
                )
                stats.done += 1
                stats.failed += 1
            stats.per_file.append(prog)
            if on_progress:
                on_progress(prog, stats)
    return results
