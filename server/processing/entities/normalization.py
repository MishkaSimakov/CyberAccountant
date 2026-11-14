"""In-memory normalization without ID allocation, registry mutation, or files."""

from datetime import date
from html import escape

from processing.common.entities import UnknownValue
from processing.common.llm import LoggerLike, ModelClient, ModelRequestOptions, TokenCallback

from .corpus import parse_drafts
from .markup import validate_markup
from .options import EntityOptions
from .prompts import date_prompt
from .schemas import DATE_NORM_SCHEMA
from .values import parse_date


def normalize_document(options: EntityOptions, tagged: str, *, client: ModelClient,
                       logger: LoggerLike, on_token: TokenCallback | None = None) -> str:
    """Fill missing normalized values and preserve explicit reviewed values."""
    tags = validate_markup(tagged)
    drafts = parse_drafts(tagged)
    candidates = sorted({draft.raw for tag, draft in zip(tags, drafts)
                         if "value" not in tag.attributes and draft.type == "date"
                         and draft.normalized_value is UnknownValue.UNKNOWN})
    dates = {}
    if candidates:
        data = client.call_llm_json(
            date_prompt(candidates),
            ModelRequestOptions(model=options.tag_model, schema=DATE_NORM_SCHEMA,
                                schema_name="date_normalizations", timeout=options.request_timeout,
                                max_retries=options.max_retries, reasoning=options.reasoning),
            logger=logger, on_token=on_token,
        )
        entries = data if isinstance(data, list) else data.get("normalizations", [])
        for entry in entries:
            if isinstance(entry, dict) and entry.get("raw") in candidates and entry.get("normalized"):
                value = parse_date(str(entry["normalized"]))
                if value is not None:
                    dates[entry["raw"]] = value
    pieces = []
    end = 0
    for tag, draft in zip(tags, drafts):
        if "value" in tag.attributes:
            continue
        value = dates.get(draft.raw, draft.normalized_value) if draft.type == "date" else draft.normalized_value
        text = "" if value is UnknownValue.UNKNOWN else value.isoformat() if isinstance(value, date) else str(value)
        insertion = tag.body_span[0] - 1
        pieces.extend((tagged[end:insertion], f' value="{escape(text, quote=True)}"'))
        end = insertion
    pieces.append(tagged[end:])
    return "".join(pieces)
