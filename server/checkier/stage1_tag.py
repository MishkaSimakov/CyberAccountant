"""Stage 1: LLM-based entity tagging.

The model returns a JSON array of alternating segments that reconstruct the
document exactly::

    [
      {"content": "# АКТ № ", "wrap": "ОРГ-0603", "type": "contract"},
      {"content": "\n| **Дата** | ", "wrap": "03.06.2026", "type": "date"},
      {"content": " |\n| **Договор** | ", "wrap": "24/03/15-01", "type": "contract"},
      {"content": " |", "wrap": "", "type": "EOF"}
    ]

Concatenating every ``content`` then ``wrap`` in array order yields the
original document; every non-EOF entry with a non-empty ``wrap`` is an
entity. ``type`` is one of amount | date | docid | contract | number |
pct | ratio.

Optional attributes ``unit`` (u) and ``norm`` (n) ride on wrap entries::

    {"content": "Сумма: ", "wrap": "245 000,00", "type": "amount",
     "unit": "RUB", "norm": "1"}

A progress callback receives :class:`TagProgress` after every file.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from checkier.config import PipelineConfig
from checkier.llm import call_llm_json
from checkier.schemas import TAGGING_SCHEMA


# ---------------------------------------------------------------------------
# Progress reporting (swap these callbacks for a frontend later)
# ---------------------------------------------------------------------------


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

TAG_PROMPT = f"""\
You extract numerical entities from accounting documents.

Entity types:
- amount    - monetary sums: "245 000,00 руб.", "10 862 418,06"
- date      - calendar dates: "28.01.2026", "Январь 2026", "16 августа 2025 г."
- docid     - document / registry identifiers: "ВЕД-ЗП-2026-01", "УПР-55", "КЛ-31", "3/1"
- contract  - contract numbers: "24/03/15-01"
- number    - any other plain number (counts, codes, coefficients, table cells)
- pct       - percentages: "30%"
- ratio     - dimensionless ratios / fractions

DATE / PERIOD FORMATS
Wrap each complete expression as ONE date entity:
{DATE_FORMAT_EXAMPLES_TEXT}

A quoted day + month + year + optional "г." is ONE date, never a separate
number and month/year entity. For a range such as 01.05.2026–10.05.2026,
wrap the two dates separately and leave the dash outside the tags.

Optional attributes for amount/date/docid entries:
- unit  - RUB (default), RUB_K (thousand rubles), USD, EUR, person_month, ...
- norm  - multiplier to base unit (1000 for RUB_K), default 1

TASK
Read the document at the bottom and output a JSON array of segments that
rebuilds it exactly.

REQUIRED OUTPUT FORMAT (no other text, no markdown fence, no commentary):
[{{"content":"...","wrap":"...","type":"..."}}, ..., {{"content":"...","wrap":"","type":"EOF"}}]

Each WRAP entry: {{"content": "text before the entity", "wrap": "ENTITY TEXT",
"type": "TYPE"}} with optional "unit" / "norm". The final entry is
{{"content": "remaining text", "wrap": "", "type": "EOF"}}.

Concatenating every "content" then its "wrap" (in array order) MUST give back
the document character-for-character.

Rules:
- Tag EVERY occurrence, including inside tables, headers and the filename title line.
- Do not tag a bare year inside running text unless it is a full date or period label.
- Keep all whitespace and punctuation exactly.
- OUTPUT ONLY THE JSON ARRAY. Do NOT repeat the document. Do NOT add prose.

EXAMPLE
Document:
```
# АКТ № ОРГ-0603

| | |
|---|---|
| **Дата** | 03.06.2026 |
| **Договор** | 24/03/15-01 |
```

Output:
```json
[
  {{"content": "# АКТ № ", "wrap": "ОРГ-0603", "type": "docid"}},
  {{"content": "\\n\\n| | |\\n|---|---|\\n| **Дата** | ", "wrap": "03.06.2026", "type": "date"}},
  {{"content": " |\\n| **Договор** | ", "wrap": "24/03/15-01", "type": "contract"}},
  {{"content": " |\\n", "wrap": "", "type": "EOF"}}
]
```

ANOTHER EXAMPLE (amounts with units)
Document:
```
Стоимость: 245 000,00 руб. за январь 2026 года.
```
Output:
```json
[
  {{"content": "Стоимость: ", "wrap": "245 000,00", "type": "amount", "unit": "RUB", "norm": "1"}},
  {{"content": " руб. за ", "wrap": "январь 2026", "type": "date"}},
  {{"content": " года.\\n", "wrap": "", "type": "EOF"}}
]
```

NOW PROCESS THIS DOCUMENT.

Filename: %%FILENAME%%

Document:
```
%%CONTENT%%
```
"""


def _build_prompt(filename: str, content: str) -> str:
    """Substitute placeholders without interpreting JSON braces in the template."""
    return TAG_PROMPT.replace("%%FILENAME%%", filename).replace("%%CONTENT%%", content)


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
    # Trailing-whitespace-only differences are insignificant (model may add a
    # final newline). Compare with trailing newlines stripped on both sides.
    if rebuilt.rstrip("\n") != doc.rstrip("\n"):
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
            attrs += f' u="{seg["unit"]}"'
        if seg.get("norm") and seg["norm"] not in (None, "", "1"):
            attrs += f' n="{seg["norm"]}"'
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
    """Extract entries from one document without filesystem access.

    Return (tagged_markdown, retries_used), or raise when extraction fails.
    The caller owns persistence and scheduling.
    """
    content = markdown
    prompt = _build_prompt(filename, content)
    last_err: Exception | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            data = call_llm_json(
                prompt,
                model=cfg.tag_model,
                schema=TAGGING_SCHEMA,
                schema_name="tagged_document",
                timeout=cfg.request_timeout,
                on_token=on_token,
                reasoning=cfg.reasoning,
            )
            segments = _coerce_segments(data)
            problems = _validate_segments(content, segments)
            if problems:
                raise ValueError("; ".join(problems))
            return _segments_to_tagged(segments), attempt
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < cfg.max_retries and _is_transient(exc):
                continue
            if attempt < cfg.max_retries:
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


def run_tag(
        cfg: PipelineConfig,
        mapping: dict,
        on_progress: TagCallback | None = None,
        on_token: Callable[[str, str], None] | None = None,
) -> dict[str, tuple[str, str]]:
    """Tag all non-duplicate files. Returns {h8: (role, tagged_text)}.

    on_progress: called after each file finishes (TagProgress, TagStats).
    on_token: called with (file, token) for every streamed LLM token so the
    caller can show live output while a file is being generated.
    """
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
