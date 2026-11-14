"""Standalone orchestration around the in-memory entity processing package."""

import json

from processing.entities.corpus import build_registry
from processing.entities.identifiers import assign_identities, read_identities
from processing.entities.markup import parse_entity_tags
from processing.entities.normalization import normalize_document
from processing.entities.options import EntityOptions


def run_registry(cfg, tagged_docs):
    # Standalone runs own an isolated corpus, so their caller can reserve a
    # local range without PostgreSQL. Stored identities always take precedence.
    existing = read_identities("\n".join(text for _, text in tagged_docs.values()))
    next_id = max((identity.num for identity in existing), default=-1) + 1
    for key, (role, text) in sorted(tagged_docs.items()):
        count = len(parse_entity_tags(text)) - len(read_identities(text))
        canonical = assign_identities(text, range(next_id, next_id + count))
        next_id += count
        tagged_docs[key] = role, canonical
        cfg.tagged_dir.mkdir(parents=True, exist_ok=True)
        (cfg.tagged_dir / f"{key}.md").write_text(canonical, encoding="utf-8")
    registry = build_registry(tagged_docs)
    cfg.entities_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.entities_path.write_text(json.dumps(registry.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return registry


def normalize_dates(cfg, tagged_docs, *, client, logger, on_token=None):
    previous = build_registry(tagged_docs)
    for key, (role, text) in tagged_docs.items():
        tagged_docs[key] = role, normalize_document(EntityOptions.from_settings(cfg), text, client=client,
                                                   logger=logger, on_token=on_token)
    registry = run_registry(cfg, tagged_docs)
    old = {entity.num: entity.value for entity in previous.entities}
    return registry, [entity for entity in registry.entities if old[entity.num] != entity.value]
