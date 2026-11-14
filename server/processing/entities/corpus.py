"""Pure draft parsing and strict indexes of canonical reviewed Markdown."""

from dataclasses import asdict
from decimal import Decimal

from processing.common.corpus import ReviewedCorpus
from processing.common.documents import DocumentRevision
from processing.common.entities import Entity, EntityDraft
from processing.common.errors import ProcessingValidationError
from processing.common.registry import EntityRegistry, RegistryEntity

from .identifiers import read_identities
from .markup import validate_markup
from .values import parse_value, recorded_value


def parse_drafts(markdown: str) -> tuple[EntityDraft, ...]:
    """Read values and source spans; never create or infer an identity."""
    drafts = []
    for tag in validate_markup(markdown):
        attrs = tag.attributes
        raw = markdown[slice(*tag.body_span)]
        norm = Decimal(attrs.get("n", "1"))
        line_start = markdown.rfind("\n", 0, tag.span[0]) + 1
        drafts.append(EntityDraft(
            type=attrs["t"], raw=raw, unit=attrs.get("u", "RUB" if attrs["t"] == "amount" else ""),
            norm=norm, normalized_value=parse_value(attrs["t"], raw, norm, attrs.get("value")),
            span=tag.span, loc="filename" if markdown[line_start:line_start + 2] == "# " else "body",
            source_text=raw,
        ))
    return tuple(drafts)


def build_corpus(corpus: ReviewedCorpus) -> tuple[Entity, ...]:
    """Read finalized identities tied to actual document/report revisions."""
    # Validate report-wide numeric and mnemonic uniqueness, including across files.
    read_identities("\n".join(document.markdown for document in corpus.documents))
    entities = []
    for document in corpus.documents:
        validate_markup(document.markdown, require_identities=True)
        for identity, draft in zip(read_identities(document.markdown), parse_drafts(document.markdown), strict=True):
            entities.append(Entity(identity=identity, revision=document.revision, **asdict(draft)))
    return tuple(entities)


def build_registry(tagged_docs: dict[str, tuple[str, str]], *,
                   revisions: dict[str, DocumentRevision] | None = None) -> EntityRegistry:
    """Build the existing solver's derived view; every input tag must be finalized."""
    read_identities("\n".join(text for _, text in tagged_docs.values()))
    entries = []
    for key, (role, markdown) in sorted(tagged_docs.items()):
        validate_markup(markdown, require_identities=True)
        revision = revisions[key] if revisions is not None else None
        for identity, draft in zip(read_identities(markdown), parse_drafts(markdown), strict=True):
            entries.append(RegistryEntity(
                num=identity.num, mnemonic=identity.mnemonic, type=draft.type, raw=draft.raw,
                value=recorded_value(draft.normalized_value), unit=draft.unit, norm=str(draft.norm),
                file=key, role=role, span=draft.span, loc=draft.loc, source_text=draft.source_text,
                revision=revision, normalized_from=draft.normalized_from,
            ))
    return EntityRegistry(entries)
