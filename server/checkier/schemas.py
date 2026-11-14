"""JSON schemas for LLM structured outputs (stages 1 and 5)."""

# Stage 1a: wrapping only. The model identifies entity spans but supplies no
# entity metadata. The final structural EOF entry has an empty wrap.
_SEGMENT_ITEM = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "wrap": {"type": "string"},
        "type": {"type": "string", "enum": ["entity", "EOF"]},
    },
    "required": ["content", "wrap", "type"],
    "additionalProperties": False,
}

WRAPPING_SCHEMA = {
    "type": "array",
    "description": (
        "Alternating segments that rebuild the filename header and document. "
        'Entity entries are {"content":"text before","wrap":"ENTITY TEXT",'
        '"type":"entity"}; the last entry has "type":"EOF" and an empty wrap.'
    ),
    "items": _SEGMENT_ITEM,
}

# Backwards-compatible import for callers that used the old combined schema.
TAGGING_SCHEMA = WRAPPING_SCHEMA

TAG_TYPES = ("amount", "date", "docid", "contract", "number", "pct", "ratio")


def entity_metadata_schema(ids: list[str]) -> dict:
    """Build an exact, closed metadata schema for the IDs in one document."""
    nullable_string = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            entity_id: {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(TAG_TYPES)},
                    "unit": {"type": "string"},
                    "norm": {"type": "string"},
                    "value": nullable_string,
                    "start": {
                        **nullable_string,
                        "pattern": r"^\d{4}-\d{2}-\d{2}T00:00:00$",
                    },
                    "end": {
                        **nullable_string,
                        "pattern": r"^\d{4}-\d{2}-\d{2}T23:59:59$",
                    },
                },
                "required": ["type", "unit", "norm", "value", "start", "end"],
                "additionalProperties": False,
            }
            for entity_id in ids
        },
        "required": ids,
        "additionalProperties": False,
    }


# Stage 2b: fallback date normalization. Both inclusive calendar-day bounds
# are DD.MM.YYYY, or both are null when the expression is unresolved.
DATE_NORM_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "raw": {"type": "string"},
            "start": {"type": ["string", "null"]},
            "end": {"type": ["string", "null"]},
        },
        "required": ["raw", "start", "end"],
        "additionalProperties": False,
    },
}

TAG_ATTR_SCHEMA = {
    "t": TAG_TYPES,
    "u": None,  # free-form unit string
    "n": None,  # numeric multiplier, validated separately
    "start": None,
    "end": None,
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
                    "text": {"type": "string"},
                    "tag": {
                        "type": "string",
                        "enum": ["scope", "agg", "ref"],
                    },
                },
                "required": ["equation"],
                "additionalProperties": False,
            }
        ]
    },
}
