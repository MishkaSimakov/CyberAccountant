"""Temporary views for prompts, derived without rewriting canonical Markdown."""

from html import escape

from .errors import ProcessingValidationError
from .markup import parse_entity_tags


def project_markdown(markdown: str, attributes: tuple[str, ...]) -> str:
    """Keep numeric canonical references and only fields requested by a prompt."""
    if "id" not in attributes:
        raise ValueError("Prompt projections must retain canonical id references.")
    pieces = []
    end = 0
    for tag in parse_entity_tags(markdown):
        if "id" not in tag.attributes or "mnemonic" not in tag.attributes:
            raise ProcessingValidationError("Prompt projections require canonical identities.")
        fields = " ".join(f'{name}="{escape(tag.attributes[name], quote=True)}"'
                          for name in attributes if name in tag.attributes)
        pieces.extend((markdown[end:tag.span[0]], f'<ent {fields}>{markdown[slice(*tag.body_span)]}</ent>'))
        end = tag.span[1]
    pieces.append(markdown[end:])
    return "".join(pieces)
