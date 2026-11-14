"""Explicit identity assignment for tests starting from extraction drafts."""

from decimal import Decimal
from html import escape

from processing.common.registry import EntityRegistry as CanonicalRegistry, RegistryEntity
from processing.entities.corpus import build_registry, parse_drafts
from processing.entities.identifiers import assign_identities, mnemonic_for_id, read_identities
from processing.common.markup import parse_entity_tags
from processing.entities.values import recorded_value


def canonical_documents(documents):
    """A test owns its isolated numeric reservation, outside corpus parsing."""
    existing = read_identities("\n".join(text for _, text in documents.values()))
    next_id = max((identity.num for identity in existing), default=-1) + 1
    canonical = {}
    for key, (role, text) in sorted(documents.items()):
        count = len(parse_entity_tags(text)) - len(read_identities(text))
        canonical[key] = role, assign_identities(text, range(next_id, next_id + count))
        next_id += count
    return canonical


def registry_from_drafts(documents, seed=0):
    canonical = canonical_documents(documents)
    # Source-span tests must inspect the same finalized input the parser reads.
    documents.update(canonical)
    return build_registry(canonical)


class EntityRegistry(CanonicalRegistry):
    """Small fixture builder for solver tests that directly adjust recorded facts."""

    def __init__(self, seed=0):
        super().__init__()

    def add(self, *, type_, raw, unit, norm, file, role, span, loc="body", source_text=""):
        markdown = f'<ent t="{type_}" u="{escape(unit, quote=True)}" n="{norm}">{raw}</ent>'
        draft, = parse_drafts(markdown)
        num = len(self.entities)
        entry = RegistryEntity(num=num, mnemonic=mnemonic_for_id(num), type=type_, raw=raw,
                               value=recorded_value(draft.normalized_value), unit=unit, norm=norm,
                               file=file, role=role, span=span, loc=loc, source_text=source_text)
        self.entities.append(entry)
        return entry


def finalize_drafts(text, first_id=0):
    count = len(parse_entity_tags(text))
    return assign_identities(text, range(first_id, first_id + count))
