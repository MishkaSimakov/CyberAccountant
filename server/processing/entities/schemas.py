"""Structured model output schemas for entity processing."""

_SEGMENT_ITEM = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "wrap": {"type": "string"},
        "type": {
            "type": "string",
            "enum": [
                "amount", "date", "docid", "contract",
                "number", "pct", "ratio", "EOF",
            ],
        },
        "unit": {"type": "string"},
        "norm": {"type": "string"},
    },
    "required": ["content", "wrap", "type"],
    "additionalProperties": False,
}

TAGGING_SCHEMA = {
    "type": "array",
    "description": (
        "Alternating segments that rebuild the document. Each entry is "
        '{"content": "text before", "wrap": "ENTITY TEXT", "type": "TYPE"} '
        'with optional "unit"/"norm"; the last entry has "type": "EOF", "wrap": "".'
    ),
    "items": _SEGMENT_ITEM,
}

TAG_TYPES = ("amount", "date", "docid", "contract", "number", "pct", "ratio")

# Stage 2b: date normalization. One entry per unparsed period expression;
# "normalized" is DD.MM.YYYY (first day of the period), or null if unresolved.
DATE_NORM_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "raw": {"type": "string"},
            "normalized": {
                "type": ["string", "null"],
                "description": "DD.MM.YYYY — first day of the period; null if the date cannot be determined",
            },
        },
        "required": ["raw", "normalized"],
        "additionalProperties": False,
    },
}

