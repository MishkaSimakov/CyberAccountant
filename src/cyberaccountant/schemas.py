"""JSON schemas for LLM structured outputs (stages 1 and 5)."""

# Stage 1: entity tagging. The model returns the full document text with
# <ent ...> tags wrapped around every entity. Using a schema with a single
# "document" string field keeps output JSON-valid even though the payload is
# markdown.
TAGGING_SCHEMA = {
    "type": "object",
    "properties": {
        "document": {
            "type": "string",
            "description": (
                "Full document text with entities wrapped in "
                '<ent t="..." u="..." n="...">raw text</ent> tags. '
                "No other changes to the text."
            ),
        }
    },
    "required": ["document"],
    "additionalProperties": False,
}

TAG_TYPES = ("amount", "date", "docid", "contract", "number", "pct", "ratio")

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
