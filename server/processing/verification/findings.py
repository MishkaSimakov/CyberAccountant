"""Document findings derived from direct checks and canonical document metadata."""

from .constraints import Relation


def document_findings(mapping: dict, relations: list[Relation], dangling: list[str]) -> dict:
    duplicates = {key: entry["duplicate_of"] for key, entry in mapping.items() if entry.get("duplicate_of")}
    out_of_scope = {}
    for relation in relations:
        if relation.tag != "scope" or relation.status != "false":
            continue
        for file in relation.involved_files:
            if mapping.get(file, {}).get("role") == "data":
                reasons = out_of_scope.setdefault(file, [])
                if relation.equation not in reasons:
                    reasons.append(relation.equation)
    return dict(duplicates=duplicates, out_of_scope=out_of_scope, dangling=dangling)
