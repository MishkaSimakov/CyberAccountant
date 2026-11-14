"""Structured output schemas for requirement generation."""

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
                    "verdict": {"enum": ["false", "unverifiable"]},
                },
                "required": ["evidence", "ids_related", "verdict"],
                "additionalProperties": False,
            },
        ]
    },
}
