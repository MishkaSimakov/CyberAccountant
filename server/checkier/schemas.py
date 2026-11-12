"""JSON schemas for LLM structured outputs (stages 1 and 5)."""

# Stage 1: entity tagging. The model returns a JSON array of segments that
# rebuild the document exactly; wrap entries carry the entity text and type.
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

TAG_ATTR_SCHEMA = {
    "t": TAG_TYPES,
    "u": None,  # free-form unit string
    "n": None,  # numeric multiplier, validated separately
}

# Stage 5: relations between entities.
RELATIONS_SCHEMA = {
    "type": "array",
    "items": {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "equation": {"type": "string"},
                    "tag": {
                        "type": "string",
                        "enum": ["contract", "period", "agg", "ref"],
                    },
                },
                "required": ["equation"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "evidence": {"type": "string"},
                    "ids_related": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "verdict": {"const": "false"},
                },
                "required": ["evidence", "ids_related", "verdict"],
                "additionalProperties": False,
            },
        ]
    },
}
